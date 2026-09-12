"""Migration-scope backup tests; integration uses explicit disposable local MySQL.

No test loads production configuration or starts an application/worker.
"""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import uuid

from tests import test_exams_migrations as migration_fixture

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "exams_rehearsal_test_module", ROOT / "scripts/exams_rehearsal.py"
)
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)
migration = migration_fixture.migration


class BackupPackageTests(unittest.TestCase):
    def test_scope_covers_every_migrated_table_and_both_journals(self):
        touched = {step.metadata["table"] for step in migration.load_migrations("all")}
        expected = (
            touched
            | set(rehearsal.LEGACY_DATA_TABLES)
            | {"schema_migrations", "exam_schema_migration_steps"}
        )
        self.assertEqual(set(rehearsal.DATA_TABLES), expected)
        self.assertEqual(len(rehearsal.DATA_TABLES), len(expected))
        self.assertFalse(set(rehearsal.DATA_TABLES) & set(rehearsal.SUPPORT_TABLES))

    def test_dependency_order_does_not_follow_creation_migration_order(self):
        tables = {
            "classic_exam_settings": {
                "ddl": "CREATE TABLE `classic_exam_settings` (id INT, part_id INT, FOREIGN KEY(part_id) REFERENCES `classic_exam_parts`(id))"
            },
            "classic_exam_parts": {
                "ddl": "CREATE TABLE `classic_exam_parts` (id INT PRIMARY KEY, exam_id INT, FOREIGN KEY(exam_id) REFERENCES `exams`(id))"
            },
            "exams": {"ddl": "CREATE TABLE `exams` (id INT PRIMARY KEY)"},
        }
        self.assertEqual(
            rehearsal.restore_order(tables),
            ["exams", "classic_exam_parts", "classic_exam_settings"],
        )

    def test_unsafe_dependency_plans_are_rejected_before_ddl(self):
        invalid = (
            {
                "exams": {
                    "ddl": "CREATE TABLE `exams` (id INT, FOREIGN KEY(id) REFERENCES `missing`(id))"
                }
            },
            {
                "exams": {
                    "ddl": "CREATE TABLE `exams` (id INT, FOREIGN KEY(id) REFERENCES `production`.`exams`(id))"
                }
            },
            {
                "exams": {
                    "ddl": "CREATE TABLE `exams` (id INT, FOREIGN KEY(id) REFERENCES `exams`(id))"
                }
            },
            {"exams": {"ddl": "DROP TABLE `exams`"}},
            {"unrelated": {"ddl": "CREATE TABLE `unrelated` (id INT)"}},
        )
        for tables in invalid:
            with self.subTest(tables=tables), self.assertRaises(ValueError):
                rehearsal.restore_order(tables)

    def test_remote_restore_is_rejected_before_creating_a_cursor(self):
        with self.assertRaisesRegex(ValueError, "Local Unix socket required"):
            rehearsal.restore_local(
                types.SimpleNamespace(unix_socket=None), "cpm_exam_restore_test", {}
            )

    def test_private_backup_is_exclusive_and_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            data = {"formatVersion": 2, "tables": {}}
            self.assertEqual(rehearsal.save_private(path, data), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                rehearsal.save_private(path, data)
        with self.assertRaisesRegex(ValueError, "outside repository"):
            rehearsal.save_private(ROOT / "never-create-this-backup.json", {})

    def test_account_data_manifest_cannot_be_promoted_to_authentic(self):
        data = {
            "formatVersion": 2,
            "dataTables": ["auth_users"],
            "tables": {
                "auth_users": {
                    "ddl": "CREATE TABLE `auth_users` (id INT)",
                    "rows": [{"id": 1, "password": "not-real"}],
                }
            },
        }
        with self.assertRaisesRegex(ValueError, "manifest"):
            rehearsal.restore_local(
                types.SimpleNamespace(unix_socket="/local/socket"),
                "cpm_exam_restore_test",
                data,
            )


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local MySQL socket not configured",
)
class LocalBackupTests(unittest.TestCase):
    query = migration_fixture.LocalMySQLMigrationTests.query
    tearDown = migration_fixture.LocalMySQLMigrationTests.tearDown

    def setUp(self):
        from tests import test_classic_exam_results as fixture

        fixture.ClassicResultTests.setUp(self)
        self.attempt_id = fixture.ClassicResultTests.attempt(self)
        self.presented_id, self.round_id = fixture.ClassicResultTests.presented(
            self, self.attempt_id
        )
        db = self.db
        db.execute(
            "CREATE TABLE admin_roles(id INT PRIMARY KEY,name VARCHAR(100) NOT NULL)"
        )
        db.execute("INSERT INTO admin_roles VALUES(7,'PRIVATE_ROLE_NAME')")
        db.execute(
            "ALTER TABLE admin_role_users ADD role_id INT NOT NULL DEFAULT 7,ADD is_active TINYINT NOT NULL DEFAULT 1,ADD FOREIGN KEY(role_id) REFERENCES admin_roles(id)"
        )
        db.execute("ALTER TABLE auth_users ADD password TEXT")
        db.execute(
            "UPDATE auth_users SET password='PRIVATE_AUTH_PASSWORD',username=CONCAT('PRIVATE_LOGIN_',id)"
        )
        db.execute(
            "UPDATE students SET full_name='PRIVATE_STUDENT_NAME',tg_name='PRIVATE_STUDENT_CONTACT' WHERE id=2081"
        )
        db.execute("UPDATE admins SET full_name='PRIVATE_ADMIN_NAME'")
        db.execute("UPDATE admin_role_users SET full_name='PRIVATE_STAFF_NAME'")
        db.execute(
            "CREATE TABLE student_credentials(student_id INT PRIMARY KEY,password_hash TEXT,FOREIGN KEY(student_id) REFERENCES students(id))"
        )
        db.execute(
            "INSERT INTO student_credentials VALUES(2081,'PRIVATE_STUDENT_PASSWORD')"
        )
        part = db.insert(
            "classic_exam_parts",
            {
                "exam_id": self.exam_id,
                "code": "A",
                "question_weight": 5,
                "question_count": 1,
                "sort_order": 1,
            },
        )
        db.execute(
            "UPDATE classic_exam_settings SET tie_breaker_part_id=%s WHERE exam_id=%s",
            (part, self.exam_id),
        )
        db.insert(
            "classic_exam_questions",
            {
                "exam_id": self.exam_id,
                "part_id": part,
                "question_text": "Подлинный текст вопроса 📚",
                "answer_text": "Подлинный ответ",
                "content_hash": "1" * 64,
                "sort_order": 1,
            },
        )
        db.insert(
            "classic_exam_grade_thresholds",
            {"exam_id": self.exam_id, "grade": 0, "min_score": 0},
        )
        commission = db.insert(
            "classic_exam_commissions",
            {"exam_id": self.exam_id, "name": "Комиссия из архива"},
        )
        db.insert(
            "classic_exam_commission_members",
            {"commission_id": commission, "examinator_id": 1, "position": 1},
        )
        db.insert(
            "classic_exam_student_privileges",
            {"exam_id": self.exam_id, "student_id": 2081, "replacement_limit": 2},
        )
        db.insert(
            "classic_exam_attempt_part_progress",
            {
                "attempt_id": self.attempt_id,
                "source_part_id": 7,
                "part_code": "A",
                "question_weight": 5,
                "required_count": 1,
                "consensus_count": 1,
            },
        )
        db.insert(
            "classic_exam_attempt_question_usage",
            {
                "attempt_id": self.attempt_id,
                "definition_question_id": self.question_id,
                "last_cycle_no": 1,
                "ever_presented": 1,
            },
        )
        db.insert(
            "classic_exam_attempt_commands",
            {
                "attempt_id": self.attempt_id,
                "actor_examinator_id": 1,
                "idempotency_key": str(uuid.uuid4()),
                "command_type": "vote",
                "request_hash": "2" * 64,
                "receipt": '{"roundId": 1, "value": 0.5}',
            },
        )
        db.insert(
            "classic_exam_appeals",
            {
                "attempt_id": self.attempt_id,
                "previous_grade": 4,
                "new_grade": 5,
                "changed_by_role": "staff_admin",
                "changed_by_admin_id": 1,
                "admin_name_snapshot": "Подлинный снимок имени",
            },
        )
        for table in (
            "classic_exam_question_import_sessions",
            "classic_exam_assignment_import_sessions",
            "outside_exam_result_import_sessions",
        ):
            db.insert(
                table,
                {
                    "exam_id": self.exam_id if table.startswith("classic") else 53,
                    "created_by_role": "staff_admin",
                    "created_by": 1,
                    "source_filename": "fixture.xlsx",
                    "preview_payload": '{"rows": [{"line": 2}], "version": 3}',
                    "status": "committed",
                    "commit_result": '{"created": 1}',
                    "expires_at": "2026-09-19 00:00:00",
                },
            )
        db.insert(
            "exam_admin_commands",
            {
                "actor_role": "staff_admin",
                "actor_id": 1,
                "idempotency_key": str(uuid.uuid4()),
                "command_type": "appeal",
                "request_hash": "3" * 64,
                "receipt": '{"attemptId": 1}',
                "expires_at": "2026-09-19 00:00:00",
            },
        )
        job = db.insert(
            "rating_recalc_jobs",
            {
                "status": "completed",
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "message": "Архивный пересчёт",
            },
        )
        db.insert(
            "rating_recalc_staging",
            {
                "job_id": job,
                "student_id": 2081,
                "exams": 4,
                "homework": 60,
                "tests": 80,
                "final": 75,
                "details_json": '{"items": [{"examId": 53, "grade": 4}]}',
            },
        )
        db.execute(
            "UPDATE Allratings SET details_json=%s,calculation_job_id=%s",
            ('{"items": [{"examId": 53, "grade": 4}]}', job),
        )
        db.execute(
            "UPDATE rating_source_state SET source_revision=8,calculated_revision=7"
        )
        self.connection.commit()

    def restore_connection(self):
        import mysql.connector

        connection = mysql.connector.connect(
            unix_socket=os.environ["EXAM_MIGRATION_TEST_SOCKET"],
            user="root",
            autocommit=False,
        )
        schema = "cpm_exam_schema_test_restore_" + uuid.uuid4().hex[:12]

        def cleanup():
            connection.rollback()
            cursor = connection.cursor()
            cursor.execute("DROP DATABASE IF EXISTS " + rehearsal.identifier(schema))
            cursor.close()
            connection.close()

        self.addCleanup(cleanup)
        return connection, schema

    def archive(self):
        return json.loads(
            json.dumps(
                rehearsal.snapshot(self.connection),
                default=rehearsal.serialize,
                ensure_ascii=False,
            )
        )

    def test_all_populated_tables_restore_and_hardening_resumes_from_copied_journals(
        self,
    ):
        before = self.archive()
        self.assertEqual(set(before["dataTables"]), set(rehearsal.DATA_TABLES))
        self.assertTrue(
            all(before["tables"][table]["rows"] for table in rehearsal.DATA_TABLES)
        )
        serialized = json.dumps(before, ensure_ascii=False)
        for private in (
            "PRIVATE_AUTH_PASSWORD",
            "PRIVATE_LOGIN_",
            "PRIVATE_STUDENT_NAME",
            "PRIVATE_STUDENT_CONTACT",
            "PRIVATE_ADMIN_NAME",
            "PRIVATE_STAFF_NAME",
            "PRIVATE_ROLE_NAME",
            "PRIVATE_STUDENT_PASSWORD",
        ):
            self.assertNotIn(private, serialized)
        self.assertIn("Подлинный снимок имени", serialized)
        self.assertEqual(
            before["tables"]["rating_recalc_staging"]["primaryKey"],
            ["job_id", "student_id"],
        )
        self.assertEqual(
            before["tables"]["exam_schema_migration_steps"]["primaryKey"],
            ["migration", "step_id"],
        )
        connection, schema = self.restore_connection()
        report = rehearsal.restore_local(connection, schema, before)
        self.assertEqual(set(report["restoredAndCompared"]), set(rehearsal.DATA_TABLES))
        restored = rehearsal.snapshot(connection)
        self.assertEqual(
            restored["tables"]["admin_role_users"]["fixtureRows"],
            [{"id": 1, "role_id": 7}],
        )
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS n FROM auth_users")
        self.assertEqual(cursor.fetchone()["n"], 0)
        cursor.execute("SELECT COUNT(*) AS n FROM student_credentials")
        self.assertEqual(cursor.fetchone()["n"], 0)
        cursor.execute("SELECT is_active FROM admin_role_users WHERE id=1")
        self.assertEqual(cursor.fetchone()["is_active"], 0)
        cursor.close()
        connection.rollback()
        self.assertTrue(migration.run_migrations(connection)["fullyApplied"])
        self.assertTrue(
            migration.run_migrations(
                connection, "hardening", apply=True, direction_map={53: 6}
            )["fullyApplied"]
        )
        self.assertTrue(
            migration.run_migrations(connection, "all", direction_map={53: 6})[
                "fullyApplied"
            ]
        )
        # The backup source was neither backfilled nor hardened by restoration.
        self.assertIsNone(
            self.query("SELECT direction_id FROM exams WHERE id=53")[0]["direction_id"]
        )
        self.assertEqual(
            self.query("SELECT source_revision FROM rating_source_state WHERE id=1")[0][
                "source_revision"
            ],
            8,
        )

    def test_post_hardening_backup_preserves_decimal_unicode_and_schema_checks(self):
        migration.run_migrations(
            self.connection, "hardening", apply=True, direction_map={53: 6}
        )
        self.query(
            "UPDATE exam_sessions SET examinator=%s WHERE id=100", ("Я" * 254 + "📚",)
        )
        before = self.archive()
        self.assertEqual(before["tables"]["exam_sessions"]["rows"][0]["val"], "7.25")
        connection, schema = self.restore_connection()
        rehearsal.restore_local(connection, schema, before)
        self.assertTrue(
            migration.run_migrations(connection, "all", direction_map={53: 6})[
                "fullyApplied"
            ]
        )

    def test_bad_child_row_rolls_back_all_data_without_disabling_foreign_keys(self):
        before = self.archive()
        before["tables"]["classic_exam_votes"]["rows"][0]["examinator_id"] = 99999
        connection, schema = self.restore_connection()
        with self.assertRaises(Exception):
            rehearsal.restore_local(connection, schema, before)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT @@foreign_key_checks AS enabled,(SELECT COUNT(*) FROM exams) AS rows_kept"
        )
        self.assertEqual(cursor.fetchone(), {"enabled": 1, "rows_kept": 0})
        cursor.close()
        connection.rollback()
        with self.assertRaisesRegex(ValueError, "must be empty"):
            rehearsal.restore_local(connection, schema, self.archive())

    def test_future_domain_table_fails_closed_instead_of_silently_omitting_data(self):
        self.query("CREATE TABLE classic_exam_future_records(id INT PRIMARY KEY)")
        with self.assertRaisesRegex(ValueError, "extend backup scope"):
            rehearsal.snapshot(self.connection)

    def test_old_v1_legacy_archive_still_restores(self):
        before = self.archive()
        # Version1 was produced before additive and had only five authentic data
        # tables plus empty support schemas. Recreate that exact legacy subset.
        before["formatVersion"] = 1
        before.pop("dataTables")
        before["tables"] = {
            table: content
            for table, content in before["tables"].items()
            if table in rehearsal.LEGACY_DATA_TABLES
            or table in rehearsal.SUPPORT_TABLES
        }
        for table, content in before["tables"].items():
            content.pop("fixtureRows")
            content.pop("primaryKey")
            content.pop("dataMode")
            # Old helper never created role/admin_role_user fixtures.
            if table in ("admin_roles", "admin_role_users"):
                content["fixtureIds"] = []
        connection, schema = self.restore_connection()
        report = rehearsal.restore_local(connection, schema, before)
        self.assertEqual(
            set(report["restoredAndCompared"]), set(rehearsal.LEGACY_DATA_TABLES)
        )


if __name__ == "__main__":
    unittest.main()
