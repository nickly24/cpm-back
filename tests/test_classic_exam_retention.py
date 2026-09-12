"""Expiry maintenance tests: bounded deletes and live-history preservation."""

from datetime import timedelta
import hashlib
import importlib
import os
import unittest
from unittest.mock import patch
import uuid

from flask import Flask
from tests import test_classic_exam_results as fixture

common = fixture.common
maintenance = importlib.import_module(fixture.DOMAIN + ".maintenance")


class RetentionWorkerTests(unittest.TestCase):
    def test_worker_starts_without_rollout_configuration(self):
        app = Flask(__name__)
        with patch.object(maintenance.threading, "Thread") as thread:
            handle = maintenance.start_retention_worker(app)
            self.assertIsNotNone(handle)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            maintenance.stop_retention_worker(app)

    def test_start_is_idempotent_and_stop_is_explicit(self):
        app = Flask(__name__)
        app.config["EXAMS_V2_ENABLED"] = False
        with patch.object(maintenance.threading, "Thread") as thread:
            thread.return_value.is_alive.return_value = True
            first = maintenance.start_retention_worker(app)
            self.assertIs(first, maintenance.start_retention_worker(app))
            self.assertEqual(thread.call_count, 1)
            maintenance.stop_retention_worker(app)
            self.assertTrue(first["stop"].is_set())

    def test_running_worker_ignores_stale_false_flag(self):
        app = Flask(__name__)
        app.config["EXAMS_V2_ENABLED"] = False
        with patch.object(maintenance.threading, "Thread") as thread, patch.object(
            maintenance.threading, "Event"
        ) as event, patch(
            "cpm_back.db.mysql_pool.get_db_connection"
        ) as connection, patch(
            "cpm_back.db.mysql_pool.close_db_connection"
        ) as close, patch.object(
            maintenance, "sweep_expired", return_value={"deleted": {}, "batches": 0}
        ) as sweep:
            event.return_value.is_set.side_effect = [False, True]
            maintenance.start_retention_worker(app)
            thread.call_args.kwargs["target"]()
            sweep.assert_called_once()
            close.assert_called_once_with(connection.return_value)


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local MySQL socket not configured",
)
class LocalRetentionTests(unittest.TestCase):
    setUp = fixture.ClassicResultTests.setUp
    tearDown = fixture.ClassicResultTests.tearDown
    query = fixture.ClassicResultTests.query
    attempt = fixture.ClassicResultTests.attempt
    presented = fixture.ClassicResultTests.presented

    def preview(self, table, expired, exam_id=None, status="editable"):
        exam_id = self.exam_id if exam_id is None else exam_id
        expiry = common.now() + timedelta(hours=-1 if expired else 1)
        return self.db.insert(
            table,
            {
                "exam_id": exam_id,
                "created_by_role": "admin",
                "created_by": 1,
                "source_filename": "synthetic.xlsx",
                "preview_payload": "[]",
                "expires_at": expiry,
                "status": status,
                "commit_result": "{}" if status == "committed" else None,
            },
        )

    def command(self, expired=True):
        return self.db.insert(
            "exam_admin_commands",
            {
                "actor_role": "admin",
                "actor_id": 1,
                "idempotency_key": str(uuid.uuid4()),
                "command_type": "test",
                "request_hash": "0" * 64,
                "target_exam_id": self.exam_id,
                "expires_at": common.now() + timedelta(hours=-1 if expired else 1),
            },
        )

    def second_connection(self):
        import mysql.connector

        return mysql.connector.connect(
            unix_socket=os.environ["EXAM_MIGRATION_TEST_SOCKET"],
            user="root",
            password="",
            database=self.database,
            autocommit=False,
        )

    def test_dry_run_performs_no_deletion(self):
        self.preview(maintenance.IMPORT_TABLES[0], True, 53)
        self.command(True)
        self.connection.commit()
        report = maintenance.sweep_expired(self.connection, dry_run=True)
        self.assertTrue(report["dryRun"])
        self.assertFalse(report["lockAcquired"])
        self.assertEqual(sum(report["expired"].values()), 2)
        self.assertEqual(sum(report["deleted"].values()), 0)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM exam_admin_commands"), 1)

    def test_only_expired_preview_and_admin_receipt_rows_are_removed(self):
        for table in maintenance.IMPORT_TABLES:
            self.preview(table, True)
            self.preview(table, True, status="committed")
            self.preview(table, False)
        self.command(True)
        self.command(False)
        attempt = self.attempt()
        self.presented(attempt)
        self.db.insert(
            "classic_exam_attempt_commands",
            {
                "attempt_id": attempt,
                "actor_examinator_id": 1,
                "idempotency_key": str(uuid.uuid4()),
                "command_type": "vote",
                "request_hash": "0" * 64,
                "receipt": "{}",
                "created_at": "2020-01-01 00:00:00",
            },
        )
        self.connection.commit()
        report = maintenance.sweep_expired(self.connection)
        self.assertTrue(report["lockAcquired"])
        self.assertEqual(sum(report["deleted"].values()), 7)
        for table in maintenance.IMPORT_TABLES:
            self.assertEqual(self.db.scalar(f"SELECT COUNT(*) FROM {table}"), 1)
        for table in (
            "classic_exam_attempts",
            "classic_exam_attempt_commands",
            "classic_exam_presented_questions",
        ):
            self.assertEqual(self.db.scalar(f"SELECT COUNT(*) FROM {table}"), 1)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM classic_exam_votes"), 2)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM exam_admin_commands"), 1)

    def test_row_budget_is_strict_and_next_sweep_continues(self):
        for _ in range(5):
            self.command(True)
        self.connection.commit()
        report = maintenance.sweep_expired(self.connection, batch_size=2, max_batches=1)
        self.assertEqual(report["deleted"]["exam_admin_commands"], 2)
        self.assertTrue(report["budgetExhausted"])
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM exam_admin_commands"), 3)
        report = maintenance.sweep_expired(
            self.connection, batch_size=2, max_batches=10
        )
        self.assertEqual(report["deleted"]["exam_admin_commands"], 3)

    def test_busy_parent_exam_is_skipped_before_child_lock(self):
        self.preview(maintenance.IMPORT_TABLES[1], True)
        self.connection.commit()
        other = self.second_connection()
        try:
            cursor = other.cursor()
            cursor.execute(
                "SELECT id FROM exams WHERE id=%s FOR UPDATE", (self.exam_id,)
            )
            cursor.fetchall()
            report = maintenance.sweep_expired(self.connection, max_seconds=2)
            self.assertGreater(report["skippedBusyExams"], 0)
            self.assertEqual(sum(report["deleted"].values()), 0)
            other.rollback()
            cursor.close()
            report = maintenance.sweep_expired(self.connection)
            self.assertEqual(sum(report["deleted"].values()), 1)
        finally:
            other.close()

    def test_another_worker_advisory_lock_prevents_duplicate_sweep(self):
        self.command(True)
        self.connection.commit()
        other = self.second_connection()
        lock_name = (
            "exam-retention:" + hashlib.sha256(self.database.encode()).hexdigest()[:32]
        )
        try:
            cursor = other.cursor()
            cursor.execute("SELECT GET_LOCK(%s,0)", (lock_name,))
            self.assertEqual(cursor.fetchone()[0], 1)
            report = maintenance.sweep_expired(self.connection)
            self.assertFalse(report["lockAcquired"])
            self.assertEqual(
                self.db.scalar("SELECT COUNT(*) FROM exam_admin_commands"), 1
            )
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            cursor.fetchall()
            cursor.close()
        finally:
            other.close()

    def test_locked_receipt_does_not_block_other_expired_receipts(self):
        first = self.command(True)
        self.command(True)
        self.connection.commit()
        other = self.second_connection()
        try:
            cursor = other.cursor()
            cursor.execute(
                "SELECT id FROM exam_admin_commands WHERE id=%s FOR UPDATE", (first,)
            )
            cursor.fetchall()
            report = maintenance.sweep_expired(
                self.connection, batch_size=10, max_seconds=2
            )
            self.assertEqual(report["deleted"]["exam_admin_commands"], 1)
            self.assertEqual(
                self.db.scalar("SELECT COUNT(*) FROM exam_admin_commands"), 1
            )
            other.rollback()
            cursor.close()
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
