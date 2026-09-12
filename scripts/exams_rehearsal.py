"""Complete migration-scoped backup and local-only classic-exam restore.

Backup mode reads configured production with a read-only snapshot. Restore mode
accepts only a Unix socket and a cpm_exam_* disposable schema; no remote host.
Student/staff accounts are synthetic (no copied credentials or contact details).
This is not a whole-application backup: only exam/rating domain rows and migration
journals are authentic. Stored domain name snapshots/import payloads are retained
unchanged, as required for recovery. Pause relevant writes/DDL while backing up.
"""

import argparse
import json
import os
import re
import runpy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_DATA_TABLES = (
    "directions",
    "exams",
    "exam_sessions",
    "Allratings",
    "rating_recalc_jobs",
)
DATA_TABLES = LEGACY_DATA_TABLES + (
    "exam_admin_commands",
    "outside_exam_result_import_sessions",
    "classic_exam_settings",
    "classic_exam_parts",
    "classic_exam_questions",
    "classic_exam_question_import_sessions",
    "classic_exam_grade_thresholds",
    "classic_exam_commissions",
    "classic_exam_commission_members",
    "classic_exam_assignments",
    "classic_exam_assignment_members",
    "classic_exam_student_privileges",
    "classic_exam_assignment_import_sessions",
    "classic_exam_definition_versions",
    "classic_exam_definition_questions",
    "classic_exam_attempts",
    "classic_exam_attempt_members",
    "classic_exam_attempt_commands",
    "classic_exam_attempt_part_progress",
    "classic_exam_presented_questions",
    "classic_exam_attempt_question_usage",
    "classic_exam_vote_rounds",
    "classic_exam_votes",
    "classic_exam_appeals",
    "rating_source_state",
    "rating_recalc_staging",
    "schema_migrations",
    "exam_schema_migration_steps",
)
SUPPORT_TABLES = (
    "schools",
    "groups",
    "students",
    "admins",
    "proctors",
    "examinators",
    "supervisors",
    "auth_users",
    "admin_roles",
    "admin_role_permissions",
    "admin_role_users",
    "student_credentials",
)
SCHEMA_TABLES = SUPPORT_TABLES + DATA_TABLES
IDENTITY_TABLES = (
    "students",
    "admins",
    "proctors",
    "examinators",
    "supervisors",
    "admin_roles",
    "admin_role_users",
)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
        raise ValueError("Unsupported database identifier")
    return "`" + value + "`"


def primary_key(cursor, table):
    cursor.execute(f"SHOW KEYS FROM {identifier(table)} WHERE Key_name='PRIMARY'")
    return [
        r["Column_name"]
        for r in sorted(cursor.fetchall(), key=lambda r: r["Seq_in_index"])
    ]


def read_rows(cursor, table, columns):
    if not columns:
        raise ValueError("A recoverable data table needs a primary key: " + table)
    order = ",".join(identifier(column) for column in columns)
    cursor.execute(f"SELECT * FROM {identifier(table)} ORDER BY {order}")
    return cursor.fetchall()


def restore_order(tables):
    """Topological DDL/data order, with all FK checks left enabled throughout."""
    dependencies = {}
    for table, content in tables.items():
        if table not in SCHEMA_TABLES:
            raise ValueError("Backup contains a table outside migration scope")
        ddl = content["ddl"]
        if not ddl.startswith("CREATE TABLE " + identifier(table) + " ("):
            raise ValueError("Unexpected table DDL: " + table)
        references = re.findall(r"REFERENCES\s+`([^`]+)`(\s*\.)?", ddl, re.IGNORECASE)
        if any(qualified for _, qualified in references):
            raise ValueError("Cross-schema foreign keys cannot be restored locally")
        dependencies[table] = {parent for parent, _ in references}
        if not dependencies[table].issubset(tables):
            raise ValueError("Backup omits a foreign-key dependency: " + table)
    result = []
    while dependencies:
        ready = sorted(table for table, parents in dependencies.items() if not parents)
        if not ready:
            raise ValueError("Cyclic foreign keys require an explicit restore plan")
        result.extend(ready)
        for table in ready:
            del dependencies[table]
        for parents in dependencies.values():
            parents.difference_update(ready)
    return result


