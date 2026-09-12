"""Always-on exam regression tests: mocked boundaries, no DB/network/factory."""

from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from flask import Flask
from cpm_back.auth.admin_permissions import enforce_admin_permissions
from cpm_back.blueprints.exams_v2_bp import exams_v2_bp
from cpm_back.blueprints.examiner_exams_bp import examiner_exams_bp
from cpm_back.blueprints.student_exams_bp import student_exams_bp
from cpm_back.blueprints.exam_imports_bp import exam_imports_bp
from cpm_back.services.exams import rating

REMOVED_FLAGS = (
    "EXAMS_V2_ENABLED",
    "CLASSIC_EXAM_CREATION_ENABLED",
    "CLASSIC_EXAM_COMMANDS_ENABLED",
    "STUDENT_EXAM_RESULTS_V2_ENABLED",
    "RATING_EXAMS_V2_ENABLED",
)


class ExamAlwaysEnabledTests(unittest.TestCase):
    def setUp(self):
        # Stale deployment variables/config must not disable exam functionality.
        environment = patch.dict(os.environ, {name: "false" for name in REMOVED_FLAGS})
        environment.start()
        self.addCleanup(environment.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY="offline-always-on-secret")
        self.app.config.update({name: False for name in REMOVED_FLAGS})
        self.app.before_request(enforce_admin_permissions)
        for blueprint in (
            exams_v2_bp,
            examiner_exams_bp,
            student_exams_bp,
            exam_imports_bp,
        ):
            self.app.register_blueprint(blueprint)
        self.client = self.app.test_client()
        no_database = patch(
            "cpm_back.db.mysql_pool.get_db_connection",
            side_effect=AssertionError(
                "No database connection permitted in this suite"
            ),
        )
        no_database.start()
        self.addCleanup(no_database.stop)
        no_mongo = patch(
            "cpm_back.db.mongo.get_mongo_db",
            side_effect=AssertionError("No Mongo connection permitted in this suite"),
        )
        no_mongo.start()
        self.addCleanup(no_mongo.stop)

    def request_as(self, actor, method, path, **kwargs):
        with patch("cpm_back.auth.jwt_auth.get_current_user", return_value=actor):
            return self.client.open(path, method=method, **kwargs)

    def capabilities(self, actor):
        response = self.request_as(actor, "GET", "/api/exams/capabilities")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["data"]["apiVersion"], "v2")
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json["data"]

    def test_config_no_longer_defines_rollout_flags_even_when_env_is_false(self):
        # Load config independently, without reloading shared app modules.
        location = Path(__file__).resolve().parents[1] / "cpm_back/config.py"
        spec = importlib.util.spec_from_file_location(
            "_exam_always_on_config_test", location
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in REMOVED_FLAGS:
            with self.subTest(flag=name):
                self.assertFalse(hasattr(module.Config, name))
                self.assertFalse(hasattr(module.config, name))

    def test_admin_capabilities_ignore_all_false_flags(self):
        caps = self.capabilities({"id": 1, "role": "admin"})
        for field in ("canReadAdminExams", "canManageOutside", "canCreateClassic"):
            self.assertTrue(caps[field], field)
        self.assertFalse(caps["canConductClassic"])
        self.assertFalse(caps["canReadStudentResults"])

    def test_examiner_and_student_capabilities_still_follow_actor_role(self):
        for role, enabled_field in (
            ("examinator", "canConductClassic"),
            ("student", "canReadStudentResults"),
        ):
            with self.subTest(role=role):
                caps = self.capabilities({"id": 42, "role": role})
                for field in (
                    "canReadAdminExams",
                    "canManageOutside",
                    "canCreateClassic",
                    "canConductClassic",
                    "canReadStudentResults",
                ):
                    self.assertEqual(caps[field], field == enabled_field, field)

    def test_delegated_admin_capabilities_preserve_view_vs_edit(self):
        actor = {
            "id": 2,
            "role": "staff_admin",
            "permissions": {"exams": {"view": True}},
        }
        caps = self.capabilities(actor)
        self.assertTrue(caps["canReadAdminExams"])
        self.assertFalse(caps["canManageOutside"])
        self.assertFalse(caps["canCreateClassic"])
        actor["permissions"]["exams"] = {"edit": True}
        caps = self.capabilities(actor)
        self.assertTrue(caps["canReadAdminExams"])
        self.assertTrue(caps["canManageOutside"])
        self.assertTrue(caps["canCreateClassic"])

    def test_anonymous_access_remains_unauthorized(self):
        for path in (
            "/api/exams/capabilities",
            "/api/exams",
            "/api/examiner/exams",
            "/api/student/exams/results",
        ):
            with self.subTest(path=path):
                response = self.request_as(None, "GET", path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json["error"], "unauthorized")

    def test_wrong_roles_and_missing_admin_permissions_remain_forbidden(self):
        for actor, path in (
            ({"id": 1, "role": "student"}, "/api/exams"),
            ({"id": 1, "role": "admin"}, "/api/examiner/exams"),
            ({"id": 1, "role": "examinator"}, "/api/student/exams/results"),
            ({"id": 1, "role": "staff_admin", "permissions": {}}, "/api/exams"),
        ):
            with self.subTest(role=actor["role"], path=path):
                response = self.request_as(actor, "GET", path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json["error"], "forbidden")

    def test_mutations_still_require_bearer_and_admin_edit_permission(self):
        payload = {"examType": "classic", "directionId": 6}
        response = self.request_as(
            {"id": 1, "role": "admin"}, "POST", "/api/exams", json=payload
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "bearer_token_required")
        actor = {
            "id": 2,
            "role": "staff_admin",
            "permissions": {"exams": {"view": True}},
        }
        response = self.request_as(
            actor,
            "POST",
            "/api/exams",
            json=payload,
            headers={"Authorization": "Bearer synthetic"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["error"], "forbidden")

    def test_classic_creation_is_not_blocked_by_stale_false_flags(self):
        with patch(
            "cpm_back.blueprints.exams_v2_bp.admin_mutation",
            return_value=({"exam": {"id": 10}}, 201),
        ) as mutation:
            response = self.request_as(
                {"id": 1, "role": "admin"},
                "POST",
                "/api/exams",
                json={"examType": "classic", "directionId": 6},
                headers={"Authorization": "Bearer synthetic"},
            )
        self.assertEqual(response.status_code, 201, response.json)
        mutation.assert_called_once()

    def test_read_routes_reach_domain_despite_false_flags(self):
        for role, path, boundary, service in (
            ("admin", "/api/exams", "exams_v2_bp", "admin.list_exams"),
            (
                "examinator",
                "/api/examiner/exams",
                "examiner_exams_bp",
                "lifecycle.examiner_exams",
            ),
            (
                "student",
                "/api/student/exams/results",
                "student_exams_bp",
                "results.student_list",
            ),
        ):
            with self.subTest(role=role):
                marker = object()
                with patch(
                    f"cpm_back.blueprints.{boundary}.transaction",
                    return_value=nullcontext(marker),
                ), patch(
                    f"cpm_back.services.exams.{service}", return_value={"items": []}
                ) as domain:
                    response = self.request_as({"id": 42, "role": role}, "GET", path)
                self.assertEqual(response.status_code, 200, response.json)
                domain.assert_called_once()
                self.assertIs(domain.call_args.args[0], marker)
                if role == "student":
                    self.assertEqual(domain.call_args.args[1], 42)

    def test_examiner_commands_remain_available_and_keep_actor_identity(self):
        actor = {"id": 42, "role": "examinator"}
        payload = {"presentedQuestionId": 5, "roundId": 8, "value": 0.5}
        with patch(
            "cpm_back.blueprints.examiner_exams_bp.transaction",
            return_value=nullcontext(object()),
        ), patch(
            "cpm_back.services.exams.lifecycle.command",
            return_value={"attempt": {"id": 3}},
        ) as command:
            response = self.request_as(
                actor,
                "POST",
                "/api/examiner/attempts/3/vote",
                json=payload,
                headers={
                    "Authorization": "Bearer synthetic",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
            )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(command.call_args.args[1:5], (3, "vote", payload, actor))

    def test_import_still_requires_both_exam_and_upload_permissions(self):
        actor = {
            "id": 2,
            "role": "staff_admin",
            "permissions": {"exams": {"edit": True}},
        }
        path = "/api/exams/10/imports/questions/parse"
        headers = {"Authorization": "Bearer synthetic"}
        response = self.request_as(actor, "POST", path, headers=headers)
        self.assertEqual(response.status_code, 403)
        actor["permissions"]["upload"] = {"edit": True}
        response = self.request_as(actor, "POST", path, headers=headers)
        # Passing permissions reaches ordinary file validation, not rollout 503.
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "file_required")

    def test_rating_helper_and_freshness_ignore_false_flags(self):
        with self.app.app_context():
            self.assertTrue(rating.enabled())
            with patch.object(
                rating, "transaction", return_value=nullcontext(object())
            ), patch.object(
                rating, "freshness", return_value={"scope": "exams", "isStale": False}
            ) as freshness:
                result = rating.freshness_response()
        freshness.assert_called_once()
        self.assertEqual(
            result, {"ratingFreshness": {"scope": "exams", "isStale": False}}
        )
        self.assertTrue(rating.enabled())


if __name__ == "__main__":
    unittest.main()
