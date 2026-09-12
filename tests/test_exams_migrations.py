"""Offline tests by default; explicit local socket enables disposable MySQL tests.

EXAM_MIGRATION_TEST_SOCKET=/absolute/local/mysql.sock python -m unittest
tests.test_exams_migrations. This never reads project DB credentials. Each local
integration test creates and drops only its own cpm_exam_schema_test_* database.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "exams_migrations", ROOT / "scripts/exams_migrations.py"
)
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


class MigrationPackageTests(unittest.TestCase):
    def test_phases_are_complete_and_separate(self):
        additive = migration.load_migrations("additive")
        hardening = migration.load_migrations("hardening")
        self.assertEqual(len(additive), 42)
        self.assertEqual(len(hardening), 8)
        self.assertEqual(len({s.migration for s in additive}), 12)
        self.assertTrue(
            all(s.migration.startswith(("026_", "027_")) for s in hardening)
        )

    def test_offline_plan_never_loads_credentials(self):
        with patch.object(
            migration,
            "connection_parameters",
            side_effect=AssertionError("No connection allowed"),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(migration.main(["--plan", "--phase", "all"]), 0)
            self.assertFalse(json.loads(output.getvalue())["databaseConnected"])

    def test_apply_requires_backup_before_loading_credentials(self):
        with patch.object(
            migration,
            "connection_parameters",
            side_effect=AssertionError("No connection allowed"),
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(migration.main(["--apply"]), 2)

    def test_hardening_requires_compatible_code(self):
        with patch.object(
            migration,
            "connection_parameters",
            side_effect=AssertionError("No connection allowed"),
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    migration.main(
                        ["--apply", "--backup-verified", "--phase", "hardening"]
                    ),
                    2,
                )

    def test_actor_identity_is_role_scoped_without_admin_fk(self):
        definitions = "\n".join(s.sql for s in migration.load_migrations("all"))
        self.assertNotIn("REFERENCES admins", definitions)
        self.assertIn("actor_role,actor_id,idempotency_key", definitions)
        self.assertIn("changed_by_role VARCHAR(20)", definitions)
        self.assertIn("created_by_role VARCHAR(20)", definitions)

    def test_additive_never_coerces_legacy_grades_or_deletes(self):
        definitions = "\n".join(s.sql for s in migration.load_migrations("additive"))
        self.assertNotIn("MODIFY COLUMN points", definitions)
        self.assertNotIn("MODIFY COLUMN val", definitions)
        self.assertNotIn("fk_exam_sessions_student", definitions)
        self.assertNotRegex(definitions, r"\bDELETE FROM\b|\bDROP TABLE\b|\bTRUNCATE\b")

    def test_immutable_bank_has_no_mutable_source_fk(self):
        step = next(
            s
            for s in migration.load_migrations()
            if s.id == "classic_exam_definition_questions"
        )
        self.assertNotIn("REFERENCES classic_exam_questions", step.sql)
        self.assertNotIn("REFERENCES classic_exam_parts", step.sql)
        self.assertEqual(step.metadata["columns"]["question_text"]["type"], "text")

    def test_all_legacy_foreign_ids_are_signed_int(self):
        for step in migration.load_migrations("all"):
            for name, definition in step.metadata.get("columns", {}).items():
                if name in (
                    "exam_id",
                    "student_id",
                    "examinator_id",
                    "actor_examinator_id",
                    "replaced_by_examinator_id",
                    "job_id",
                ):
                    self.assertEqual(definition["type"], "int", (step.id, name))

    def test_index_descriptions_include_uniqueness_and_order(self):
        step = next(
            s for s in migration.load_migrations() if s.id == "classic_exam_attempts"
        )
        indexes = migration.declared_indexes(step.sql)
        self.assertEqual(
            indexes["uq_classic_attempt_student_no"],
            (False, ["exam_id", "student_id", "attempt_no"]),
        )
        self.assertEqual(indexes["PRIMARY"], (False, ["id"]))
        self.assertEqual(
            indexes["ix_classic_attempt_exam_status"],
            (True, ["exam_id", "status", "id"]),
        )

    def test_direction_map_does_not_guess_ids(self):
        for value in (
            {"examDirectionMap": {"1": True}},
            {"examDirectionMap": {"01": 5}},
            {"examDirectionMap": {"1": "5"}},
        ):
            with patch.object(Path, "read_text", return_value=json.dumps(value)):
                with self.assertRaises(migration.MigrationError):
                    migration.load_direction_map("reviewed.json")
        with patch.object(
            Path, "read_text", return_value='{"examDirectionMap":{"53":6}}'
        ):
            self.assertEqual(migration.load_direction_map("reviewed.json"), {53: 6})


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local MySQL socket not configured",
)
class LocalMySQLMigrationTests(unittest.TestCase):
    def setUp(self):
        import mysql.connector

        socket = os.environ["EXAM_MIGRATION_TEST_SOCKET"]
        if not Path(socket).is_absolute():
            raise AssertionError(
                "Integration socket must be an explicit absolute local path"
            )
        self.database = "cpm_exam_schema_test_" + uuid.uuid4().hex[:16]
        self.connection = mysql.connector.connect(
            unix_socket=socket, user="root", password="", autocommit=False
        )
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(
            "CREATE DATABASE "
            + migration.identifier(self.database)
            + " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.execute("USE " + migration.identifier(self.database))
        schema = json.loads(
            (
                ROOT
                / "docs/classic-exams-implementation/2026-09-12-prod-preflight.json"
            ).read_text()
        )["schema"]
        for table in migration.REQUIRED_BASE_TABLES:
            cursor.execute(schema[table]["ddl"])
        cursor.execute(
            "INSERT INTO directions(id,name) VALUES(6,'Тестовое направление')"
        )
        cursor.execute(
            "INSERT INTO students(id,full_name,class,tg_name) VALUES(2081,'Тестовый студент',10,'')"
        )
        cursor.execute(
            "INSERT INTO exams(id,name,date) VALUES(53,'Тестовое направление','2026-09-01')"
        )
        cursor.execute(
            "INSERT INTO exam_sessions(id,exam_id,student_id,val,points,examinator) VALUES(100,53,2081,7.25,4,'Тестовый экзаменатор')"
        )
        cursor.execute(
            "INSERT INTO Allratings(student_id,exams,homework,tests,final) VALUES(2081,4,60,80,75)"
        )
        self.connection.commit()
        cursor.close()

    def tearDown(self):
        try:
            self.connection.rollback()
            cursor = self.connection.cursor()
            # Only the unique disposable schema created by this test may drop.
            assert self.database.startswith("cpm_exam_schema_test_")
            cursor.execute("DROP DATABASE " + migration.identifier(self.database))
            cursor.close()
        finally:
            self.connection.close()

    def query(self, sql, params=()):
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            result = cursor.fetchall() if cursor.with_rows else None
            self.connection.commit()
            return result
        finally:
            cursor.close()

    def test_read_only_check_and_full_apply_resume(self):
        report = migration.run_migrations(self.connection)
        self.assertFalse(report["fullyApplied"])
        self.assertEqual(self.query("SHOW TABLES LIKE 'schema_migrations'"), [])
        additive = migration.run_migrations(self.connection, apply=True)
        self.assertTrue(additive["fullyApplied"])
        self.assertEqual(len(additive["steps"]), 42)
        self.assertEqual(
            self.query("SELECT val,points FROM exam_sessions WHERE id=100"),
            [{"val": 7.25, "points": 4.0}],
        )
        hardening = migration.run_migrations(
            self.connection, "hardening", True, {53: 6}
        )
        self.assertTrue(hardening["fullyApplied"])
        self.assertEqual(
            self.query("SELECT direction_id FROM exams WHERE id=53"),
            [{"direction_id": 6}],
        )
        self.assertTrue(
            migration.run_migrations(self.connection, "all", False, {53: 6})[
                "fullyApplied"
            ]
        )
        self.assertTrue(
            migration.run_migrations(self.connection, "additive", True)["fullyApplied"]
        )
        self.assertTrue(
            migration.run_migrations(self.connection, "hardening", True, {53: 6})[
                "fullyApplied"
            ]
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) AS n FROM schema_migrations"), [{"n": 14}]
        )
        self.assertEqual(
            self.query("SELECT source_revision FROM rating_source_state"),
            [{"source_revision": 1}],
        )
        self.query(
            "UPDATE exam_sessions SET examinator=%s WHERE id=100", ("Преподаватель 🎓",)
        )
        self.assertEqual(
            self.query("SELECT examinator FROM exam_sessions WHERE id=100"),
            [{"examinator": "Преподаватель 🎓"}],
        )

    def test_crash_after_ddl_resumes_only_with_matching_journal(self):
        migration.run_migrations(self.connection, apply=True)
        self.query(
            "UPDATE exam_schema_migration_steps SET status='started',completed_at=NULL WHERE step_id='classic_exam_parts'"
        )
        self.query(
            "DELETE FROM schema_migrations WHERE version='018_classic_exam_question_bank.sql'"
        )
        self.assertTrue(
            migration.run_migrations(self.connection, apply=True)["fullyApplied"]
        )
        self.assertEqual(
            self.query(
                "SELECT status FROM exam_schema_migration_steps WHERE step_id='classic_exam_parts'"
            ),
            [{"status": "done"}],
        )

    def test_schema_drift_is_not_silently_adopted(self):
        migration.run_migrations(self.connection, apply=True)
        self.query(
            "ALTER TABLE classic_exam_questions DROP INDEX uq_classic_question_content"
        )
        with self.assertRaisesRegex(migration.MigrationError, "no longer satisfies"):
            migration.run_migrations(self.connection, apply=True)

    def test_unjournalled_existing_target_is_refused(self):
        first = migration.load_migrations()[0]
        self.query(first.sql)
        with self.assertRaisesRegex(migration.MigrationError, "Unjournalled"):
            migration.run_migrations(self.connection, apply=True)

    def test_invalid_grade_stops_hardening_without_coercion(self):
        migration.run_migrations(self.connection, apply=True)
        self.query("UPDATE exam_sessions SET points=6 WHERE id=100")
        with self.assertRaisesRegex(
            migration.MigrationError, "Data precondition failed"
        ):
            migration.run_migrations(self.connection, "hardening", True, {53: 6})
        self.assertEqual(
            self.query("SELECT points FROM exam_sessions WHERE id=100"),
            [{"points": 6.0}],
        )

    def test_checksum_mismatch_stops_before_any_ddl(self):
        migration.run_migrations(self.connection, apply=True)
        self.query(
            "UPDATE schema_migrations SET checksum=REPEAT('0',64) WHERE version='015_exam_core.sql'"
        )
        with self.assertRaisesRegex(migration.MigrationError, "edited"):
            migration.run_migrations(self.connection, apply=True)


if __name__ == "__main__":
    unittest.main()
