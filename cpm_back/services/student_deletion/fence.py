"""Fail-closed write fencing for durable student erasure.

Lock order is advisory fence -> domain row locks. Never enter a new fence with
an already-started domain transaction. The lock is held until commits and Mongo
writes finish; reentrant calls reuse its connection. SQL triggers additionally
serialize uncooperative SQL writers with barrier insertion through an InnoDB
singleton, independent of transaction isolation/snapshots.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
import re
import threading

from flask import current_app, g, has_app_context, jsonify, request

FENCE_KEY = 'cpm:student-write-fence'
_state = ContextVar('student_write_fence', default=None)
_barrier_schema_seen = False


class StudentWriteBlocked(RuntimeError):
    """A pending or completed deletion permanently revoked this identity."""


class StudentFenceUnavailable(RuntimeError):
    """Safety schema, connection, or cross-process lock is unavailable."""


def enabled():
    value = current_app.config.get('STUDENT_DELETION_ENABLED', False) if has_app_context() else os.environ.get('STUDENT_DELETION_ENABLED', '')
    return value is True or str(value).lower() in ('1', 'true', 'yes', 'on')



def protection_active(connection=None):
    """Durable tombstones survive disabling acceptance of new deletion jobs.

    Only a verified missing table before any observed installation permits
    legacy mode. Cache positive schema existence, never absence/errors.
    """
    global _barrier_schema_seen
    if enabled() or _barrier_schema_seen:
        return True
    own = connection is None
    cursor = None
    try:
        connection = connection or _connection()
        cursor = connection.cursor()
        cursor.execute('SELECT 1 FROM student_deletion_barriers LIMIT 0')
        _barrier_schema_seen = True
        return True
    except Exception as error:
        if getattr(error, 'errno', None) == 1146 and not _barrier_schema_seen:
            return False
        raise StudentFenceUnavailable('student_write_guard_unavailable') from error
    finally:
        if cursor is not None:
            cursor.close()
        if own and connection is not None:
            _close(connection)


def _connection():
    from cpm_back.db.mysql_pool import get_db_connection
    return get_db_connection()


def _close(connection):
    from cpm_back.db.mysql_pool import close_db_connection
    close_db_connection(connection)


def _scalar(cursor):
    row = cursor.fetchone()
    return next(iter(row.values())) if isinstance(row, dict) else (row[0] if row else None)


def _student_id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise StudentWriteBlocked('invalid_student_id')
    ident = int(value)
    if ident > 9223372036854775807:
        raise StudentWriteBlocked('invalid_student_id')
    return ident


@contextmanager
def mutation_fence(student_ids=(), connection=None, *, force=False, verify_schema=True):
    """Drain current writers and serialize against deletion barrier commits.

    `force=True` is for the deletion control plane and migration verifier. A
    caller-owned connection must be idle, and is never committed/closed here.
    No bypass permits inserts or updates to a revoked student's personal data.
    """
    if not force and not protection_active():
        yield connection
        return
    state = _state.get()
    if state is not None and state['thread'] == threading.get_ident():
        for student_id in student_ids:
            _check_writable(state['connection'], student_id)
        yield state['connection']
        return
    own = connection is None
    try:
        connection = connection or _connection()
    except Exception as error:
        raise StudentFenceUnavailable('student_write_guard_unavailable') from error
    cursor, token, acquired, entered = None, None, False, False
    try:
        if getattr(connection, 'in_transaction', False):
            raise StudentFenceUnavailable('fence_requires_idle_connection')
        cursor = connection.cursor()
        timeout = current_app.config.get('STUDENT_DELETION_FENCE_TIMEOUT', 10) if has_app_context() else 10
        cursor.execute('SELECT GET_LOCK(%s, %s)', (FENCE_KEY, int(timeout)))
        acquired = _scalar(cursor) == 1
        if not acquired:
            raise StudentFenceUnavailable('student_write_fence_busy')
        # Dedicated lock connection stays outside domain transactions. Ending
        # read snapshots keeps subsequent barrier checks current.
        connection.rollback()
        if verify_schema:
            verify_write_barrier_schema(connection)
            connection.rollback()
        token = _state.set({'connection': connection, 'thread': threading.get_ident()})
        for student_id in student_ids:
            _check_writable(connection, student_id)
        entered = True
        yield connection
    except Exception as error:
        if not entered and not isinstance(error, (StudentFenceUnavailable, StudentWriteBlocked)):
            raise StudentFenceUnavailable('student_write_guard_unavailable') from error
        raise
    finally:
        if token is not None:
            _state.reset(token)
        try:
            if acquired:
                if own:
                    connection.rollback()
                cursor.execute('SELECT RELEASE_LOCK(%s)', (FENCE_KEY,))
                cursor.fetchone()
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                if own:
                    _close(connection)


def _check_writable(connection, student_id):
    ident = _student_id(student_id)
    cursor = connection.cursor(dictionary=True)
    try:
        # Locking reads ignore a pre-existing REPEATABLE READ snapshot. Domain
        # writes never use this dedicated connection while the request is live.
        cursor.execute('SELECT student_id FROM student_deletion_barriers WHERE student_id=%s LOCK IN SHARE MODE', (ident,))
        if cursor.fetchone():
            raise StudentWriteBlocked('student_deletion_pending')
        cursor.execute('SELECT id FROM students WHERE id=%s LOCK IN SHARE MODE', (ident,))
        if not cursor.fetchone():
            raise StudentWriteBlocked('student_not_found')
    except StudentWriteBlocked:
        raise
    except Exception as error:
        raise StudentFenceUnavailable('student_write_guard_unavailable') from error
    finally:
        cursor.close()
        # The fence, not row locks on this connection, protects the check/use
        # interval. Avoid retaining row locks that conflict with domain writes.
        connection.rollback()


def check_student_writable(student_id):
    with mutation_fence((student_id,)):
        pass


def load_live_student(student_id):
    """Every student JWT/login resolves live account + profile + tombstone.

    Account/profile checks always apply. Once deletion is enabled a missing
    barrier schema is an authentication failure, never legacy-token fallback.
    """
    connection = None
    try:
        ident = _student_id(student_id)
        connection = _connection()
        cursor = connection.cursor(dictionary=True)
        try:
            barrier_sql = (' AND NOT EXISTS (SELECT 1 FROM student_deletion_barriers b WHERE b.student_id=s.id)' if protection_active(connection) else '')
            cursor.execute(
                "SELECT s.id,s.full_name,s.group_id FROM students s WHERE s.id=%s "
                "AND EXISTS (SELECT 1 FROM auth_users a WHERE a.role='student' AND a.ref_id=s.id)" + barrier_sql,
                (ident,),
            )
            row = cursor.fetchone()
            return dict(row, role='student') if row else None
        finally:
            cursor.close()
    except Exception:
        # Do not leak connector diagnostics, credentials or profile data.
        return None
    finally:
        if connection is not None:
            _close(connection)


def fenced_mutation(function):
    """Wrap a complete background write unit, before opening its SQL tx."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with mutation_fence():
            return function(*args, **kwargs)
    return guarded


