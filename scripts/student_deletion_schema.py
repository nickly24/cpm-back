#!/usr/bin/env python3
"""Explicit schema preflight/install. Never imports repository connection defaults.

Use preflight -> base -> homework -> apply -> check, with writers stopped during
all mutation stages. MySQL DDL commits implicitly; this is not an atomic rollback.
No mode deletes student/application content or starts a deletion worker.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def statements(sql):
    delimiter, buffer = ';', []
    for line in sql.splitlines():
        if not line.strip() or line.lstrip().startswith('--'):
            continue
        if line.upper().startswith('DELIMITER '):
            delimiter = line.split()[1]
            continue
        buffer.append(line)
        if line.rstrip().endswith(delimiter):
            yield '\n'.join(buffer).rstrip()[:-len(delimiter)].strip()
            buffer = []
    if buffer:
        raise ValueError('unterminated_migration_statement')


NEW_TABLES = {
    'student_deletion_jobs', 'student_deletion_barriers', 'student_deletion_guard_state',
    'homework_service_capabilities', 'homework_object_ownership', 'homework_student_purges',
    'homework_student_purge_objects', 'homework_s3_delete_receipts',
}
NEW_COLUMNS = {
    'homework_file_jobs': {'processed_output_key', 'upload_expires_at'},
    'homework_s3_delete_queue': {'lease_owner', 'lease_expires_at', 'completed_at', 'generation'},
}


def prepared_metadata(columns, engines):
    expected_path = ROOT / 'migrations/student-deletion-schema-audit-v2/expected-post-activation.json'
    expected = json.loads(expected_path.read_text(encoding='utf-8'))
    result = {name: dict(fields) for name, fields in columns.items()}
    result_engines = dict(engines)
    for row in expected['columns']:
        table, column = row['TABLE_NAME'], row['COLUMN_NAME']
        if table in NEW_TABLES or (table in result and column in NEW_COLUMNS.get(table, set())):
            result.setdefault(table, {}).setdefault(column, row['DATA_TYPE'])
            result_engines.setdefault(table, 'InnoDB')
    return result, result_engines


def preflight(connection, fence):
    """Read-only structural checks, including before new control tables exist."""
    columns, references, engines = fence._read_schema(connection)
    required = {
        'students': {'id', 'full_name', 'group_id'}, 'auth_users': {'role', 'ref_id', 'username'},
        'groups': {'id', 'name'}, 'homework_sessions': {'student_id', 'homework_id'},
        'exam_sessions': {'student_id', 'exam_id'}, 'test_sessions': {'student_id', 'test_id'},
        'homework_submissions': {'id', 'student_id', 'homework_id'},
        'homework_submission_files': {'id', 'submission_id', 'object_key'},
        'homework_file_jobs': {'id', 'student_id', 'submission_id', 'staging_key'},
        'homework_s3_delete_queue': {'object_key', 'status', 'attempts', 'available_at'},
    }
    for table, fields in required.items():
        if not fields <= set(columns.get(table, {})):
            raise ValueError('missing_legacy_prerequisite:' + table)
    # Only known additive migration metadata is simulated. Existing columns are
    # never overwritten, and missing OLD prerequisites still fail above/guards.
    simulated, simulated_engines = prepared_metadata(columns, engines)
    protected = fence._validate_ownership_schema(simulated, references, simulated_engines)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute('SELECT VERSION() AS version')
        version = str(cursor.fetchone()['version'])
        if not version.startswith('8.') or 'mariadb' in version.lower():
            raise ValueError('mysql8_required')
        cursor.execute('''SELECT COUNT(*) AS count FROM (
            SELECT f.object_key FROM homework_submission_files f JOIN homework_submissions s ON s.id=f.submission_id
            GROUP BY f.object_key HAVING COUNT(DISTINCT s.student_id)>1) conflicts''')
        if cursor.fetchone()['count']:
            raise ValueError('shared_homework_object_ownership_requires_review')
        cursor.execute('''SELECT COUNT(*) AS count FROM homework_submission_files f
            LEFT JOIN homework_submissions s ON s.id=f.submission_id WHERE s.id IS NULL''')
        if cursor.fetchone()['count']:
            raise ValueError('orphan_homework_file_metadata_requires_review')
        cursor.execute('''SELECT COUNT(*) AS count FROM homework_file_jobs j
            LEFT JOIN homework_submissions s ON s.id=j.submission_id
            WHERE s.id IS NULL OR s.student_id<>j.student_id''')
        if cursor.fetchone()['count']:
            raise ValueError('homework_job_owner_mismatch_requires_review')
        print('PREFLIGHT_OK: MySQL8; known ownership schema; file ownership consistent;', len(protected), 'known tables')
        print('Read-only schema check only: separately verify backup/restore, privileges, actual FK/legacy contents, stopped writers, Mongo and storage policy.')
    finally:
        cursor.close()
    connection.rollback()


def apply_sql(connection, path):
    cursor = connection.cursor()
    try:
        for statement in statements(path.read_text(encoding='utf-8')):
            cursor.execute(statement)
            if cursor.with_rows:
                cursor.fetchall()
            while cursor.nextset():
                if cursor.with_rows:
                    cursor.fetchall()
        connection.commit()
    finally:
        cursor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preflight', 'check', 'base', 'homework', 'apply'])
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=3306)
    parser.add_argument('--socket')
    parser.add_argument('--allow-remote', action='store_true')
    parser.add_argument('--ssl-ca', type=Path, help='Trusted provider CA file for verified remote TLS')
    parser.add_argument('--homework-migration', type=Path, default=ROOT.parent / 'cpm-homework-service/migrations/002_student_purges.sql')
    parser.add_argument('--confirm')
    args = parser.parse_args(argv)
    if args.host not in {'127.0.0.1', 'localhost', '::1'} and not args.allow_remote:
        parser.error('Remote access requires explicit --allow-remote; no repository defaults are used.')
    if args.host not in {'127.0.0.1', 'localhost', '::1'} and not args.socket and not args.ssl_ca:
        parser.error('Remote database requires --ssl-ca and verified TLS; alternatively use an existing secure local tunnel.')
    if args.mode in {'base', 'homework', 'apply'} and args.confirm != 'APPLY_STUDENT_DELETION_SCHEMA':
        parser.error('Mutation requires --confirm APPLY_STUDENT_DELETION_SCHEMA with all service writers stopped.')
    if 'MYSQL_PASSWORD' not in os.environ:
        parser.error('Set MYSQL_PASSWORD explicitly (an empty password is only for an isolated local fixture).')
    import mysql.connector
    spec = importlib.util.spec_from_file_location('deletion_fence_schema', ROOT / 'cpm_back/services/student_deletion/fence.py')
    fence = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fence)
    connection = None
    try:
        tls = {'ssl_ca': str(args.ssl_ca), 'ssl_verify_cert': True, 'ssl_verify_identity': True} if args.ssl_ca else {}
        connection = mysql.connector.connect(host=args.host, port=args.port, unix_socket=args.socket,
            user=args.user, password=os.environ['MYSQL_PASSWORD'], database=args.database,
            autocommit=False, connection_timeout=10, **tls)
        if args.mode == 'preflight':
            preflight(connection, fence)
            return 0
        if args.mode == 'base':
            preflight(connection, fence)
            apply_sql(connection, ROOT / 'migrations/030_student_deletion_jobs.sql')
            print('BASE_OK:030 installed. Keep writers stopped; next homework, then apply and check.')
            return 0
        if args.mode == 'homework':
            preflight(connection, fence)
            cursor = connection.cursor()
            try:
                cursor.execute('SELECT 1 FROM student_deletion_barriers LIMIT 0')
                cursor.fetchall()
            finally:
                cursor.close()
            apply_sql(connection, args.homework_migration)
            print('HOMEWORK_OK:002 installed. Keep writers stopped; next apply and check.')
            return 0
        if args.mode == 'apply':
            preflight(connection, fence)
            cursor = connection.cursor()
            try:
                cursor.execute('SELECT object_key FROM homework_object_ownership LIMIT 0')
                cursor.fetchall()
                cursor.execute('SELECT generation FROM homework_s3_delete_queue LIMIT 0')
                cursor.fetchall()
            finally:
                cursor.close()
                connection.rollback()
            fence.apply_write_barrier_migration(connection)
        result = fence.verify_write_barrier_schema(connection)
        print('CHECK_OK: Student write guards verified:', len(result['tables']), 'tables; version', result['guard_version'])
        print('Keep new deletion and storage attestation disabled until coordinated code rollout and remaining runtime gates are verified.')
    except Exception as error:
        # Do not echo connector host/user/query diagnostics or credentials.
        code = str(error) if isinstance(error, (ValueError, fence.StudentFenceUnavailable)) else type(error).__name__
        print('SCHEMA_FAILED:', code, 'errno=' + str(getattr(error, 'errno', 'n/a')), file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
