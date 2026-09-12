"""Delegated-admin removal cannot orphan role-scoped examination audit."""

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import uuid

from tests import test_classic_exam_results as fixture


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local MySQL socket not configured",
)
class StaffDeleteGuardTests(unittest.TestCase):
    setUp = fixture.ClassicResultTests.setUp
    tearDown = fixture.ClassicResultTests.tearDown
    query = fixture.ClassicResultTests.query
    attempt = fixture.ClassicResultTests.attempt

    @contextmanager
    def service(self):
        pool = types.ModuleType("cpm_back.db.mysql_pool")
        pool.get_db_connection = lambda: self.connection
        pool.close_db_connection = lambda connection: connection.rollback()
        permissions = types.ModuleType("cpm_back.auth.admin_permissions")
        permissions.SECTIONS = {"exams": "Экзамены"}
        with patch.dict(
            sys.modules,
            {
                "cpm_back.db.mysql_pool": pool,
                "cpm_back.auth.admin_permissions": permissions,
            },
        ):
            path = (
                Path(__file__).resolve().parents[1]
                / "cpm_back/services/admin_access.py"
            )
            spec = importlib.util.spec_from_file_location(
                "_staff_delete_guard_test_service", path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            yield module

    def staff_auth(self):
        self.db.execute(
            "INSERT INTO auth_users(ref_id,role,username) VALUES(1,'staff_admin','staff')"
        )
        self.connection.commit()

    def test_referenced_staff_receipt_blocks_before_auth_delete(self):
        self.staff_auth()
        self.db.insert(
            "exam_admin_commands",
            {
                "actor_role": "staff_admin",
                "actor_id": 1,
                "idempotency_key": str(uuid.uuid4()),
                "command_type": "test",
                "request_hash": "0" * 64,
                "expires_at": "2099-01-01 00:00:00",
            },
        )
        self.connection.commit()
        with self.service() as service:
            with self.assertRaises(service.ExamReferencedUserError) as error:
                service.delete_user(1)
            self.assertEqual(error.exception.code, "user_referenced_by_exam")
        self.assertEqual(
            self.db.scalar(
                "SELECT COUNT(*) FROM auth_users WHERE role='staff_admin' AND ref_id=1"
            ),
            1,
        )
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM admin_role_users WHERE id=1"), 1
        )

    def test_all_three_import_session_owners_are_protected(self):
        self.staff_auth()
        for table in (
            "classic_exam_question_import_sessions",
            "classic_exam_assignment_import_sessions",
            "outside_exam_result_import_sessions",
        ):
            with self.subTest(table=table):
                self.db.insert(
                    table,
                    {
                        "exam_id": self.exam_id,
                        "created_by_role": "staff_admin",
                        "created_by": 1,
                        "source_filename": "test.xlsx",
                        "preview_payload": "[]",
                        "expires_at": "2099-01-01 00:00:00",
                    },
                )
                self.connection.commit()
                with self.service() as service:
                    with self.assertRaises(service.ExamReferencedUserError):
                        service.delete_user(1)
                self.db.execute(f"DELETE FROM {table}")
                self.connection.commit()

    def test_assignment_and_appeal_role_identity_blocks_staff_delete(self):
        self.staff_auth()
        attempt = self.attempt()
        self.db.execute(
            "UPDATE classic_exam_assignments SET created_by_role='staff_admin',created_by=1"
        )
        self.connection.commit()
        with self.service() as service:
            with self.assertRaises(service.ExamReferencedUserError):
                service.delete_user(1)
        self.db.execute("UPDATE classic_exam_assignments SET created_by_role='admin'")
        self.db.insert(
            "classic_exam_appeals",
            {
                "attempt_id": attempt,
                "previous_grade": 4,
                "new_grade": 5,
                "changed_by_role": "staff_admin",
                "changed_by_admin_id": 1,
                "admin_name_snapshot": "Snapshot",
            },
        )
        self.connection.commit()
        with self.service() as service:
            with self.assertRaises(service.ExamReferencedUserError):
                service.delete_user(1)

    def test_same_numeric_admin_id_does_not_block_unreferenced_staff_delete(self):
        self.staff_auth()
        self.db.insert(
            "exam_admin_commands",
            {
                "actor_role": "admin",
                "actor_id": 1,
                "idempotency_key": str(uuid.uuid4()),
                "command_type": "test",
                "request_hash": "0" * 64,
                "expires_at": "2099-01-01 00:00:00",
            },
        )
        self.connection.commit()
        with self.service() as service:
            self.assertTrue(service.delete_user(1)["status"])
        self.assertEqual(
            self.db.scalar(
                "SELECT COUNT(*) FROM auth_users WHERE role='staff_admin' AND ref_id=1"
            ),
            0,
        )
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM admin_role_users WHERE id=1"), 0
        )
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM admins WHERE id=1"), 1)


if __name__ == "__main__":
    unittest.main()
