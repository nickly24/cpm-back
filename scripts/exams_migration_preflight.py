"""Read-only preflight for the classic-exam migration.

Run from the backend checkout with its configured Python environment. This script
loads config.py directly: it never starts Flask, MongoDB, workers or the bot.
It deliberately has no apply/repair mode. Exit codes: 0 = no detected data
blockers, 2 = owner decisions required, 1 = inspection failed. A zero exit code
does not certify backup restoration, migration rehearsal or deployment safety.

The report contains schema, aggregates and IDs, not credentials or student names.
"""

import argparse
import hashlib
import json
import runpy
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_TABLES = (
    "exams",
    "exam_sessions",
    "directions",
    "students",
    "examinators",
    "admins",
    "auth_users",
    "admin_role_users",
    "Allratings",
    "rating_recalc_jobs",
    "schema_migrations",
)


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def normalized_name(value):
    return str(value or "").strip().casefold()


def direction_suggestions(exams, directions):
    by_name = {}
    for direction in directions:
        by_name.setdefault(normalized_name(direction["name"]), []).append(
            direction["id"]
        )
    result = []
    for exam in exams:
        matches = by_name.get(normalized_name(exam["name"]), [])
        result.append(
            {
                "examId": exam["id"],
                "legacyName": exam["name"],
                "candidateDirectionIds": matches,
                "suggestedDirectionId": matches[0] if len(matches) == 1 else None,
                "status": (
                    "exact_match"
                    if len(matches) == 1
                    else "ambiguous" if matches else "unmatched"
                ),
            }
        )
    return result


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def collect_report(connection, pilot_student_id=None):
    """Inspect one consistent InnoDB snapshot. Always roll back and close cursor."""
    cursor = connection.cursor(dictionary=True)
    try:
        # Set the connection's transaction default as well as the actual snapshot
        # read-only, so an accidental future persistent write fails server-side.
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("SET SESSION MAX_EXECUTION_TIME=15000")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")

        server = _rows(
            cursor,
            """
            SELECT VERSION() AS version, @@sql_mode AS sqlMode,
                   @@character_set_database AS charset,
                   @@collation_database AS collation, @@time_zone AS timeZone,
                   @@max_allowed_packet AS maxAllowedPacket
        """,
        )[0]
        schema = {}
        for table in SCHEMA_TABLES:
            metadata = _rows(
                cursor,
                """
                SELECT TABLE_NAME AS tableName, ENGINE AS engine
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
            """,
                (table,),
            )
            if metadata:
                # The identifier comes exclusively from the fixed allowlist.
                definition = _rows(cursor, f"SHOW CREATE TABLE `{table}`")[0]
                schema[table] = {**metadata[0], "ddl": definition["Create Table"]}

        required = set(SCHEMA_TABLES) - {"schema_migrations", "admin_role_users"}
        if not required.issubset(schema):
            raise ValueError(
                "Required legacy tables are missing; no migration attempted"
            )

        counts = _rows(
            cursor,
            """
            SELECT (SELECT COUNT(*) FROM exams) AS exams,
                   (SELECT COUNT(*) FROM exam_sessions) AS results,
                   (SELECT COUNT(*) FROM students) AS students,
                   (SELECT COUNT(*) FROM examinators) AS examinators,
                   (SELECT COUNT(*) FROM Allratings) AS ratings
        """,
        )[0]
        exams = _rows(cursor, "SELECT id,name,date FROM exams ORDER BY id")
        directions = _rows(cursor, "SELECT id,name FROM directions ORDER BY id")
        duplicates = _rows(
            cursor,
            """
            SELECT es.id,es.exam_id AS examId,es.student_id AS studentId,
                   es.val AS points,es.points AS grade
            FROM exam_sessions es
            JOIN (SELECT exam_id,student_id FROM exam_sessions
                  GROUP BY exam_id,student_id HAVING COUNT(*)>1) d
              ON d.exam_id=es.exam_id AND d.student_id=es.student_id
            ORDER BY es.exam_id,es.student_id,es.id
        """,
        )
        invalid_grades = _rows(
            cursor,
            """
            SELECT id,exam_id AS examId,student_id AS studentId,points AS grade
            FROM exam_sessions
            WHERE points IS NULL OR points<0 OR points>5 OR points<>FLOOR(points)
            ORDER BY id
        """,
        )
        # CAST from DOUBLE to decimal text is checked with Decimal below, so no
        # epsilon silently authorizes truncating genuine >2-decimal input.
        point_values = _rows(
            cursor,
            "SELECT id,CAST(val AS CHAR) AS points FROM exam_sessions ORDER BY id",
        )
        invalid_points = []
        for row in point_values:
            value = Decimal(row["points"]) if row["points"] is not None else None
            if (
                value is None
                or not value.is_finite()
                or value < 0
                or value > Decimal("9999999999.99")
                or value != value.quantize(Decimal("0.01"))
            ):
                invalid_points.append(row)

        orphan_results = _rows(
            cursor,
            """
            SELECT es.id,es.exam_id AS examId,es.student_id AS studentId,
                   (e.id IS NULL) AS missingExam,(s.id IS NULL) AS missingStudent
            FROM exam_sessions es
            LEFT JOIN exams e ON e.id=es.exam_id
            LEFT JOIN students s ON s.id=es.student_id
            WHERE e.id IS NULL OR s.id IS NULL ORDER BY es.id
        """,
        )
        duplicate_ratings = _rows(
            cursor,
            """
            SELECT student_id AS studentId,COUNT(*) AS count
            FROM Allratings GROUP BY student_id HAVING COUNT(*)>1 ORDER BY student_id
        """,
        )
        active_jobs = _rows(
            cursor,
            """
            SELECT id,status FROM rating_recalc_jobs
            WHERE status IN ('running','queued') ORDER BY id
        """,
        )

        # Exam/result checksums detect later edits; names are never copied into
        # this result report. This is a fingerprint, NOT a recoverable backup.
        fingerprints = {}
        for table, query in (
            ("exams", "SELECT id,name,date FROM exams ORDER BY id"),
            (
                "exam_sessions",
                "SELECT id,exam_id,student_id,val,points,examinator FROM exam_sessions ORDER BY id",
            ),
        ):
            digest = hashlib.sha256()
            cursor.execute(query)
            for row in cursor:
                digest.update(
                    json.dumps(
                        row, ensure_ascii=False, sort_keys=True, default=json_value
                    ).encode("utf-8")
                )
                digest.update(b"\n")
            fingerprints[table] = digest.hexdigest()

        pilot = None
        if pilot_student_id is not None:
            candidates = _rows(
                cursor,
                """
                SELECT s.id AS studentId,
                    (SELECT COUNT(*) FROM auth_users a
                     WHERE a.role='student' AND a.ref_id=s.id) AS accountCount,
                    (SELECT COUNT(*) FROM exam_sessions es
                     WHERE es.student_id=s.id) AS existingResultCount
                FROM students s WHERE s.id=%s
            """,
                (pilot_student_id,),
            )
            pilot = (
                candidates[0]
                if candidates
                else {"studentId": pilot_student_id, "exists": False}
            )

        suggestions = direction_suggestions(exams, directions)
        blockers = []
        for code, values in (
            ("duplicate_exam_results", duplicates),
            ("orphan_exam_results", orphan_results),
            ("invalid_grades", invalid_grades),
            ("invalid_points", invalid_points),
            ("duplicate_ratings", duplicate_ratings),
            (
                "unresolved_direction_mapping",
                [s for s in suggestions if s["status"] != "exact_match"],
            ),
            ("active_rating_jobs", active_jobs),
        ):
            if values:
                blockers.append(code)
        warnings = []
        if not any(
            mode in server["sqlMode"].split(",")
            for mode in ("STRICT_TRANS_TABLES", "STRICT_ALL_TABLES")
        ):
            warnings.append("migration_connection_must_enable_strict_sql_mode")
        if not counts["examinators"]:
            warnings.append("no_examinators_for_classic_pilot")
        if server["maxAllowedPacket"] <= 32 * 1024 * 1024:
            warnings.append("verify_packet_headroom_for_16MiB_json_payloads")
        return {
            "reportVersion": 1,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only",
            "databaseModified": False,
            "dataPreflightPassed": not blockers,
            "migrationAuthorizedByReport": False,
            "blockers": blockers,
            "warnings": warnings,
            "server": server,
            "schema": schema,
            "counts": counts,
            "fingerprints": fingerprints,
            "directionSuggestions": suggestions,
            "duplicateResults": duplicates,
            "orphanResults": orphan_results,
            "invalidGrades": invalid_grades,
            "invalidPoints": invalid_points,
            "duplicateRatings": duplicate_ratings,
            "activeRatingJobs": active_jobs,
            "pilotStudent": pilot,
            "notVerified": [
                "restorable_backup",
                "migration_rehearsal",
                "legacy_api_compatibility",
                "deployment_readiness",
            ],
        }
    finally:
        try:
            connection.rollback()
        finally:
            cursor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-student-id", type=int)
    args = parser.parse_args()
    if args.pilot_student_id is not None and args.pilot_student_id <= 0:
        parser.error("--pilot-student-id must be positive")
    connection = None
    try:
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
        report = collect_report(connection, args.pilot_student_id)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=json_value))
        return 0 if report["dataPreflightPassed"] else 2
    except Exception as exc:
        # Connector errors can contain host/user/query contents. Do not emit their
        # message or traceback, and never echo the loaded config.
        print(
            json.dumps(
                {
                    "mode": "read_only",
                    "inspectionFailed": True,
                    "errorType": type(exc).__name__,
                    "errno": getattr(exc, "errno", None),
                }
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
