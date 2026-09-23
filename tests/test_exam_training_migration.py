"""Offline guards and opt-in disposable local MySQL tests for migration029."""

import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from scripts import exam_training_migration as migration


class ExamTrainingMigrationGuardTests(unittest.TestCase):
    def test_offline_plan_and_backup_guard_do_not_load_credentials(self):
        with patch.object(migration, "connection_parameters", side_effect=AssertionError("No DB")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(migration.main(["--plan"]), 0)
            self.assertFalse(json.loads(output.getvalue())["databaseConnected"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(migration.main(["--apply"]), 2)

    def test_only_additive_schema_statements(self):
        self.assertEqual(len(migration.statements()), 6)
        self.assertNotRegex("\n".join(migration.statements()), r"\b(DELETE|DROP|TRUNCATE|UPDATE|INSERT)\b")


@unittest.skipUnless(os.environ.get("EXAM_MIGRATION_TEST_SOCKET"), "Explicit local socket required")
class ExamTrainingMigrationMySQLTests(unittest.TestCase):
    def setUp(self):
        import mysql.connector
        socket = os.environ["EXAM_MIGRATION_TEST_SOCKET"]
        self.assertTrue(Path(socket).is_absolute())
        self.connection = mysql.connector.connect(unix_socket=socket, user="root", password="", autocommit=False)
        self.database = "cpm_exam_training_schema_" + uuid.uuid4().hex[:12]
        self.query("CREATE DATABASE `" + self.database + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        self.query("USE `" + self.database + "`")
        self.query("CREATE TABLE exams(id INT PRIMARY KEY, name VARCHAR(100)) ENGINE=InnoDB")
        # Extract only the two original CREATE definitions; never execute 013.
        original = next((migration.ROOT / "migrations").glob("013_*.sql")).read_text()
        for table in ("student_card_progress", "student_section_study_settings"):
            ddl = "CREATE TABLE IF NOT EXISTS " + table + original.split("CREATE TABLE IF NOT EXISTS " + table, 1)[1].split(";", 1)[0]
            self.query(ddl)
        self.query("INSERT INTO exams VALUES (1,'Синтетический экзамен')")
        self.query("INSERT INTO student_card_progress(student_id,section_kind,section_ref_id,card_ref,content_fingerprint) VALUES(1,'manual','9','manual:9:1',%s),(1,'test','abc','test:abc:1',%s)", ("a" * 64, "b" * 64))
        self.query("INSERT INTO student_section_study_settings(student_id,section_kind,section_ref_id,batch_size,last_batch_index,study_mode) VALUES(1,'manual','9',20,2,'learned'),(1,'test','abc',30,1,'stale')")

    def tearDown(self):
        try:
            self.connection.rollback()
            assert self.database.startswith("cpm_exam_training_schema_")
            self.query("DROP DATABASE `" + self.database + "`")
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

    def test_apply_preserves_old_rows_new_defaults_and_is_idempotent(self):
        progress = self.query("SELECT * FROM student_card_progress ORDER BY id")
        settings = self.query("SELECT * FROM student_section_study_settings ORDER BY id")
        self.assertFalse(migration.run_migration(self.connection)["fullyApplied"])
        applied = migration.run_migration(self.connection, apply=True)
        self.assertTrue(applied["fullyApplied"])
        self.assertEqual(self.query("SELECT * FROM student_card_progress ORDER BY id"), progress)
        self.assertEqual(self.query("SELECT * FROM student_section_study_settings ORDER BY id"), [dict(row, last_part_id=None) for row in settings])
        self.assertEqual(self.query("SELECT training_enabled FROM exams"), [{"training_enabled": 0}])
        rerun = migration.run_migration(self.connection, apply=True)
        self.assertTrue(all(step["status"] == "verified" for step in rerun["steps"]))
        ref = "2147483647:9223372036854775807"
        self.query("INSERT INTO student_section_study_settings(student_id,section_kind,section_ref_id,last_part_id) VALUES(2,'exam',%s,9223372036854775807)", (ref,))
        self.assertEqual(self.query("SELECT section_ref_id FROM student_section_study_settings WHERE student_id=2")[0]["section_ref_id"], ref)

    def test_partial_apply_resumes_without_resetting_existing_flag(self):
        self.query(migration.statements()[0])
        self.query("UPDATE exams SET training_enabled=1")
        report = migration.run_migration(self.connection, apply=True)
        self.assertEqual(report["steps"][0]["status"], "verified")
        self.assertEqual(self.query("SELECT training_enabled FROM exams"), [{"training_enabled": 1}])

    def test_unexpected_late_column_refuses_before_any_ddl(self):
        self.query("ALTER TABLE student_section_study_settings MODIFY section_ref_id VARCHAR(25) NOT NULL")
        with self.assertRaises(migration.MigrationError):
            migration.run_migration(self.connection, apply=True)
        self.assertEqual(self.query("SHOW COLUMNS FROM exams LIKE 'training_enabled'"), [])
