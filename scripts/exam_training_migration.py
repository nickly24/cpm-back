"""Resumable additive migration 029. Check-only by default, --plan is offline.

Apply only after local tests and verification of a restorable private backup.
Never invokes historical migrations, Flask, MongoDB or application workers.
DDL commits implicitly; reruns verify completed columns and resume missing ones.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.exams_migrations import MigrationError, connection_parameters

MIGRATION = ROOT / "migrations/029_exam_training.sql"
# table, column, original type (None means absent), final type, nullable, default
CHANGES = (
    ("exams", "training_enabled", None, "tinyint(1)", "NO", "0"),
    ("student_card_progress", "section_kind", "enum('manual','test')", "enum('manual','test','exam')", "NO", None),
    ("student_card_progress", "section_ref_id", "varchar(24)", "varchar(64)", "NO", None),
    ("student_section_study_settings", "section_kind", "enum('manual','test')", "enum('manual','test','exam')", "NO", None),
    ("student_section_study_settings", "section_ref_id", "varchar(24)", "varchar(64)", "NO", None),
    ("student_section_study_settings", "last_part_id", None, "bigint", "YES", None),
)


def statements():
    sql = re.sub(r"^--[^\n]*", "", MIGRATION.read_text(), flags=re.M)
    result = [item.strip() for item in sql.split(";") if item.strip()]
    if len(result) != len(CHANGES) or any(not s.startswith("ALTER TABLE ") for s in result):
        raise MigrationError("Invalid migration 029 package")
    return result


def column_state(cursor, change):
    table, name, old, new, nullable, default = change
    cursor.execute(
        "SELECT COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, name),
    )
    row = cursor.fetchone()
    if row is None:
        if old is None:
            return "pending"
    elif (row["IS_NULLABLE"], row["COLUMN_DEFAULT"], row["EXTRA"]) == (nullable, default, ""):
        if row["COLUMN_TYPE"].lower() == new:
            return "verified"
        if old is not None and row["COLUMN_TYPE"].lower() == old:
            return "pending"
    raise MigrationError("Unexpected schema for " + table + "." + name)


def run_migration(connection, apply=False):
    sql = statements()
    cursor = connection.cursor(dictionary=True)
    acquired = False
    report = {"migration": MIGRATION.name, "mode": "apply" if apply else "check", "steps": []}
    try:
        cursor.execute("SET SESSION lock_wait_timeout=10")
        cursor.execute("SET SESSION innodb_lock_wait_timeout=10")
        cursor.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
        cursor.execute("SELECT VERSION() AS version, DATABASE() AS db")
        server = cursor.fetchone()
        if not server["version"].startswith("8.") or not server["db"]:
            raise MigrationError("Migration requires a selected MySQL 8 database")
        lock_name = "exam-training-029:" + hashlib.sha256(server["db"].encode()).hexdigest()[:32]
        if apply:
            cursor.execute("SELECT GET_LOCK(%s,0) AS acquired", (lock_name,))
            acquired = cursor.fetchone()["acquired"] == 1
            if not acquired:
                raise MigrationError("Another migration 029 is running")
        for table in sorted({change[0] for change in CHANGES}):
            cursor.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
            row = cursor.fetchone()
            if not row or row["ENGINE"] != "InnoDB":
                raise MigrationError("Required InnoDB table is missing: " + table)
        # Validate every original/final column before the first DDL operation.
        states = [column_state(cursor, change) for change in CHANGES]
        for change, query, state in zip(CHANGES, sql, states):
            if state == "pending" and apply:
                cursor.execute(query)
                connection.commit()
                if column_state(cursor, change) != "verified":
                    raise MigrationError("Migration postcondition failed")
                state = "applied"
            report["steps"].append({"column": change[0] + "." + change[1], "status": state})
        report["fullyApplied"] = all(item["status"] != "pending" for item in report["steps"])
        report["checksum"] = hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        return report
    finally:
        connection.rollback()
        if acquired:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            cursor.fetchall()
        cursor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-verified", action="store_true")
    parser.add_argument("--connection-json", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.plan:
            print(json.dumps({"databaseConnected": False, "migration": MIGRATION.name, "statements": statements()}))
            return 0
        if args.apply and not args.backup_verified:
            raise MigrationError("Apply requires --backup-verified after restoring and verifying a private backup")
        import mysql.connector
        connection = mysql.connector.connect(**connection_parameters(args.connection_json), connection_timeout=10, autocommit=False)
        try:
            report = run_migration(connection, args.apply)
        finally:
            connection.close()
        print(json.dumps(report))
        return 0
    except Exception as error:
        # Connector errors can contain addresses and identities: do not print them.
        message = str(error) if isinstance(error, MigrationError) else type(error).__name__
        print(json.dumps({"success": False, "message": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