_ID_KEYS = {'student_id', 'studentId', 'studentID', 'existing_student_id', 'target_student_id', 'privilegeBaselineStudentId'}
_IDS_KEYS = {'student_ids', 'studentIds', 'student_ids_created'}
_OWNERSHIP_COLUMN_KEYS = {key.lower().replace('_', '') for key in _ID_KEYS | _IDS_KEYS}


def student_ids_in(value):
    """Extract only explicit ownership fields, never generic staff/exam IDs."""
    ids = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_KEYS and item is not None:
                if isinstance(item, dict):
                    # Mongo filters may enumerate equivalent numeric/string IDs.
                    for nested in item.get('$in', []):
                        ids.add(_student_id(nested))
                    if '$eq' in item:
                        ids.add(_student_id(item['$eq']))
                else:
                    ids.add(_student_id(item))
            elif key in _IDS_KEYS and isinstance(item, (list, tuple)):
                ids.update(_student_id(x) for x in item)
            elif isinstance(item, (dict, list, tuple)):
                ids.update(student_ids_in(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            ids.update(student_ids_in(item))
    return ids


def register_request_hooks(app):
    """Fence all routes: legacy GET handlers also finalize expired attempts."""
    @app.before_request
    def acquire_student_write_fence():
        if request.method == 'OPTIONS' or request.path == '/' or request.endpoint is None:
            return None
        try:
            if not protection_active():
                return None
            context = mutation_fence()
            context.__enter__()
            g._student_write_fence = context
            from cpm_back.auth.jwt_auth import get_current_user
            user = get_current_user()
            if user and user.get('role') == 'student':
                check_student_writable(user['id'])
            # Control-plane retries must remain usable for already-barred IDs.
            if not (request.endpoint or '').startswith('student_deletion.'):
                ids = student_ids_in(request.view_args or {})
                ids.update(student_ids_in(request.get_json(silent=True)))
                ids.update(student_ids_in(request.args.to_dict()))
                for ident in ids:
                    check_student_writable(ident)
        except StudentWriteBlocked:
            return jsonify({'status': False, 'error': 'student_deletion_pending'}), 409
        except Exception:
            return jsonify({'status': False, 'error': 'student_write_guard_unavailable'}), 503
        return None

    @app.teardown_request
    def release_student_write_fence(error):
        context = g.pop('_student_write_fence', None)
        if context is not None:
            context.__exit__(None, None, None)

    @app.errorhandler(StudentWriteBlocked)
    def rejected_student_write(error):
        return jsonify({'status': False, 'error': 'student_deletion_pending'}), 409

    @app.errorhandler(StudentFenceUnavailable)
    def unavailable_student_fence(error):
        return jsonify({'status': False, 'error': 'student_write_guard_unavailable'}), 503

# The manifest is intentionally explicit. Unknown ownership columns or incoming
# FKs stop deployment/readiness rather than being guessed or silently skipped.
DIRECT_TABLES = {
    'students': 'id', 'student_credentials': 'student_id',
    'attendance': 'student_id', 'class_day_attendance': 'student_id',
    'homework_sessions': 'student_id', 'test_sessions': 'student_id',
    'exam_sessions': 'student_id', 'Allratings': 'student_id',
    'zaps': 'student_id', 'student_progress': 'student_id',
    'student_card_progress': 'student_id', 'student_section_study_settings': 'student_id',
    'telegram_bot_chats': 'student_id', 'classic_exam_assignments': 'student_id',
    'classic_exam_student_privileges': 'student_id', 'classic_exam_attempts': 'student_id',
    'rating_recalc_staging': 'student_id', 'homework_submissions': 'student_id',
    'homework_file_jobs': 'student_id', 'homework_object_ownership': 'student_id',
    'homework_chat_threads': 'student_id',
}
# child -> (child FK, parent table, parent ownership column); multi-hop special
# children resolve through the entire path before invoking the same SQL guard.
INDIRECT_TABLES = {
    'zap_dates': ('zap_id', 'zaps', 'student_id'),
    'zap_img': ('zap_id', 'zaps', 'student_id'),
    'classic_exam_assignment_members': ('assignment_id', 'classic_exam_assignments', 'student_id'),
    'homework_submission_files': ('submission_id', 'homework_submissions', 'student_id'),
    'homework_chat_admin_followers': ('thread_id', 'homework_chat_threads', 'student_id'),
}
for _table in ('classic_exam_attempt_members', 'classic_exam_attempt_commands',
               'classic_exam_attempt_part_progress', 'classic_exam_presented_questions',
               'classic_exam_attempt_question_usage', 'classic_exam_vote_rounds', 'classic_exam_appeals'):
    INDIRECT_TABLES[_table] = ('attempt_id', 'classic_exam_attempts', 'student_id')
SPECIAL_TABLES = {
    'auth_users', 'exam_admin_commands', 'classic_exam_votes',
    'homework_chat_messages', 'homework_chat_reads', 'homework_realtime_presence',
    'homework_realtime_outbox', 'notifications', 'push_subscriptions',
}
# Actual audited legacy tables remain retired. These guards only prevent stale
# writers from recreating erased personal state; they do not restore features.
LEGACY_GUARD_COLUMNS = {
    'homework_chat_threads': {'id', 'student_id'},
    'homework_chat_messages': {'id', 'thread_id', 'sender_role', 'sender_id'},
    'homework_chat_reads': {'thread_id', 'reader_role', 'reader_id', 'last_message_id'},
    'homework_chat_admin_followers': {'thread_id', 'admin_id'},
    'homework_realtime_presence': {'thread_id', 'actor_role', 'actor_id'},
    'homework_realtime_outbox': {'thread_id', 'event_type', 'entity_id', 'payload_json'},
    'notifications': {'id', 'recipient_role', 'recipient_id', 'student_id', 'thread_id'},
    'push_subscriptions': {'user_role', 'user_id'},
}
HOMEWORK_GUARD_COLUMNS = {
    'homework_submissions': {'id', 'student_id', 'homework_id', 'draft_file_id', 'current_file_id'},
    'homework_submission_files': {'id', 'submission_id', 'object_key'},
    'homework_file_jobs': {'id', 'student_id', 'submission_id', 'homework_id', 'result_file_id', 'staging_key', 'processed_output_key'},
    'homework_object_ownership': {'object_key', 'student_id', 'submission_id', 'upload_job_id'},
    'homework_student_purges': {'job_id', 'student_id'},
    'homework_student_purge_objects': {'job_id', 'object_key'},
}
JOURNAL_COLUMNS = {
    'rating_recalc_jobs': ('errors',),
    'user_import_sessions': ('preview_payload',),
    'user_import_jobs': ('errors', 'entities_created', 'summary'),
    'user_import_job_results': ('rows_data',),
    'classic_exam_assignment_import_sessions': ('preview_payload', 'commit_result'),
    'outside_exam_result_import_sessions': ('preview_payload', 'commit_result'),
    'exam_admin_commands': ('receipt',),
}
CONTROL_TABLES = {
    'student_deletion_jobs', 'student_deletion_barriers', 'student_deletion_guard_state',
    'homework_student_purges', 'homework_student_purge_objects', 'homework_service_capabilities',
}
MINIMUM_TABLES = {'students', 'auth_users', 'student_deletion_barriers', 'student_deletion_guard_state'}


def _read_schema(connection):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute('SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()')
        columns = {}
        for row in cursor.fetchall():
            columns.setdefault(row['TABLE_NAME'], {})[row['COLUMN_NAME']] = row['DATA_TYPE']
        cursor.execute('SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL')
        references = cursor.fetchall()
        cursor.execute('SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')
        engines = {row['TABLE_NAME']: row['ENGINE'] for row in cursor.fetchall()}
        return columns, references, engines
    finally:
        cursor.close()


def _validate_ownership_schema(columns, references, engines):
    known = set(DIRECT_TABLES) | set(INDIRECT_TABLES) | SPECIAL_TABLES | set(JOURNAL_COLUMNS)
    missing = MINIMUM_TABLES - set(columns)
    if missing:
        raise StudentFenceUnavailable('missing_student_write_guard_tables:' + ','.join(sorted(missing)))
    for table, fields in columns.items():
        ownership = {key for key in fields if key.lower().replace('_', '') in _OWNERSHIP_COLUMN_KEYS}
        if ownership and table not in known | CONTROL_TABLES:
            raise StudentFenceUnavailable('unknown_student_ownership_table:' + table)
        expected_owners = {DIRECT_TABLES[table]} if table in DIRECT_TABLES else ({'target_student_id'} if table == 'exam_admin_commands' else ({'student_id'} if table == 'notifications' else set()))
        if table not in CONTROL_TABLES and ownership - expected_owners:
            raise StudentFenceUnavailable('unknown_student_ownership_column:' + table)
        if table in known | CONTROL_TABLES and str(engines.get(table)).upper() != 'INNODB':
            raise StudentFenceUnavailable('student_write_guard_requires_innodb:' + table)
    for row in references:
        parent, child = row['REFERENCED_TABLE_NAME'], row['TABLE_NAME']
        if parent in known and child not in known | CONTROL_TABLES:
            raise StudentFenceUnavailable('unknown_student_ownership_reference:' + child)
    for table, key in DIRECT_TABLES.items():
        if table in columns and key not in columns[table]:
            raise StudentFenceUnavailable('student_write_guard_missing_column:' + table + '.' + key)
    for table, (key, parent, owner) in INDIRECT_TABLES.items():
        if table in columns and (key not in columns[table] or parent not in columns or owner not in columns[parent]):
            raise StudentFenceUnavailable('student_write_guard_missing_reference:' + table)
    for table, required in {
        'auth_users': {'role', 'ref_id'},
        'exam_admin_commands': {'id', 'actor_role', 'actor_id', 'idempotency_key', 'command_type', 'target_exam_id', 'target_student_id', 'target_attempt_id', 'request_hash', 'receipt', 'tombstone', 'created_at', 'expires_at'},
        'classic_exam_votes': {'round_id'},
        'classic_exam_attempts': {'id', 'student_id', 'assignment_id', 'current_presented_question_id'},
        'classic_exam_presented_questions': {'id', 'attempt_id', 'replaces_presented_question_id'},
        'classic_exam_vote_rounds': {'id', 'attempt_id', 'presented_question_id'},
        'class_day_attendance': {'student_id', 'zap_id'},
        'student_deletion_barriers': {'student_id', 'job_id', 'created_at'},
        'student_deletion_guard_state': {'id', 'schema_version'},
        'student_deletion_jobs': {'id', 'student_id', 'status', 'manifest_json'},
        **LEGACY_GUARD_COLUMNS,
        **HOMEWORK_GUARD_COLUMNS,
    }.items():
        if table in columns and not required <= set(columns[table]):
            raise StudentFenceUnavailable('student_write_guard_schema_mismatch:' + table)
    if 'classic_exam_votes' in columns and not {'classic_exam_vote_rounds', 'classic_exam_attempts'} <= set(columns):
        raise StudentFenceUnavailable('student_write_guard_missing_reference:classic_exam_votes')
    if 'exam_admin_commands' in columns and not {'classic_exam_attempts', 'student_deletion_jobs'} <= set(columns):
        raise StudentFenceUnavailable('student_write_guard_missing_reference:exam_admin_commands')
    if set(HOMEWORK_GUARD_COLUMNS) & set(columns) and not set(HOMEWORK_GUARD_COLUMNS) <= set(columns):
        raise StudentFenceUnavailable('student_write_guard_missing_reference:homework_object_ownership')
    for table, parents in {
        'classic_exam_attempts': {'classic_exam_assignments', 'classic_exam_presented_questions'},
        'classic_exam_presented_questions': {'classic_exam_attempts', 'classic_exam_assignments'},
        'classic_exam_vote_rounds': {'classic_exam_attempts', 'classic_exam_assignments', 'classic_exam_presented_questions'},
        'classic_exam_votes': {'classic_exam_attempts', 'classic_exam_assignments', 'classic_exam_presented_questions', 'classic_exam_vote_rounds'},
        'class_day_attendance': {'zaps'},
    }.items():
        if table in columns and not parents <= set(columns):
            raise StudentFenceUnavailable('student_write_guard_missing_reference:' + table)
    legacy_parents = {
        'homework_chat_messages': {'homework_chat_threads'},
        'homework_chat_reads': {'homework_chat_threads', 'homework_chat_messages'},
        'homework_realtime_presence': {'homework_chat_threads'},
        'notifications': {'homework_chat_threads'},
        'homework_realtime_outbox': {'homework_chat_threads', 'homework_chat_messages', 'notifications', 'homework_submissions', 'homework_file_jobs'},
    }
    for table, parents in legacy_parents.items():
        if table in columns and not parents <= set(columns):
            raise StudentFenceUnavailable('student_write_guard_missing_reference:' + table)
    if 'homework_realtime_outbox' in columns:
        for table, required in {
            'homework_submissions': {'id', 'student_id', 'homework_id'},
            'homework_file_jobs': {'id', 'student_id', 'homework_id', 'submission_id'},
        }.items():
            if not required <= set(columns[table]):
                raise StudentFenceUnavailable('student_write_guard_missing_reference:homework_realtime_outbox')
    for table, fields in JOURNAL_COLUMNS.items():
        if table in columns and not set(fields) <= set(columns[table]):
            raise StudentFenceUnavailable('student_write_guard_missing_journal_column:' + table)
        if table in columns and any(columns[table][field].lower() != 'json' for field in fields):
            raise StudentFenceUnavailable('student_write_guard_journal_type_mismatch:' + table)
    return known & set(columns)


def _owner_checks(table, alias):
    if table == 'classic_exam_attempts':
        return (f'CALL cpm_assert_student_attempt_write({alias}.id,{alias}.student_id,'
                f'{alias}.assignment_id,{alias}.current_presented_question_id);')
    if table == 'classic_exam_presented_questions':
        return f'CALL cpm_assert_student_presented_write({alias}.attempt_id,{alias}.replaces_presented_question_id);'
    if table == 'classic_exam_vote_rounds':
        return f'CALL cpm_assert_student_round_write({alias}.attempt_id,{alias}.presented_question_id);'
    if table == 'class_day_attendance':
        return f'CALL cpm_assert_student_attendance_write({alias}.student_id,{alias}.zap_id);'
    if table == 'homework_submissions':
        return (f'CALL cpm_assert_student_submission_write({alias}.id,{alias}.student_id,'
                f'{alias}.draft_file_id,{alias}.current_file_id);')
    if table == 'homework_file_jobs':
        return (f'CALL cpm_assert_student_job_write({alias}.id,{alias}.student_id,{alias}.submission_id,'
                f'{alias}.homework_id,{alias}.result_file_id,{alias}.staging_key,{alias}.processed_output_key);')
    if table == 'homework_submission_files':
        return f'CALL cpm_assert_student_file_write({alias}.submission_id,{alias}.object_key);'
    if table == 'homework_object_ownership':
        return (f'CALL cpm_assert_student_ledger_write({alias}.object_key,{alias}.student_id,'
                f'{alias}.submission_id,{alias}.upload_job_id);')
    if table in DIRECT_TABLES:
        return f'CALL cpm_assert_student_write({alias}.`{DIRECT_TABLES[table]}`);'
    if table in INDIRECT_TABLES:
        key, parent, owner = INDIRECT_TABLES[table]
        lookup = f'SELECT `{owner}` INTO owner_id FROM `{parent}` WHERE id={alias}.`{key}` LOCK IN SHARE MODE;'
        return (f'CALL cpm_assert_student_write(NULL); SET owner_id=NULL; {lookup} IF {alias}.`{key}` IS NOT NULL AND owner_id IS NULL THEN '
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; "
                'CALL cpm_assert_student_write(owner_id);')
    if table == 'auth_users':
        return f"IF {alias}.role='student' THEN CALL cpm_assert_student_write({alias}.ref_id); END IF;"
    if table == 'push_subscriptions':
        return f'CALL cpm_assert_student_role_write({alias}.user_role,{alias}.user_id,NULL);'
    if table == 'homework_chat_messages':
        return (f'CALL cpm_assert_student_thread_write({alias}.thread_id,owner_id); '
                f"IF BINARY {alias}.sender_role='system' AND {alias}.sender_id IS NULL THEN CALL cpm_assert_student_write(NULL); "
                f'ELSE CALL cpm_assert_student_role_write({alias}.sender_role,{alias}.sender_id,owner_id); END IF;')
    if table == 'homework_chat_reads':
        return (f'CALL cpm_assert_student_thread_write({alias}.thread_id,owner_id); '
                f'CALL cpm_assert_student_role_write({alias}.reader_role,{alias}.reader_id,owner_id); '
                f'IF {alias}.last_message_id<>0 THEN CALL cpm_assert_student_message_write({alias}.last_message_id,{alias}.thread_id,owner_id); END IF;')
    if table == 'homework_realtime_presence':
        return (f'CALL cpm_assert_student_thread_write({alias}.thread_id,owner_id); '
                f'CALL cpm_assert_student_role_write({alias}.actor_role,{alias}.actor_id,owner_id);')
    if table == 'notifications':
        return (f'CALL cpm_assert_student_notification_write({alias}.recipient_role,{alias}.recipient_id,'
                f'{alias}.student_id,{alias}.thread_id,owner_id);')
    if table == 'homework_realtime_outbox':
        return (f'CALL cpm_assert_student_outbox_write({alias}.thread_id,{alias}.event_type,'
                f'{alias}.entity_id,{alias}.payload_json);')
    if table == 'classic_exam_votes':
        return f'CALL cpm_assert_student_vote_write({alias}.round_id);'
    if table == 'exam_admin_commands':
        return (f'CALL cpm_assert_student_write({alias}.target_student_id); '
                f'IF {alias}.target_attempt_id IS NOT NULL THEN '
                f'SET owner_id=NULL; SELECT student_id INTO owner_id FROM classic_exam_attempts WHERE id={alias}.target_attempt_id LOCK IN SHARE MODE; '
                "IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; "
                f"IF {alias}.target_student_id IS NOT NULL AND {alias}.target_student_id<>owner_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; "
                'CALL cpm_assert_student_write(owner_id); END IF;')
    if table in JOURNAL_COLUMNS:
        return ''
    raise StudentFenceUnavailable('unknown_student_ownership_table')


def _trigger_definitions(tables):
    for table in sorted(tables):
        for event, suffix in (('INSERT', 'i'), ('UPDATE', 'u')):
            name = 'cpm_sd_' + table + '_' + suffix
            # AFTER INSERT sees AUTO_INCREMENT-generated students.id.
            timing = 'AFTER' if table == 'students' and event == 'INSERT' else 'BEFORE'
            checks = (_owner_checks(table, 'OLD') + ' ') if event == 'UPDATE' else ''
            checks += _owner_checks(table, 'NEW')
            for column in JOURNAL_COLUMNS.get(table, ()):
                checks += f' CALL cpm_assert_student_json_write(NEW.`{column}`);'
            declarations = 'DECLARE owner_id BIGINT DEFAULT NULL; '
            if table == 'exam_admin_commands' and event == 'UPDATE':
                # Only a manifest-captured durable deletion job authorizes this
                # monotonic removal. No session variables or connection bypass.
                invariants = ('id', 'actor_role', 'actor_id', 'idempotency_key',
                              'command_type', 'target_exam_id', 'request_hash',
                              'created_at', 'expires_at')
                unchanged = ' AND '.join(f'(BINARY NEW.`{key}`<=>BINARY OLD.`{key}`)' for key in invariants)
                erasure = ("command_erasure AND NEW.target_student_id IS NULL AND NEW.target_attempt_id IS NULL "
                           "AND NEW.receipt IS NULL AND NEW.tombstone=1 AND " + unchanged)
                declarations += 'DECLARE command_captured BOOLEAN DEFAULT FALSE; DECLARE command_erasure BOOLEAN DEFAULT FALSE; '
                checks = ('CALL cpm_assert_student_command_erasure(OLD.id,command_captured,command_erasure); '
                          f'IF {erasure} THEN CALL cpm_assert_student_write(NULL); ELSE '
                          "IF command_captured THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_command_tombstone_immutable'; END IF; "
                          + checks + ' END IF;')
            body = 'BEGIN ' + declarations + checks + ' END'
            yield name, table, event, timing, body
        if table == 'exam_admin_commands':
            yield ('cpm_sd_exam_admin_commands_d', table, 'DELETE', 'BEFORE',
                   'BEGIN DECLARE command_captured BOOLEAN DEFAULT FALSE; DECLARE command_erasure BOOLEAN DEFAULT FALSE; '
                   'CALL cpm_assert_student_command_erasure(OLD.id,command_captured,command_erasure); '
                   "IF command_captured THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_command_tombstone_immutable'; END IF; END")


def _normalize_sql(statement):
    # Literal case/whitespace are security-significant (BINARY roles, event
    # names, JSON paths and regular expressions). Never normalize their bytes.
    # Keep identifier case as well: table names can be case-sensitive on Linux.
    tokens = re.split(r"('(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`(?:``|[^`])*`)", statement.strip())
    return ''.join(
        (token[1:-1].replace('``', '`') if token.startswith('`') else token)
        if index % 2 else re.sub(r'\s+', ' ', token)
        for index, token in enumerate(tokens)
    ).strip()


def verify_write_barrier_schema(connection):
    """Read-only readiness check; absent/drifted guards always fail closed."""
    columns, references, engines = _read_schema(connection)
    tables = _validate_ownership_schema(columns, references, engines)
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute('SELECT schema_version FROM student_deletion_guard_state WHERE id=1')
        state = cursor.fetchone()
        if not state or state['schema_version'] != 1:
            raise StudentFenceUnavailable('student_write_guard_version_mismatch')
        cursor.execute('SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,EVENT_MANIPULATION,ACTION_TIMING,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()')
        triggers = {row['TRIGGER_NAME']: row for row in cursor.fetchall()}
        for name, table, event, timing, body in _trigger_definitions(tables):
            row = triggers.get(name)
            if not row or (row['EVENT_OBJECT_TABLE'], row['EVENT_MANIPULATION'], row['ACTION_TIMING'], _normalize_sql(row['ACTION_STATEMENT'])) != (table, event, timing, _normalize_sql(body)):
                raise StudentFenceUnavailable('missing_or_changed_student_write_trigger:' + name)
        for name in ('cpm_sdb_insert', 'cpm_sdb_update', 'cpm_sdb_delete'):
            row = triggers.get(name)
            if not row or row['EVENT_OBJECT_TABLE'] != 'student_deletion_barriers':
                raise StudentFenceUnavailable('missing_student_barrier_trigger:' + name)
        # Exact routine/barrier-body postconditions come from the checked-in
        # migration. A marker or trigger name alone is not evidence of safety.
        migration = _migration_statements()
        for statement in migration:
            normalized = _normalize_sql(statement).lower()
            if normalized.startswith('create procedure cpm_assert_student_'):
                routine_name = statement.split()[2].split('(')[0]
                cursor.execute("SELECT ROUTINE_DEFINITION FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE() AND ROUTINE_NAME=%s AND ROUTINE_TYPE='PROCEDURE'", (routine_name,))
                row = cursor.fetchone()
                expected = statement[statement.upper().index('BEGIN'):]
                if not row or _normalize_sql(row['ROUTINE_DEFINITION'] or '') != _normalize_sql(expected):
                    raise StudentFenceUnavailable('missing_or_changed_student_write_procedure')
            elif normalized.startswith('create trigger cpm_sdb_'):
                name = statement.split()[2]
                body = re.split(r'FOR EACH ROW\s+', statement, flags=re.I)[1]
                if _normalize_sql(triggers[name]['ACTION_STATEMENT']) != _normalize_sql(body):
                    raise StudentFenceUnavailable('changed_student_barrier_trigger:' + name)
        return {'tables': sorted(tables), 'guard_version': 1}
    finally:
        cursor.close()


def _migration_statements():
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / 'migrations/031_student_deletion_write_barriers.sql'
    statements, buffer, delimiter = [], [], ';'
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('--') or not line.strip():
            continue
        if line.upper().startswith('DELIMITER '):
            delimiter = line.split()[1]
            continue
        buffer.append(line)
        if line.rstrip().endswith(delimiter):
            statements.append('\n'.join(buffer).rstrip()[:-len(delimiter)].strip())
            buffer = []
    if buffer:
        raise StudentFenceUnavailable('incomplete_write_guard_migration')
    return statements


def apply_write_barrier_migration(connection):
    """Explicit operator migration only, with all services stopped beforehand.

    DDL auto-commits. Never call from app startup or a request. Repeat execution
    repairs a partial installation; deletion remains disabled until verification.
    """
    with mutation_fence(connection=connection, force=True, verify_schema=False):
        cursor = connection.cursor()
        try:
            for statement in _migration_statements():
                cursor.execute(statement)
            columns, references, engines = _read_schema(connection)
            tables = _validate_ownership_schema(columns, references, engines)
            for name, table, event, timing, body in _trigger_definitions(tables):
                cursor.execute(f'DROP TRIGGER IF EXISTS `{name}`')
                cursor.execute(f'CREATE TRIGGER `{name}` {timing} {event} ON `{table}` FOR EACH ROW {body}')
            connection.commit()
            return verify_write_barrier_schema(connection)
        finally:
            cursor.close()

PERSONAL_MONGO_COLLECTIONS = {'test_attempts': 'studentId', 'test_sessions': 'studentId', 'rate_rec': 'student_id'}
SHARED_MONGO_COLLECTIONS = {'tests', 'test_versions', 'test_drafts', 'test_question_change_log', 'schedule'}
_MONGO_WRITES = {'insert_one', 'insert_many', 'update_one', 'update_many', 'replace_one',
                 'find_one_and_update', 'find_one_and_replace', 'delete_one', 'delete_many',
                 'find_one_and_delete', 'bulk_write'}
_MONGO_DELETES = {'delete_one', 'delete_many', 'find_one_and_delete'}


def _has_write_stage(value):
    if isinstance(value, dict):
        return bool({'$out', '$merge'} & set(value)) or any(_has_write_stage(v) for v in value.values())
    return isinstance(value, (tuple, list)) and any(_has_write_stage(v) for v in value)


class GuardedMongoCollection:
    """Fence the actual matched document, including staff `_id`-only updates.

    Bulk writes/pipeline updates require an explicit ownership-aware extension;
    unsupported operations fail closed. Deleted identities cannot be upserted by
    a stale background job, even after their SQL/Mongo rows are gone.
    """
    def __init__(self, collection):
        self._collection = collection

    @property
    def database(self):
        return GuardedMongoDatabase(self._collection.database)

    def with_options(self, *args, **kwargs):
        return type(self)(self._collection.with_options(*args, **kwargs))

    def aggregate(self, pipeline, *args, **kwargs):
        if protection_active() and _has_write_stage(pipeline):
            raise StudentWriteBlocked('unguarded_mongo_aggregation_write')
        return self._collection.aggregate(pipeline, *args, **kwargs)

    def __getattr__(self, name):
        if name in _MONGO_WRITES:
            return lambda *args, **kwargs: self._mutate(name, *args, **kwargs)
        if name in {'rename', 'drop', 'map_reduce', 'inline_map_reduce'} and protection_active():
            raise StudentWriteBlocked('unsupported_mongo_mutation')
        return getattr(self._collection, name)

    def _owner(self, document, field):
        if not isinstance(document, dict) or document.get(field) is None:
            raise StudentWriteBlocked('mongo_student_owner_required')
        return _student_id(document[field])

    def _mutate(self, method, *args, **kwargs):
        if not protection_active():
            return getattr(self._collection, method)(*args, **kwargs)
        name = self._collection.name
        if name not in PERSONAL_MONGO_COLLECTIONS and name not in SHARED_MONGO_COLLECTIONS:
            raise StudentWriteBlocked('unknown_mongo_write_collection')
        with mutation_fence():
            if name in SHARED_MONGO_COLLECTIONS or method in _MONGO_DELETES:
                return getattr(self._collection, method)(*args, **kwargs)
            if method == 'bulk_write':
                raise StudentWriteBlocked('unguarded_mongo_bulk_write')
            if not self._collection.write_concern.acknowledged:
                raise StudentFenceUnavailable('acknowledged_mongo_writes_required')
            field = PERSONAL_MONGO_COLLECTIONS[name]
            ids = set()
            if method in ('insert_one', 'insert_many'):
                raw = args[0] if args else kwargs.get('document' if method == 'insert_one' else 'documents')
                docs = [raw] if method == 'insert_one' else list(raw)
                ids.update(self._owner(doc, field) for doc in docs)
                if method == 'insert_many':
                    if args:
                        args = (docs,) + args[1:]
                    else:
                        kwargs['documents'] = docs
            else:
                query = args[0] if args else kwargs.get('filter', {})
                update = args[1] if len(args) > 1 else kwargs.get('update', kwargs.get('replacement'))
                if not isinstance(query, dict) or not isinstance(update, dict):
                    raise StudentWriteBlocked('unguarded_mongo_pipeline_update')
                existing = list(self._collection.find(query, {field: 1}))
                ids.update(self._owner(doc, field) for doc in existing)
                replacement = method in ('replace_one', 'find_one_and_replace')
                if replacement:
                    ids.add(self._owner(update, field))
                else:
                    # Ownership changes via arithmetic, rename, unset or dotted
                    # paths are deliberately unsupported. $set is checked below.
                    for operation, values in update.items():
                        if not operation.startswith('$') or not isinstance(values, dict):
                            raise StudentWriteBlocked('unsupported_mongo_update')
                        for key, value in values.items():
                            if key == field or key.startswith(field + '.') or (operation == '$rename' and value == field):
                                if operation not in ('$set', '$setOnInsert') or key != field:
                                    raise StudentWriteBlocked('mongo_owner_change_forbidden')
                                ids.add(_student_id(value))
                # Positional upsert is supported by update/replace, not by
                # find_one_and_* (projection/other args occupy later positions).
                upsert = kwargs.get('upsert', args[2] if len(args) > 2 and method in ('update_one', 'update_many', 'replace_one') else False)
                if upsert:
                    owner = update.get(field) if replacement else update.get('$set', {}).get(field, update.get('$setOnInsert', {}).get(field, query.get(field)))
                    if isinstance(owner, dict):
                        owner = owner.get('$eq')
                    if owner is None:
                        raise StudentWriteBlocked('mongo_upsert_owner_required')
                    ids.add(_student_id(owner))
            for student_id in sorted(ids):
                check_student_writable(student_id)
            return getattr(self._collection, method)(*args, **kwargs)


class GuardedMongoDatabase:
    def __init__(self, database):
        self._database = database

    def __getitem__(self, name):
        return GuardedMongoCollection(self._database[name])

    def get_collection(self, name, *args, **kwargs):
        return GuardedMongoCollection(self._database.get_collection(name, *args, **kwargs))

    def with_options(self, *args, **kwargs):
        return type(self)(self._database.with_options(*args, **kwargs))

    def aggregate(self, pipeline, *args, **kwargs):
        if protection_active() and _has_write_stage(pipeline):
            raise StudentWriteBlocked('unguarded_mongo_aggregation_write')
        return self._database.aggregate(pipeline, *args, **kwargs)

    def __getattr__(self, name):
        if name in set(PERSONAL_MONGO_COLLECTIONS) | SHARED_MONGO_COLLECTIONS:
            return self[name]
        if name in {'command', 'drop_collection', 'create_collection'} and protection_active():
            raise StudentWriteBlocked('unsupported_mongo_database_mutation')
        value = getattr(self._database, name)
        from pymongo.collection import Collection
        return GuardedMongoCollection(value) if isinstance(value, Collection) else value
