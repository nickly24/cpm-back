"""Result/protocol tests use only explicitly opted-in disposable local MySQL.

No test calls create_app or loads a configured database connection.
"""

from contextlib import contextmanager
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import uuid

from flask import Flask
from tests import test_exams_migrations as migration_fixture

migration = migration_fixture.migration
# Load only the pure domain package. Importing cpm_back's app package eagerly
# imports unrelated legacy services (some require a newer Python interpreter).
DOMAIN = "_classic_results_test_domain"
package = types.ModuleType(DOMAIN)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "cpm_back/services/exams")
]
sys.modules[DOMAIN] = package
common = importlib.import_module(DOMAIN + ".common")
results = importlib.import_module(DOMAIN + ".results")


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local MySQL socket not configured",
)
class ClassicResultTests(unittest.TestCase):
    query = migration_fixture.LocalMySQLMigrationTests.query
    tearDown = migration_fixture.LocalMySQLMigrationTests.tearDown

    def setUp(self):
        migration_fixture.LocalMySQLMigrationTests.setUp(self)
        migration.run_migrations(self.connection, apply=True)
        self.db = common.UnitOfWork(self.connection)
        self.addCleanup(self.db.cursor.close)
        self.db.execute(
            "CREATE TABLE admins(id INT PRIMARY KEY,full_name VARCHAR(255))"
        )
        self.db.execute(
            "CREATE TABLE admin_role_users(id INT PRIMARY KEY,full_name VARCHAR(255))"
        )
        self.db.execute(
            "CREATE TABLE auth_users(id INT AUTO_INCREMENT PRIMARY KEY,ref_id INT,role VARCHAR(20),username VARCHAR(50))"
        )
        self.db.execute("INSERT INTO admins VALUES(1,'Администратор')")
        self.db.execute(
            "INSERT INTO admin_role_users VALUES(1,'Делегированный администратор')"
        )
        self.db.execute(
            "INSERT INTO examinators(id,full_name) VALUES(1,'Экзаменатор 1'),(2,'Экзаменатор 2'),(3,'Экзаменатор 3')"
        )
        self.db.execute(
            "INSERT INTO auth_users(ref_id,role,username) VALUES(1,'examinator','exam1'),(2,'examinator','exam2'),(3,'examinator','exam3'),(2081,'student','student')"
        )
        self.exam_id = self.db.insert(
            "exams",
            {"name": None, "date": None, "exam_type": "classic", "direction_id": 6},
        )
        self.db.insert(
            "classic_exam_settings",
            {"exam_id": self.exam_id, "start_at": common.now(), "end_at": common.now()},
        )
        self.admin = {"role": "admin", "id": 1}
        self.student = {"role": "student", "id": 2081}
        self.definition_id = self.db.insert(
            "classic_exam_definition_versions",
            {
                "exam_id": self.exam_id,
                "config_version": 1,
                "config_json": common.dumps(
                    {
                        "parts": [
                            {"sourcePartId": 7, "code": "A", "weight": 5, "quota": 1}
                        ],
                        "thresholds": [
                            {"grade": grade, "minScore": grade} for grade in range(6)
                        ],
                        "startAt": "2026-09-12T10:00:00+03:00",
                        "endAt": "2026-09-12T18:00:00+03:00",
                    }
                ),
            },
        )
        self.question_id = self.db.insert(
            "classic_exam_definition_questions",
            {
                "definition_id": self.definition_id,
                "source_question_id": 10,
                "source_part_id": 7,
                "part_code": "A",
                "question_text": "Вопрос?",
                "answer_text": "Ответ.",
            },
        )
        self.app = Flask(__name__)
        self.app.config.update(
            JWT_SECRET_KEY="local-test-signing-key-only",
        )
        self.connection.commit()

    def attempt(self, number=1, status="completed", grade=4, members=(1, 2)):
        assignment = self.db.insert(
            "classic_exam_assignments",
            {
                "exam_id": self.exam_id,
                "student_id": 2081,
                "attempt_no": number,
                "created_by_role": "admin",
                "created_by": 1,
                "commission_name_snapshot": "Комиссия",
            },
        )
        attempt_id = self.db.insert(
            "classic_exam_attempts",
            {
                "exam_id": self.exam_id,
                "student_id": 2081,
                "assignment_id": assignment,
                "definition_id": self.definition_id,
                "attempt_no": number,
                "status": status,
                "phase": "completed" if status == "completed" else "regular_questions",
                "history_generation": 1,
                "student_name_snapshot": "Студент",
                "raw_total": Decimal("4.0"),
                "rounded_total": 4,
                "max_score": 5,
                "calculated_grade": grade if status == "completed" else None,
                "completed_at": common.now() if status == "completed" else None,
                "published_at": common.now() if status == "completed" else None,
            },
        )
        for position, examiner_id in enumerate(members, 1):
            data = {
                "examinator_id": examiner_id,
                "position": position,
                "full_name_snapshot": "Экзаменатор " + str(examiner_id),
            }
            self.db.insert(
                "classic_exam_assignment_members", dict(data, assignment_id=assignment)
            )
            self.db.insert(
                "classic_exam_attempt_members", dict(data, attempt_id=attempt_id)
            )
        self.connection.commit()
        return attempt_id

    def presented(self, attempt_id, status="consensus", round_status="consensus"):
        question = self.db.insert(
            "classic_exam_presented_questions",
            {
                "attempt_id": attempt_id,
                "definition_question_id": self.question_id,
                "sequence_no": 1,
                "purpose": "regular",
                "cycle_no": 1,
                "status": status,
                "consensus_value": 1 if status == "consensus" else None,
                "weighted_score": 5 if status == "consensus" else None,
            },
        )
        round_id = self.db.insert(
            "classic_exam_vote_rounds",
            {
                "attempt_id": attempt_id,
                "presented_question_id": question,
                "round_no": 1,
                "status": round_status,
                "consensus_value": 1 if round_status == "consensus" else None,
            },
        )
        for examiner in (1, 2):
            self.db.insert(
                "classic_exam_votes",
                {"round_id": round_id, "examinator_id": examiner, "value": 1},
            )
        self.connection.commit()
        return question, round_id

    def test_student_reads_own_current_and_history_with_grade_filter_before_page(self):
        first = self.attempt(1, grade=5)
        second = self.attempt(2, "in_progress")
        self.assertEqual(
            results.effective_result(self.db, self.exam_id, 2081)["id"], first
        )
        self.assertEqual(
            results.student_detail(self.db, self.exam_id, 2081)["current"]["grade"], 5
        )
        self.db.update(
            "classic_exam_attempts",
            {"status": "completed", "phase": "completed", "calculated_grade": 0},
            "id=%s",
            (second,),
        )
        self.connection.commit()
        current = results.student_list(
            self.db, 2081, {"type": "classic", "grade": "0", "limit": "1"}
        )
        self.assertEqual(current["pagination"]["total"], 1)
        self.assertEqual(current["items"][0]["currentAttempt"]["attemptId"], second)
        detail = results.student_detail(self.db, self.exam_id, 2081)
        self.assertEqual(detail["history"][0]["attemptId"], first)
        self.assertNotIn("calculatedGrade", detail["current"])
        self.assertNotIn("resultVersion", detail["current"])
        self.assertEqual(
            results.student_list(self.db, 2081, {"type": "classic", "grade": "5"})[
                "items"
            ],
            [],
        )
        with self.assertRaises(common.ExamError) as error:
            results.student_detail(self.db, self.exam_id, 99)
        self.assertEqual(error.exception.status, 404)

    def test_protocol_redacts_other_open_votes_and_all_student_votes(self):
        first = self.attempt()
        presented, _ = self.presented(first, "open", "open")
        examiner = results.rounds(
            self.db, first, presented, {}, {"role": "examinator", "id": 1}, self.exam_id
        )
        self.assertEqual(
            [v["examinatorId"] for v in examiner["items"][0]["votes"]], [1]
        )
        admin = results.rounds(self.db, first, presented, {}, self.admin, self.exam_id)
        self.assertEqual(len(admin["items"][0]["votes"]), 2)
        student = results.questions(self.db, first, {}, self.student, self.exam_id)
        self.assertEqual(student["items"][0]["weight"], 5)
        self.assertNotIn("votes", common.dumps(student))
        self.assertNotIn("round", common.dumps(student))
        with self.assertRaises(common.ExamError):
            results.questions(
                self.db, first, {}, {"role": "examinator", "id": 3}, self.exam_id
            )
        with self.assertRaises(common.ExamError):
            results.rounds(self.db, first, presented, {}, self.student, self.exam_id)

    def test_same_grade_appeal_keeps_fact_and_historical_appeal_does_not_dirty_rating(
        self,
    ):
        first = self.attempt(1, grade=4)
        second = self.attempt(2, grade=3)
        before = self.db.scalar("SELECT source_revision FROM rating_source_state")
        historical = results.appeal(
            self.db,
            self.exam_id,
            first,
            {"grade": 4, "expectedResultVersion": 1},
            self.admin,
        )
        self.connection.commit()
        self.assertFalse(historical["isCurrentAttempt"])
        self.assertTrue(historical["hasAppeal"])
        self.assertEqual(
            self.db.scalar("SELECT source_revision FROM rating_source_state"), before
        )
        current = results.appeal(
            self.db,
            self.exam_id,
            second,
            {"grade": 3, "expectedResultVersion": 1},
            {"role": "staff_admin", "id": 1},
        )
        self.connection.commit()
        self.assertTrue(current["isCurrentAttempt"])
        self.assertEqual(
            current["appeal"]["changedBy"]["fullName"], "Делегированный администратор"
        )
        self.assertEqual(
            self.db.scalar(
                "SELECT state_version FROM classic_exam_attempts WHERE id=%s", (second,)
            ),
            2,
        )
        self.assertEqual(
            self.db.scalar("SELECT source_revision FROM rating_source_state"),
            before + 1,
        )
        with self.assertRaises(common.ExamError) as error:
            results.appeal(
                self.db,
                self.exam_id,
                second,
                {"grade": 5, "expectedResultVersion": 1},
                self.admin,
            )
        self.assertEqual(error.exception.code, "result_modified")

    def test_history_delete_cascades_and_preserves_entitlement_and_first_assignment(
        self,
    ):
        first = self.attempt(1)
        self.attempt(2)
        self.presented(first)
        self.db.insert(
            "classic_exam_student_privileges",
            {"exam_id": self.exam_id, "student_id": 2081, "replacement_limit": 2},
        )
        self.connection.commit()
        with self.app.test_request_context():
            preview = results.history_delete_preview(
                self.db, self.exam_id, 2081, self.admin
            )
        with self.app.test_request_context(
            headers={"X-Exam-Confirmation": preview["confirmationToken"]}
        ):
            results.delete_history(self.db, self.exam_id, 2081, self.admin)
        self.connection.commit()
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_attempts"), 0
        )
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM classic_exam_votes"), 0)
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_definition_versions"), 0
        )
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_assignments"), 1
        )
        self.assertEqual(
            self.db.scalar("SELECT history_generation FROM classic_exam_assignments"), 2
        )
        self.assertEqual(
            self.db.scalar(
                "SELECT replacement_limit FROM classic_exam_student_privileges"
            ),
            2,
        )

    def test_changed_preview_blocks_delete_and_whole_delete_is_clean(self):
        first = self.attempt()
        self.presented(first)
        with self.app.test_request_context():
            preview = results.exam_delete_preview(self.db, self.exam_id, self.admin)
        self.db.execute(
            "UPDATE classic_exam_attempts SET state_version=state_version+1 WHERE id=%s",
            (first,),
        )
        self.connection.commit()
        with self.app.test_request_context(
            headers={"X-Exam-Confirmation": preview["confirmationToken"]}
        ):
            with self.assertRaises(common.ExamError) as error:
                results.delete_exam(self.db, self.exam_id, self.admin)
        self.assertEqual(error.exception.code, "delete_preview_changed")
        self.connection.rollback()
        with self.app.test_request_context():
            preview = results.exam_delete_preview(self.db, self.exam_id, self.admin)
        with self.app.test_request_context(
            headers={"X-Exam-Confirmation": preview["confirmationToken"]}
        ):
            results.delete_exam(self.db, self.exam_id, self.admin)
        self.connection.commit()
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM exams WHERE id=%s", (self.exam_id,)), 0
        )
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_attempts"), 0
        )
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_assignments"), 0
        )
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_definition_versions"), 0
        )
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM students"), 1)

    def test_retake_requires_changed_membership(self):
        admin = importlib.import_module(DOMAIN + ".admin")
        self.attempt()
        same = admin.save_commission(
            self.db, self.exam_id, {"name": "Комиссия та же", "examinatorIds": [1, 2]}
        )
        changed = admin.save_commission(
            self.db, self.exam_id, {"name": "Комиссия другая", "examinatorIds": [2, 3]}
        )
        self.connection.commit()
        with self.assertRaises(common.ExamError) as error:
            results.retake(
                self.db,
                self.exam_id,
                2081,
                {"commissionId": same["id"], "expectedHistoryGeneration": 1},
                self.admin,
            )
        self.assertEqual(error.exception.code, "retake_commission_unchanged")
        successful = results.retake(
            self.db,
            self.exam_id,
            2081,
            {"commissionId": changed["id"], "expectedHistoryGeneration": 1},
            self.admin,
        )
        self.connection.commit()
        self.assertEqual(successful["assignment"]["attemptNo"], 2)
        self.assertEqual(successful["assignment"]["historyGeneration"], 1)

    @contextmanager
    def http_api(self, filename, blueprint_name, actor):
        """Real Flask routes with local transaction/auth boundaries only."""
        parent = types.ModuleType("cpm_back")
        parent.__path__ = []
        services = types.ModuleType("cpm_back.services")
        services.__path__ = []
        auth = types.ModuleType("cpm_back.auth")
        auth.__path__ = []
        db_package = types.ModuleType("cpm_back.db")
        db_package.__path__ = []
        pool = types.ModuleType("cpm_back.db.mysql_pool")

        def checkout():
            self.connection.rollback()  # Model a fresh/reset pool checkout.
            return self.connection

        pool.get_db_connection = checkout
        pool.close_db_connection = lambda connection: connection.rollback()
        jwt = types.ModuleType("cpm_back.auth.jwt_auth")
        jwt.get_current_user = lambda: actor[0]
        modules = {
            "cpm_back": parent,
            "cpm_back.services": services,
            "cpm_back.auth": auth,
            "cpm_back.db": db_package,
            "cpm_back.db.mysql_pool": pool,
            "cpm_back.auth.jwt_auth": jwt,
            "cpm_back.services.exams": package,
            "cpm_back.services.exams.common": common,
            "cpm_back.services.exams.results": results,
            "cpm_back.services.exams.admin": importlib.import_module(DOMAIN + ".admin"),
        }
        with patch.dict(sys.modules, modules):
            path = (
                Path(__file__).resolve().parents[1] / "cpm_back/blueprints" / filename
            )
            spec = importlib.util.spec_from_file_location(
                "_local_result_blueprint_" + blueprint_name, path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.transaction = common.transaction
            self.app.register_blueprint(getattr(module, blueprint_name))
            yield self.app.test_client()

    def test_http_physical_delete_replay_after_exam_absent(self):
        self.attempt()
        with self.http_api("exams_v2_bp.py", "exams_v2_bp", [self.admin]) as client:
            preview = client.get(f"/api/exams/{self.exam_id}/delete-preview")
            self.assertEqual(preview.status_code, 200)
            token = preview.get_json()["data"]["confirmationToken"]
            headers = {
                "Authorization": "Bearer local-test-token",
                "Idempotency-Key": str(uuid.uuid4()),
                "X-Exam-Confirmation": token,
            }
            first = client.delete(f"/api/exams/{self.exam_id}", headers=headers)
            self.assertEqual(first.status_code, 204, first.get_data(as_text=True))
            repeated = client.delete(f"/api/exams/{self.exam_id}", headers=headers)
            self.assertEqual(repeated.status_code, 204, repeated.get_data(as_text=True))
            headers["Idempotency-Key"] = str(uuid.uuid4())
            self.assertEqual(
                client.delete(
                    f"/api/exams/{self.exam_id}", headers=headers
                ).status_code,
                404,
            )
            self.assertEqual(
                self.db.scalar(
                    "SELECT COUNT(*) FROM exam_admin_commands WHERE tombstone=1 AND receipt IS NULL"
                ),
                1,
            )

    def test_http_student_actor_is_not_client_student_id(self):
        attempt = self.attempt()
        self.presented(attempt)
        actor = [self.student]
        with self.http_api("student_exams_bp.py", "student_exams_bp", actor) as client:
            response = client.get(
                f"/api/student/exams/{self.exam_id}/result?studentId=99"
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers["Cache-Control"])
            actor[0] = {"role": "student", "id": 99}
            self.assertEqual(
                client.get(
                    f"/api/student/exams/{self.exam_id}/result?studentId=2081"
                ).status_code,
                404,
            )
            actor[0] = self.admin
            self.assertEqual(client.get("/api/student/exams/results").status_code, 403)
            actor[0] = None
            self.assertEqual(client.get("/api/student/exams/results").status_code, 401)


if __name__ == "__main__":
    unittest.main()