def serialize(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def snapshot(connection):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SET time_zone='+00:00'")
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'"
        )
        available = {row["TABLE_NAME"] for row in cursor.fetchall()}
        # Fail closed if a future domain table is not yet covered by this backup.
        discovered = {
            name
            for name in available
            if name.startswith(("classic_exam_", "outside_exam_", "exam_", "rating_"))
        }
        if not discovered.issubset(DATA_TABLES):
            raise ValueError(
                "Unrecognized exam/rating table; extend backup scope first"
            )
        tables = {}
        for table in SCHEMA_TABLES:
            if table not in available:
                continue
            cursor.execute(f"SHOW CREATE TABLE `{table}`")
            ddl = cursor.fetchone()["Create Table"]
            rows = []
            ids = []
            keys = primary_key(cursor, table)
            fixtures = []
            if table in DATA_TABLES:
                rows = read_rows(cursor, table, keys)
            elif table in IDENTITY_TABLES:
                # Only numeric identities/role relation are copied, never names,
                # contacts, credentials, role permissions or active sessions.
                role_column = table == "admin_role_users" and "`role_id`" in ddl
                cursor.execute(
                    f"SELECT id{',role_id' if role_column else ''} FROM `{table}` ORDER BY id"
                )
                fixtures = cursor.fetchall()
                ids = [row["id"] for row in fixtures]
            tables[table] = {
                "ddl": ddl,
                "rows": rows,
                "fixtureIds": ids,
                "fixtureRows": fixtures,
                "primaryKey": keys,
                "dataMode": "authentic" if table in DATA_TABLES else "synthetic",
            }
        restore_order(tables)
        return {
            "formatVersion": 2,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "purpose": "exam_migration_backup_and_local_rehearsal",
            "dataTables": [table for table in DATA_TABLES if table in tables],
            "credentialsCopied": False,
            "tables": tables,
        }
    finally:
        connection.rollback()
        cursor.close()


def save_private(path, data):
    path = path.expanduser().resolve()
    if ROOT == path or ROOT in path.parents:
        raise ValueError("Business-data backup must be outside repository")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("Backup directory must have mode0700")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, default=serialize)
        stream.flush()
        os.fsync(stream.fileno())
    # Roundtrip is verified before a caller can rely on the backup.
    return json.loads(path.read_text(encoding="utf-8"))


