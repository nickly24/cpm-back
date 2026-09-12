"""Offline tests: importing/running these tests never connects to any database."""

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/exams_migration_preflight.py"
SPEC = importlib.util.spec_from_file_location("exams_migration_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class FakeCursor:
    def __init__(self, fail=False):
        self.queries = []
        self.result = []
        self.closed = False
        self.fail = fail

    def execute(self, sql, params=()):
        query = " ".join(sql.split())
        self.queries.append((query, params))
        if self.fail and query.startswith("SELECT VERSION"):
            raise RuntimeError("simulated read error")
        if query.startswith("SET ") or query.startswith("START TRANSACTION"):
            self.result = []
        elif query.startswith("SELECT VERSION"):
            self.result = [
                {
                    "version": "8.0.22",
                    "sqlMode": "",
                    "charset": "utf8",
                    "collation": "utf8_general_ci",
                    "timeZone": "SYSTEM",
                    "maxAllowedPacket": 16777216,
                }
            ]
        elif "FROM information_schema.TABLES" in query:
            self.result = (
                []
                if params[0] == "schema_migrations"
                else [{"tableName": params[0], "engine": "InnoDB"}]
            )
        elif query.startswith("SHOW CREATE TABLE"):
            self.result = [
                {"Create Table": "CREATE TABLE example (id INT PRIMARY KEY)"}
            ]
        elif query.startswith("SELECT (SELECT COUNT(*) FROM exams)"):
            self.result = [
                {
                    "exams": 1,
                    "results": 3,
                    "students": 1,
                    "examinators": 0,
                    "ratings": 1,
                }
            ]
        elif query == "SELECT id,name,date FROM exams ORDER BY id":
            self.result = [
                {"id": 53, "name": "Теория государства", "date": "2026-07-07"}
            ]
        elif query == "SELECT id,name FROM directions ORDER BY id":
            self.result = [{"id": 6, "name": "Теория государства"}]
        elif "JOIN (SELECT exam_id,student_id" in query:
            self.result = [
                {"id": 1, "examId": 53, "studentId": 2, "points": 12, "grade": 5},
                {"id": 2, "examId": 53, "studentId": 2, "points": 5, "grade": 4},
            ]
        elif "CAST(val AS CHAR)" in query:
            self.result = [
                {"id": 1, "points": "12"},
                {"id": 2, "points": "0.001"},
                {"id": 3, "points": "9999999999999999999999999999999999"},
            ]
        elif "(e.id IS NULL) AS missingExam" in query:
            self.result = [
                {
                    "id": 3,
                    "examId": 53,
                    "studentId": 9,
                    "missingExam": 0,
                    "missingStudent": 1,
                }
            ]
        elif query.startswith("SELECT s.id AS studentId"):
            self.result = [
                {"studentId": params[0], "accountCount": 1, "existingResultCount": 0}
            ]
        else:
            self.result = []

    def fetchall(self):
        return self.result

    def __iter__(self):
        return iter(self.result)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail=False):
        self.reader = FakeCursor(fail)
        self.rolled_back = False

    def cursor(self, dictionary):
        assert dictionary is True
        return self.reader

    def rollback(self):
        self.rolled_back = True


class PreflightTests(unittest.TestCase):
    def test_normalized_unique_direction_only(self):
        result = preflight.direction_suggestions(
            [{"id": 1, "name": "  ТЕОРИЯ ПРАВА "}],
            [{"id": 5, "name": "Теория права"}],
        )
        self.assertEqual(result[0]["suggestedDirectionId"], 5)
        self.assertEqual(result[0]["status"], "exact_match")

    def test_ambiguous_direction_is_never_chosen(self):
        result = preflight.direction_suggestions(
            [{"id": 1, "name": "Право"}],
            [{"id": 5, "name": "Право"}, {"id": 6, "name": " ПРАВО "}],
        )
        self.assertIsNone(result[0]["suggestedDirectionId"])
        self.assertEqual(result[0]["status"], "ambiguous")

    def test_unmatched_is_never_fuzzy_mapped(self):
        result = preflight.direction_suggestions(
            [{"id": 1, "name": "Теория права"}],
            [{"id": 7, "name": "Теория государства и права"}],
        )
        self.assertEqual(result[0]["status"], "unmatched")

    def test_report_flags_bad_data_without_choosing_grade(self):
        connection = FakeConnection()
        report = preflight.collect_report(connection, 2081)
        self.assertEqual(
            report["blockers"],
            ["duplicate_exam_results", "orphan_exam_results", "invalid_points"],
        )
        self.assertEqual([row["grade"] for row in report["duplicateResults"]], [5, 4])
        self.assertEqual([row["id"] for row in report["invalidPoints"]], [2, 3])
        self.assertFalse(report["databaseModified"])
        self.assertFalse(report["migrationAuthorizedByReport"])
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.reader.closed)

    def test_inspection_uses_only_read_only_snapshot_and_queries(self):
        connection = FakeConnection()
        preflight.collect_report(connection)
        queries = [q for q, _ in connection.reader.queries]
        self.assertEqual(queries[0], "SET SESSION TRANSACTION READ ONLY")
        self.assertIn("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY", queries)
        self.assertTrue(
            all(
                q.startswith(
                    (
                        "SELECT ",
                        "SHOW CREATE TABLE ",
                        "SET SESSION ",
                        "START TRANSACTION ",
                    )
                )
                for q in queries
            )
        )
        self.assertFalse(
            any("password" in q.lower() or "full_name" in q.lower() for q in queries)
        )

    def test_failed_inspection_rolls_back_and_closes(self):
        connection = FakeConnection(fail=True)
        with self.assertRaises(RuntimeError):
            preflight.collect_report(connection)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.reader.closed)

    def test_decimal_serialization_keeps_precision(self):
        self.assertEqual(preflight.json_value(Decimal("1.23")), "1.23")


if __name__ == "__main__":
    unittest.main()
