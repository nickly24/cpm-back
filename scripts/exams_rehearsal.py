"""Targeted backup and local-only MySQL restore for classic exam migrations.

Backup mode reads configured production with a read-only snapshot. Restore mode
accepts only a Unix socket and a cpm_exam_* disposable schema; no remote host.
Student/staff accounts are synthetic (no copied credentials or contact details).
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
DATA_TABLES = (
    "directions",
    "exams",
    "exam_sessions",
    "Allratings",
    "rating_recalc_jobs",
)
SCHEMA_TABLES = (
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
) + DATA_TABLES


def serialize(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def snapshot(connection):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        tables = {}
        for table in SCHEMA_TABLES:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                (table,),
            )
            if not cursor.fetchone():
                continue
            cursor.execute(f"SHOW CREATE TABLE `{table}`")
            ddl = cursor.fetchone()["Create Table"]
            rows = []
            ids = []
            if table in DATA_TABLES:
                cursor.execute(f"SELECT * FROM `{table}` ORDER BY id")
                rows = cursor.fetchall()
            elif table in (
                "students",
                "admins",
                "proctors",
                "examinators",
                "supervisors",
            ):
                cursor.execute(f"SELECT id FROM `{table}` ORDER BY id")
                ids = [row["id"] for row in cursor.fetchall()]
            tables[table] = {"ddl": ddl, "rows": rows, "fixtureIds": ids}
        return {
            "formatVersion": 1,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "purpose": "exam_migration_backup_and_local_rehearsal",
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
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT @@socket AS socket")
        if not connection.unix_socket:
            raise ValueError("Local Unix socket required")
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
        tables = data["tables"]
        for table in SCHEMA_TABLES:
            if table not in tables:
                continue
            cursor.execute(tables[table]["ddl"])
            for row in tables[table]["rows"]:
                columns = ",".join("`" + column + "`" for column in row)
                cursor.execute(
                    f'INSERT INTO `{table}` ({columns}) VALUES ({",".join(["%s"] * len(row))})',
                    tuple(row.values()),
                )
            # Satisfy copied exam FKs without copying unrelated personal data.
            for id in tables[table]["fixtureIds"]:
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
        for role, table in (
            ("student", "students"),
            ("admin", "admins"),
            ("examinator", "examinators"),
            ("proctor", "proctors"),
            ("supervisor", "supervisors"),
        ):
            for id in tables.get(table, {}).get("fixtureIds", []):
                cursor.execute(
                    "INSERT INTO auth_users (username,ref_id,role,password) VALUES (%s,%s,%s,NULL)",
                    (f"local-{role}-{id}", id, role),
                )
        connection.commit()
        counts = {}
        for table in DATA_TABLES:
            cursor.execute(f"SELECT * FROM `{table}` ORDER BY id")
            restored = cursor.fetchall()
            expected = tables[table]["rows"]
            actual_json = json.loads(
                json.dumps(restored, default=serialize, ensure_ascii=False)
            )
            if actual_json != expected:
                raise ValueError("Restored table differs: " + table)
            counts[table] = len(restored)
        return {
            "schema": schema,
            "restoredAndCompared": counts,
            "credentialsCopied": False,
        }
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