def restore_local(connection, schema, data):
    if not re.fullmatch(r"cpm_exam_[a-z0-9_]+", schema):
        raise ValueError("Only cpm_exam_* schemas may be restored")
    if not connection.unix_socket:
        raise ValueError("Local Unix socket required")
    if data.get("formatVersion") not in (1, 2):
        raise ValueError("Unsupported backup format")
    tables = data["tables"]
    order = restore_order(tables)
    authentic = (
        data["dataTables"]
        if data["formatVersion"] == 2
        else [table for table in LEGACY_DATA_TABLES if table in tables]
    )
    if len(set(authentic)) != len(authentic) or set(authentic) != set(
        tables
    ).intersection(DATA_TABLES):
        raise ValueError("Backup data-table manifest is incomplete")
    for table, content in tables.items():
        if table not in authentic and content.get("rows"):
            raise ValueError("Backup must not contain real account/credential rows")
        for fixture in content.get("fixtureRows", []):
            allowed = {"id", "role_id"} if table == "admin_role_users" else {"id"}
            if table not in IDENTITY_TABLES or not set(fixture).issubset(allowed):
                raise ValueError("Unexpected identity fixture fields")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT @@socket AS socket")
        cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
            (schema,),
        )
        if cursor.fetchone()["n"]:
            raise ValueError(
                "Restore schema must be empty; no existing tables are overwritten"
            )
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )
        cursor.execute(f"USE `{schema}`")
        cursor.execute("SET time_zone='+00:00'")
        cursor.execute(
            "SET SESSION sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'"
        )
        cursor.execute("SET SESSION foreign_key_checks=1")
        for table in order:
            cursor.execute(tables[table]["ddl"])
        # Finish every CREATE before beginning data restoration: later DDL must
        # not implicitly commit a partially restored data set.
        for table in order:
            for row in tables[table]["rows"]:
                columns = ",".join(identifier(column) for column in row)
                cursor.execute(
                    f'INSERT INTO `{table}` ({columns}) VALUES ({",".join(["%s"] * len(row))})',
                    tuple(row.values()),
                )
            # Satisfy copied exam FKs without copying unrelated personal data.
            fixtures = tables[table].get("fixtureRows") or [
                {"id": id} for id in tables[table].get("fixtureIds", [])
            ]
            for fixture in fixtures:
                id = fixture["id"]
                if table == "students":
                    cursor.execute(
                        "INSERT INTO students (id,full_name,`class`,tg_name) VALUES (%s,%s,10,'')",
                        (id, f"Локальный студент {id}"),
                    )
                elif table in ("admins", "examinators", "supervisors"):
                    cursor.execute(
                        f"INSERT INTO `{table}` (id,full_name) VALUES (%s,%s)",
                        (id, f"Локальный {table} {id}"),
                    )
                elif table == "proctors":
                    cursor.execute(
                        "INSERT INTO proctors (id,full_name) VALUES (%s,%s)",
                        (id, f"Локальный проктор {id}"),
                    )
                elif table == "admin_roles":
                    cursor.execute(
                        "INSERT INTO admin_roles(id,name) VALUES(%s,%s)",
                        (id, f"Локальная роль {id}"),
                    )
                elif table == "admin_role_users":
                    if "role_id" in fixture:
                        cursor.execute(
                            "INSERT INTO admin_role_users(id,full_name,role_id,is_active) VALUES(%s,%s,%s,0)",
                            (id, f"Локальный администратор {id}", fixture["role_id"]),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO admin_role_users(id,full_name) VALUES(%s,%s)",
                            (id, f"Локальный администратор {id}"),
                        )
        # No auth_users rows, even synthetic logins: a restored recovery schema
        # is deliberately not an authenticated runnable copy of production.
        counts = {}
        for table in authentic:
            restored = read_rows(cursor, table, primary_key(cursor, table))
            expected = tables[table]["rows"]
            actual_json = json.loads(
                json.dumps(restored, default=serialize, ensure_ascii=False)
            )
            if actual_json != json.loads(
                json.dumps(expected, default=serialize, ensure_ascii=False)
            ):
                raise ValueError("Restored table differs: " + table)
            counts[table] = len(restored)
        connection.commit()
        return {
            "schema": schema,
            "restoredAndCompared": counts,
            "credentialsCopied": False,
            "foreignKeyChecks": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--schema", default="cpm_exam_rehearsal")
    args = parser.parse_args()
    if bool(args.backup) == bool(args.restore) or (args.restore and not args.socket):
        parser.error("Choose --backup FILE or --restore FILE --socket LOCAL_SOCKET")
    import mysql.connector

    if args.backup:
        config = runpy.run_path(str(ROOT / "cpm_back/config.py"))["config"]
        connection = mysql.connector.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
            connection_timeout=10,
        )
    else:
        connection = mysql.connector.connect(unix_socket=str(args.socket), user="root")
    try:
        if args.backup:
            data = save_private(args.backup, snapshot(connection))
            print(
                json.dumps(
                    {
                        "backupPath": str(args.backup),
                        "tables": {
                            k: len(v["rows"]) for k, v in data["tables"].items()
                        },
                        "databaseModified": False,
                    }
                )
            )
        else:
            print(
                json.dumps(
                    restore_local(
                        connection, args.schema, json.loads(args.restore.read_text())
                    )
                )
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "errorType": type(exc).__name__,
                    "errno": getattr(exc, "errno", None),
                    "reason": (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "database_operation_failed"
                    ),
                }
            )
        )
        return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
