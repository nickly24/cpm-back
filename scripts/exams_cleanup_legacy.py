"""Owner-authorized cleanup: keep newest result ID; delete missing-student results.

No Flask startup. Dry-run by default. Apply requires the reviewed report and a
private backup directory. A changed set of rows/values stops before DELETE.
Backup is a restorable JSON snapshot of exam_sessions, not just a checksum.
"""

import argparse
import hashlib
import json
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ("id", "exam_id", "student_id", "val", "points", "examinator")


def fingerprint(rows):
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["id"]):
        digest.update(
            json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def cleanup_plan(rows, student_ids):
    newest = {}
    for row in rows:
        key = (row["exam_id"], row["student_id"])
        newest[key] = max(newest.get(key, 0), row["id"])
    orphans = {row["id"] for row in rows if row["student_id"] not in student_ids}
    duplicates = {
        row["id"]
        for row in rows
        if row["student_id"] in student_ids
        and row["id"] != newest[(row["exam_id"], row["student_id"])]
    }
    return {
        "olderDuplicateIds": sorted(duplicates),
        "missingStudentResultIds": sorted(orphans),
        "deleteIds": sorted(duplicates | orphans),
    }


def reviewed_ids(report):
    newest = {}
    for row in report["duplicateResults"]:
        key = (row["examId"], row["studentId"])
        newest[key] = max(newest.get(key, 0), row["id"])
    return {
        row["id"]
        for row in report["duplicateResults"]
        if row["id"] != newest[(row["examId"], row["studentId"])]
    } | {row["id"] for row in report["orphanResults"] if row["missingStudent"]}


def save_backup(directory, rows, ddl, plan):
    directory = directory.expanduser().resolve()
    if ROOT == directory or ROOT in directory.parents:
        raise ValueError("Backup must be outside the repository")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ValueError("Backup directory must be private (mode 0700)")
    payload = {
        "formatVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "table": "exam_sessions",
        "columns": COLUMNS,
        "ddl": ddl,
        "rows": rows,
        "beforeFingerprint": fingerprint(rows),
        "approvedPlan": plan,
    }
    path = directory / (
        "exam-sessions-before-cleanup-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + ".json"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    restored = json.loads(path.read_text(encoding="utf-8"))
    if (
        restored["rows"] != rows
        or fingerprint(restored["rows"]) != payload["beforeFingerprint"]
    ):
        raise ValueError("Backup roundtrip validation failed")
    return path


def execute(connection, report, backup_directory=None, apply=False):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SET SESSION innodb_lock_wait_timeout=5")
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute(
            "START TRANSACTION"
            if apply
            else "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
        )
        suffix = " FOR SHARE" if apply else ""
        cursor.execute("SELECT id FROM students ORDER BY id" + suffix)
        student_ids = {row["id"] for row in cursor.fetchall()}
        cursor.execute("SELECT id FROM exams ORDER BY id" + suffix)
        exam_ids = {row["id"] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT id,status FROM rating_recalc_jobs WHERE status IN ('running','queued')"
            + (" FOR UPDATE" if apply else "")
        )
        if cursor.fetchall():
            raise ValueError("Active rating job: cleanup stopped")
        cursor.execute(
            "SELECT id,exam_id,student_id,val,points,examinator FROM exam_sessions ORDER BY id"
            + (" FOR UPDATE" if apply else "")
        )
        rows = cursor.fetchall()
        plan = cleanup_plan(rows, student_ids)
        if not plan["deleteIds"]:
            return {
                "status": "already_clean",
                "deleted": 0,
                "remainingResults": len(rows),
            }
        if (
            set(plan["deleteIds"]) != reviewed_ids(report)
            or fingerprint(rows) != report["fingerprints"]["exam_sessions"]
            or any(row["exam_id"] not in exam_ids for row in rows)
        ):
            raise ValueError(
                "Source data changed since reviewed report: cleanup stopped"
            )
        summary = {
            "status": "dry_run",
            "beforeResults": len(rows),
            "deleteCount": len(plan["deleteIds"]),
            **plan,
        }
        if not apply:
            return summary
        if backup_directory is None:
            raise ValueError("Private backup directory required")
        cursor.execute("SHOW CREATE TABLE exam_sessions")
        ddl = cursor.fetchone()["Create Table"]
        backup = save_backup(backup_directory, rows, ddl, plan)
        placeholders = ",".join(["%s"] * len(plan["deleteIds"]))
        cursor.execute(
            f"DELETE FROM exam_sessions WHERE id IN ({placeholders})",
            tuple(plan["deleteIds"]),
        )
        if cursor.rowcount != len(plan["deleteIds"]):
            raise ValueError("Unexpected deleted row count: rolled back")
        cursor.execute(
            "SELECT id,exam_id,student_id,val,points,examinator FROM exam_sessions ORDER BY id"
        )
        remaining = cursor.fetchall()
        expected = [row for row in rows if row["id"] not in set(plan["deleteIds"])]
        if remaining != expected or cleanup_plan(remaining, student_ids)["deleteIds"]:
            raise ValueError("Post-cleanup verification failed: rolled back")
        connection.commit()
        return {
            **summary,
            "status": "committed",
            "deleted": len(plan["deleteIds"]),
            "remainingResults": len(remaining),
            "backupPath": str(backup),
            "afterFingerprint": fingerprint(remaining),
            "publishedRatingNotRecalculated": True,
        }
    finally:
        connection.rollback()
        cursor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-report", type=Path, required=True)
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.backup_directory:
        parser.error("--apply requires --backup-directory")
    import mysql.connector

    config = runpy.run_path(str(ROOT / "cpm_back/config.py"))["config"]
    connection = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        connection_timeout=10,
        autocommit=False,
    )
    try:
        result = execute(
            connection,
            json.loads(args.reviewed_report.read_text()),
            args.backup_directory,
            args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Do not expose credentials, server address or connector error messages.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "errorType": type(exc).__name__,
                    "reason": (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "database_operation_failed"
                    ),
                    "errno": getattr(exc, "errno", None),
                }
            )
        )
        return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
