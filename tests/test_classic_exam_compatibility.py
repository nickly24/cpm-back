"""Offline boundary tests and explicitly local legacy compatibility checks."""

import os
import unittest
from unittest.mock import patch

from flask import Flask
from cpm_back.auth.admin_permissions import allowed, enforce_admin_permissions
from cpm_back.blueprints.exams_v2_bp import exams_v2_bp
from cpm_back.blueprints.exam_imports_bp import exam_imports_bp
from cpm_back.services.exam import get_exams
from cpm_back.services.serv.delete_user import delete_user
from tests import test_exams_migrations as migration_fixture

migration = migration_fixture.migration


class ExamBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY="offline-exam-secret")
        self.app.before_request(enforce_admin_permissions)
        self.app.register_blueprint(exams_v2_bp)
        self.app.register_blueprint(exam_imports_bp)
        self.client = self.app.test_client()
        no_io = patch(
            "cpm_back.db.mysql_pool.get_db_connection",
            side_effect=AssertionError("No database in boundary suite"),
        )
        no_io.start()
        self.addCleanup(no_io.stop)

    def test_method_aware_policy_and_both_import_permissions(self):
        user = {
            "role": "staff_admin",
            "id": 1,
            "permissions": {"exams": {"view": True}},
        }
        for method, expected in [
            ("GET", True),
            ("HEAD", True),
            ("POST", False),
            ("PATCH", False),
            ("DELETE", False),
        ]:
            with self.app.test_request_context(method=method):
                self.assertEqual(allowed(user, "exams_v2.exams_collection"), expected)
                self.assertFalse(allowed(user, "exam_imports.parse"))
        user["permissions"]["exams"]["edit"] = True
        with self.app.test_request_context(method="POST"):
            self.assertTrue(allowed(user, "exams_v2.exams_collection"))
            self.assertFalse(allowed(user, "exam_imports.parse"))
            user["permissions"]["upload"] = {"edit": True}
            self.assertTrue(allowed(user, "exam_imports.parse"))

    def test_denied_mutations_do_not_reach_database(self):
        user = {
            "role": "staff_admin",
            "id": 1,
            "permissions": {"exams": {"view": True}},
        }
        with patch("cpm_back.auth.jwt_auth.get_current_user", return_value=user):
            response = self.client.post(
                "/api/exams",
                json={"examType": "classic", "directionId": 6},
                headers={"Authorization": "Bearer synthetic"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["error"], "forbidden")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(
            response.json["correlationId"], response.headers["X-Correlation-ID"]
        )

    def test_capability_gateway_and_bearer_requirement(self):
        with patch("cpm_back.auth.jwt_auth.get_current_user", return_value=None):
            self.assertEqual(
                self.client.get("/api/exams/capabilities").status_code, 401
            )
        with patch(
            "cpm_back.auth.jwt_auth.get_current_user",
            return_value={"id": 1, "role": "admin"},
        ):
            caps = self.client.get("/api/exams/capabilities").json["data"]
            self.assertTrue(caps["canReadAdminExams"])
            self.assertTrue(caps["canCreateClassic"])
            response = self.client.post("/api/exams", json={})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json["error"], "bearer_token_required")
            with patch("cpm_back.blueprints.exams_v2_bp.transaction") as transaction:
                with patch(
                    "cpm_back.services.exams.admin.list_exams",
                    return_value={"items": []},
                ):
                    self.assertEqual(self.client.get("/api/exams").status_code, 200)
                transaction.assert_called_once()

    def test_real_factory_registration_cors_and_uniform_unknown_path(self):
        import cpm_back

        with patch("cpm_back.services.exams.maintenance.start_retention_worker"), patch(
            "cpm_back.init_mysql_pool"
        ), patch("cpm_back.init_mongo"), patch(
            "cpm_back.services.exam.rating_recalc_jobs.recover_stale_rating_jobs"
        ), patch(
            "cpm_back.services.user_import.import_jobs.recover_stale_user_import_jobs"
        ), patch(
            "cpm_back.services.telegram_bot.start_bot_if_configured"
        ):
            app = cpm_back.create_app()
        client = app.test_client()
        self.assertEqual(client.get("/").json["examMode"], "always_on")
        response = client.options(
            "/api/exams/1",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "Authorization,Idempotency-Key,X-Exam-Confirmation",
            },
        )
        self.assertEqual(response.status_code, 204)
        self.assertIn(
            "Idempotency-Key", response.headers["Access-Control-Allow-Headers"]
        )
        unknown = client.get("/api/exams/no-such-route")
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json["error"], "not_found")
        self.assertEqual(
            unknown.json["correlationId"], unknown.headers["X-Correlation-ID"]
        )
        self.assertIn("no-store", unknown.headers["Cache-Control"])


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local socket opt-in required",
)
class ExamLegacyMySQLTests(unittest.TestCase):
    query = migration_fixture.LocalMySQLMigrationTests.query
    tearDown = migration_fixture.LocalMySQLMigrationTests.tearDown

    def setUp(self):
        migration_fixture.LocalMySQLMigrationTests.setUp(self)
        migration.run_migrations(self.connection, "all", True, {53: 6})
        self.query(
            "CREATE TABLE auth_users(id INT PRIMARY KEY,role VARCHAR(20),ref_id INT)"
        )
        self.query(
            "CREATE TABLE student_credentials(student_id INT PRIMARY KEY,password VARCHAR(30))"
        )
        self.query("INSERT INTO auth_users VALUES(1,'student',2081)")
        self.query("INSERT INTO student_credentials VALUES(2081,'local-test-password')")

    def connect(self):
        import mysql.connector

        return mysql.connector.connect(
            unix_socket=os.environ["EXAM_MIGRATION_TEST_SOCKET"],
            user="root",
            password="",
            database=self.database,
            autocommit=False,
        )

    def test_legacy_numeric_contract_name_join_and_classic_filter(self):
        self.query(
            "INSERT INTO exams(exam_type,direction_id,name,date) VALUES('classic',6,NULL,NULL)"
        )
        self.query("UPDATE directions SET name='Переименовано' WHERE id=6")
        with patch.object(get_exams, "get_db_connection", side_effect=self.connect):
            exams = get_exams.get_all_exams()
            self.assertTrue(exams["status"])
            self.assertEqual(len(exams["exams"]), 1)
            self.assertEqual(exams["exams"][0]["name"], "Переименовано")
            result = get_exams.get_exam_sessions_by_student_paginated(2081)
            self.assertTrue(result["status"], result)
            self.assertEqual(result["summary"]["totalPoints"], 7.25)
            self.assertIsInstance(result["sessions"][0]["points"], float)
            self.assertEqual(result["sessions"][0]["grade"], 4)

    def test_user_refusal_preserves_credentials_and_auth(self):
        with patch(
            "cpm_back.services.serv.delete_user.get_db_connection",
            side_effect=self.connect,
        ):
            result = delete_user("student", 2081)
        self.assertEqual(result["http_status"], 409)
        self.assertEqual(
            self.query("SELECT password FROM student_credentials")[0]["password"],
            "local-test-password",
        )
        self.assertEqual(len(self.query("SELECT * FROM auth_users")), 1)
        self.assertEqual(len(self.query("SELECT * FROM students")), 1)

    def test_failure_after_entity_delete_rolls_back_credentials(self):
        self.query("DELETE FROM exam_sessions")
        self.query(
            "CREATE TABLE auth_restrict(id INT PRIMARY KEY,auth_id INT,FOREIGN KEY(auth_id) REFERENCES auth_users(id))"
        )
        self.query("INSERT INTO auth_restrict VALUES(1,1)")
        with patch(
            "cpm_back.services.serv.delete_user.get_db_connection",
            side_effect=self.connect,
        ):
            result = delete_user("student", 2081)
        self.assertEqual(result["http_status"], 409)
        self.assertEqual(len(self.query("SELECT * FROM students")), 1)
        self.assertEqual(len(self.query("SELECT * FROM auth_users")), 1)
        self.assertEqual(len(self.query("SELECT * FROM student_credentials")), 1)

    def test_legacy_delete_invalidates_rating_and_rejects_classic(self):
        self.query(
            "INSERT INTO exams(id,exam_type,direction_id,name,date) VALUES(9999,'classic',6,NULL,NULL)"
        )
        before = self.query("SELECT source_revision FROM rating_source_state")[0][
            "source_revision"
        ]
        with patch.object(get_exams, "get_db_connection", side_effect=self.connect):
            with self.assertRaisesRegex(ValueError, "use_classic_exam_api"):
                get_exams.delete_exam(9999)
            self.assertEqual(get_exams.delete_exam(53)["sessionsDeleted"], 1)
        self.assertEqual(
            self.query("SELECT source_revision FROM rating_source_state")[0][
                "source_revision"
            ],
            before + 1,
        )
        self.assertEqual(len(self.query("SELECT * FROM exams WHERE id=9999")), 1)


if __name__ == "__main__":
    unittest.main()
