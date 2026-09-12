"""Opt-in real MySQL integration suite. Hard-coded loopback only, never prod.

CLASSIC_EXAM_TEST_MYSQL=1 .venv/bin/python -m unittest discover -s tests -p test_classic_exam_mysql.py -v
Uses schema already migrated in cpm_exam_rehearsal on port33077. All personal data
and exams created here are synthetic and removed by exact IDs in tearDown.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import threading
import unittest
from unittest.mock import patch
import uuid

from flask import Flask
import mysql.connector

from cpm_back.auth.jwt_auth import generate_token
from cpm_back.blueprints.exams_v2_bp import exams_v2_bp
from cpm_back.blueprints.examiner_exams_bp import examiner_exams_bp
from cpm_back.blueprints.student_exams_bp import student_exams_bp
from cpm_back.services.exams.common import UnitOfWork


@unittest.skipUnless(
    os.environ.get("CLASSIC_EXAM_TEST_MYSQL") == "1",
    "Explicit local MySQL opt-in required",
)
class ClassicExamMySQLTests(unittest.TestCase):
    @staticmethod
    def connect():
        return mysql.connector.connect(
            host="127.0.0.1",
            port=33077,
            user="root",
            password="",
            database="cpm_exam_rehearsal",
            autocommit=False,
        )

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            JWT_SECRET_KEY="local-classic-exam-api-test-secret-only",
        )
        self.app.register_blueprint(exams_v2_bp)
        self.app.register_blueprint(examiner_exams_bp)
        self.app.register_blueprint(student_exams_bp)
        self.pool_patch = patch(
            "cpm_back.db.mysql_pool.get_db_connection", side_effect=self.connect
        )
        self.pool_patch.start()
        self.addCleanup(self.pool_patch.stop)
        self.exam_ids = []
        self.users = []
        with self.connect() as conn:
            db = UnitOfWork(conn)
            self.direction = db.scalar("SELECT MIN(id) FROM directions")
            self.student = db.insert(
                "students",
                {"full_name": "Synthetic classic student", "class": 10, "tg_name": ""},
            )
            self.users.append(("student", self.student))
            self.members = []
            for i in range(6):
                ident = db.insert(
                    "examinators", {"full_name": f"Synthetic examiner {i}"}
                )
                self.users.append(("examinator", ident))
                self.members.append(ident)
            for role, ident in self.users:
                db.insert(
                    "auth_users",
                    {
                        "username": "ce-local-" + str(uuid.uuid4())[:20],
                        "ref_id": ident,
                        "role": role,
                    },
                )
            conn.commit()
            db.cursor.close()
        self.admin = {"role": "admin", "id": 7000001, "full_name": "Local admin"}
        self.addCleanup(self.cleanup_data)

    def cleanup_data(self):
        with self.connect() as conn:
            db = UnitOfWork(conn)
            for ident in self.exam_ids:
                db.execute(
                    "DELETE FROM classic_exam_attempts WHERE exam_id=%s", (ident,)
                )
                db.execute(
                    "DELETE FROM classic_exam_definition_versions WHERE exam_id=%s",
                    (ident,),
                )
                db.execute("DELETE FROM exams WHERE id=%s", (ident,))
                db.execute(
                    "DELETE FROM exam_admin_commands WHERE target_exam_id=%s", (ident,)
                )
            for role, ident in self.users:
                db.execute(
                    "DELETE FROM auth_users WHERE role=%s AND ref_id=%s", (role, ident)
                )
                db.execute(
                    "DELETE FROM "
                    + ("students" if role == "student" else "examinators")
                    + " WHERE id=%s",
                    (ident,),
                )
            db.execute(
                "DELETE FROM exam_admin_commands WHERE actor_role=%s AND actor_id=%s",
                ("admin", self.admin["id"]),
            )
            conn.commit()
            db.cursor.close()

    def headers(self, actor=None, key=None):
        with self.app.app_context():
            token = generate_token(actor or self.admin)
        return {
            "Authorization": "Bearer " + token,
            "Idempotency-Key": key or str(uuid.uuid4()),
        }

    def request(
        self,
        method,
        path,
        data=None,
        actor=None,
        key=None,
        expected=200,
        extra_headers=None,
    ):
        headers = self.headers(actor, key)
        headers.update(extra_headers or {})
        with self.app.test_client() as client:
            response = client.open(path, method=method, json=data, headers=headers)
        self.assertEqual(
            response.status_code, expected, (method, path, response.get_json())
        )
        return (
            response.get_json()["data"]
            if response.is_json and expected < 400
            else response.get_json()
        )

    def setup_exam(
        self, members=1, quota=1, weight=5, mode="round_up", replacement_limit=1
    ):
        exam = self.request(
            "POST",
            "/api/exams",
            {"examType": "classic", "directionId": self.direction},
            expected=201,
        )["exam"]
        self.exam_ids.append(exam["id"])
        eid = exam["id"]
        base = f"/api/exams/{eid}/classic"
        time = datetime.now(timezone.utc)
        config = self.request(
            "PATCH",
            base + "/config",
            {
                "startAt": (time - timedelta(hours=1)).isoformat(),
                "endAt": (time + timedelta(hours=1)).isoformat(),
                "fractionalMode": mode,
                "tieBreakerSourceMode": (
                    "any_part" if mode == "extra_question" else None
                ),
                "tieBreakerHalfMode": "repeat" if mode == "extra_question" else None,
                "expectedConfigVersion": 1,
            },
        )["config"]
        part = self.request(
            "POST",
            base + "/parts",
            {"code": "A", "questionWeight": weight, "questionCount": quota},
            expected=201,
        )["part"]
        for i in range(quota + replacement_limit + 2):
            self.request(
                "POST",
                base + "/questions",
                {
                    "partId": part["id"],
                    "questionText": f"Question {i}",
                    "answerText": f"Answer {i}",
                },
                expected=201,
            )
        cv = self.request("GET", base + "/config")["config"]["configVersion"]
        self.request(
            "PUT",
            base + "/scoring",
            {
                "thresholds": [{"grade": i, "minScore": i} for i in range(6)],
                "fractionalMode": mode,
                "tieBreakerSourceMode": (
                    "any_part" if mode == "extra_question" else None
                ),
                "tieBreakerHalfMode": "repeat" if mode == "extra_question" else None,
                "expectedConfigVersion": cv,
            },
        )
        commission = self.request(
            "POST",
            base + "/commissions",
            {"examinatorIds": self.members[:members]},
            expected=201,
        )["commission"]
        assignment = self.request(
            "POST",
            base + "/assignments",
            {
                "studentId": self.student,
                "commissionId": commission["id"],
                "replacementLimit": replacement_limit,
            },
            expected=201,
        )["assignment"]
        self.exam_id = eid
        self.part_id = part["id"]
        self.assignment = assignment
        self.active_members = self.members[:members]
        actor = {"role": "examinator", "id": self.members[0]}
        result = self.request(
            "POST",
            f'/api/examiner/exams/{eid}/assignments/{assignment["id"]}/attempts/ensure',
            {"expectedHistoryGeneration": 1},
            actor,
            expected=201,
        )
        return result["attempt"]

    def ready_start(self, attempt):
        for ident in self.active_members:
            attempt = self.request(
                "POST",
                f'/api/examiner/attempts/{attempt["id"]}/ready',
                {},
                {"role": "examinator", "id": ident},
            )["attempt"]
        return self.request(
            "POST",
            f'/api/examiner/attempts/{attempt["id"]}/start',
            {"expectedStateVersion": attempt["stateVersion"]},
            {"role": "examinator", "id": self.active_members[0]},
        )["attempt"]

    def vote_all(self, attempt, values):
        question = attempt["currentQuestion"]
        result = None
        for ident, value in zip(self.active_members, values):
            result = self.request(
                "POST",
                f'/api/examiner/attempts/{attempt["id"]}/vote',
                {
                    "presentedQuestionId": question["id"],
                    "roundId": question["round"]["id"],
                    "value": value,
                },
                {"role": "examinator", "id": ident},
            )
        return result

    def test_create_validation_replay_and_safe_protocol(self):
        key = str(uuid.uuid4())
        payload = {
            "examType": "outside_lms",
            "directionId": self.direction,
            "date": "2026-09-12",
        }
        first = self.request("POST", "/api/exams", payload, key=key, expected=201)
        self.exam_ids.append(first["exam"]["id"])
        replay = self.request("POST", "/api/exams", payload, key=key)
        self.assertEqual(first["exam"]["id"], replay["exam"]["id"])
        self.assertTrue(replay["replayed"])
        error = self.request(
            "POST",
            "/api/exams",
            dict(payload, date="2026-09-13"),
            key=key,
            expected=409,
        )
        self.assertEqual(error["error"], "idempotency_key_reused")
        self.request(
            "POST",
            "/api/exams",
            {"examType": {}, "directionId": self.direction},
            expected=400,
        )
        self.request(
            "POST",
            f'/api/exams/{first["exam"]["id"]}/outside-lms/results',
            {"studentId": self.student, "points": 1, "grade": 6, "examinator": "Local"},
            expected=400,
        )

    def test_concurrent_create_same_key_returns_created_and_replayed(self):
        key = str(uuid.uuid4())
        payload = {"examType": "classic", "directionId": self.direction}
        barrier = threading.Barrier(2)
        original = UnitOfWork.insert

        def simultaneous_reservation(db, table, data):
            if table == "exam_admin_commands" and data["idempotency_key"] == key:
                barrier.wait(timeout=5)
            return original(db, table, data)

        def create():
            with self.app.test_client() as client:
                response = client.post(
                    "/api/exams", json=payload, headers=self.headers(key=key)
                )
            return response.status_code, response.get_json()

        with patch.object(
            UnitOfWork, "insert", simultaneous_reservation
        ), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        for status, result in results:
            if status in (200, 201):
                self.exam_ids.append(result["data"]["exam"]["id"])
        self.exam_ids = list(set(self.exam_ids))
        self.assertEqual(sorted(status for status, _ in results), [200, 201], results)
        self.assertEqual(len(self.exam_ids), 1)
        self.assertEqual(sum(result["data"]["replayed"] for _, result in results), 1)

    def test_batched_catalogs_and_pending_readiness_do_not_issue_per_student_queries(
        self,
    ):
        attempt = self.setup_exam(members=1)
        actor = {"role": "examinator", "id": self.members[0]}
        with self.connect() as conn:
            db = UnitOfWork(conn)
            for i in range(19):
                student = db.insert(
                    "students",
                    {
                        "full_name": f"Synthetic catalog student {i}",
                        "class": 10,
                        "tg_name": "",
                    },
                )
                self.users.append(("student", student))
                db.insert(
                    "auth_users",
                    {
                        "username": "ce-local-" + str(uuid.uuid4())[:20],
                        "ref_id": student,
                        "role": "student",
                    },
                )
                assignment = db.insert(
                    "classic_exam_assignments",
                    {
                        "exam_id": self.exam_id,
                        "student_id": student,
                        "attempt_no": 1,
                        "created_by_role": "admin",
                        "created_by": self.admin["id"],
                        "commission_name_snapshot": "Synthetic catalog commission",
                    },
                )
                db.insert(
                    "classic_exam_assignment_members",
                    {
                        "assignment_id": assignment,
                        "examinator_id": self.members[0],
                        "position": 1,
                        "full_name_snapshot": "Synthetic examiner 0",
                    },
                )
            conn.commit()
            db.cursor.close()
        statements = []
        execute = UnitOfWork.execute

        def count(db, sql, params=()):
            if not sql.lstrip().upper().startswith("SET "):
                statements.append(sql)
            return execute(db, sql, params)

        with patch.object(UnitOfWork, "execute", count):
            rows = self.request(
                "GET",
                f"/api/examiner/exams/{self.exam_id}/students?limit=50",
                actor=actor,
            )
        self.assertEqual(len(rows["items"]), 20)
        self.assertLessEqual(
            len(statements),
            12,
            "Catalog queries must be bounded independently of page size",
        )
        own = next(row for row in rows["items"] if row["student"]["id"] == self.student)
        self.assertTrue(own["canPrepare"])
        self.assertFalse(own["canStart"])
        self.request("POST", f'/api/examiner/attempts/{attempt["id"]}/ready', {}, actor)
        rows = self.request(
            "GET", f"/api/examiner/exams/{self.exam_id}/students", actor=actor
        )
        own = next(row for row in rows["items"] if row["student"]["id"] == self.student)
        self.assertTrue(own["canStart"])
        statements.clear()
        with patch.object(UnitOfWork, "execute", count):
            catalog = self.request("GET", "/api/examiner/exams", actor=actor)
        self.assertLessEqual(len(statements), 8)
        exam = next(row for row in catalog["items"] if row["id"] == self.exam_id)
        self.assertEqual(exam["assignedStudentsCount"], 20)
        self.assertEqual(exam["assignments"]["pendingReady"], 1)
        self.assertEqual(exam["assignments"]["notStarted"], 19)

    def test_concurrent_six_ready_start_and_votes(self):
        attempt = self.setup_exam(members=6)
        aid = attempt["id"]
        barrier = threading.Barrier(6)

        def ready(ident):
            barrier.wait()
            return self.request(
                "POST",
                f"/api/examiner/attempts/{aid}/ready",
                {},
                {"role": "examinator", "id": ident},
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(ready, self.active_members))
        state = self.request(
            "GET",
            f"/api/examiner/attempts/{aid}",
            actor={"role": "examinator", "id": self.members[0]},
        )["attempt"]
        self.assertTrue(state["allMembersReady"])
        self.assertEqual(state["stateVersion"], 7)
        version = state["stateVersion"]
        barrier = threading.Barrier(6)

        def start(ident):
            barrier.wait()
            return self.request(
                "POST",
                f"/api/examiner/attempts/{aid}/start",
                {"expectedStateVersion": version},
                {"role": "examinator", "id": ident},
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            started = list(pool.map(start, self.active_members))
        self.assertEqual(sum(r["receipt"]["outcome"] == "started" for r in started), 1)
        state = started[0]["attempt"]
        question = state["currentQuestion"]
        barrier = threading.Barrier(6)

        def vote(ident):
            barrier.wait()
            return self.request(
                "POST",
                f"/api/examiner/attempts/{aid}/vote",
                {
                    "presentedQuestionId": question["id"],
                    "roundId": question["round"]["id"],
                    "value": 1,
                },
                {"role": "examinator", "id": ident},
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(vote, self.active_members))
        final = self.request(
            "GET",
            f"/api/examiner/attempts/{aid}",
            actor={"role": "examinator", "id": self.members[0]},
        )["attempt"]
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["result"]["grade"], 5)
        self.assertEqual(
            sum(r["receipt"]["outcome"] == "completed" for r in results), 1
        )

    def test_dispute_privacy_replacement_and_snapshot(self):
        state = self.ready_start(self.setup_exam(members=3, mode="extra_question"))
        aid = state["id"]
        actor = {"role": "examinator", "id": self.members[0]}
        first = state["currentQuestion"]
        replaced = self.request(
            "POST",
            f"/api/examiner/attempts/{aid}/replace-question",
            {
                "presentedQuestionId": first["id"],
                "expectedStateVersion": state["stateVersion"],
            },
            actor,
        )["attempt"]
        self.assertNotEqual(first["id"], replaced["currentQuestion"]["id"])
        question = replaced["currentQuestion"]
        response = self.request(
            "POST",
            f"/api/examiner/attempts/{aid}/vote",
            {
                "presentedQuestionId": question["id"],
                "roundId": question["round"]["id"],
                "value": 1,
            },
            actor,
        )
        peer = self.request(
            "GET",
            f"/api/examiner/attempts/{aid}",
            actor={"role": "examinator", "id": self.members[1]},
        )["attempt"]
        self.assertNotIn("votes", peer["currentQuestion"]["round"])
        self.assertIsNone(peer["currentQuestion"]["round"]["myVote"])
        self.request(
            "POST",
            f"/api/examiner/attempts/{aid}/vote",
            {
                "presentedQuestionId": question["id"],
                "roundId": question["round"]["id"],
                "value": 0,
            },
            {"role": "examinator", "id": self.members[1]},
        )
        disputed = self.request(
            "POST",
            f"/api/examiner/attempts/{aid}/vote",
            {
                "presentedQuestionId": question["id"],
                "roundId": question["round"]["id"],
                "value": 0.5,
            },
            {"role": "examinator", "id": self.members[2]},
        )["attempt"]
        self.assertEqual(disputed["currentQuestion"]["round"]["roundNo"], 2)
        self.assertEqual(
            len(disputed["currentQuestion"]["lastCompletedRound"]["votes"]), 3
        )
        base = f"/api/exams/{self.exam_id}/classic"
        questions = self.request("GET", base + "/questions")["items"]
        for q in questions:
            self.request(
                "DELETE",
                base + f'/questions/{q["id"]}?expectedVersion={q["version"]}',
                expected=204,
            )
        extra = self.vote_all(disputed, [0.5] * 3)["attempt"]
        self.assertEqual(extra["phase"], "tie_breaker")
        final = self.vote_all(extra, [1] * 3)["attempt"]
        self.assertEqual(final["result"]["rawTotal"], 2.5)
        self.assertEqual(final["result"]["grade"], 3)
        detail = self.request(
            "GET",
            f"/api/student/exams/{self.exam_id}/result",
            actor={"role": "student", "id": self.student},
        )
        protocol = self.request(
            "GET",
            f"/api/student/exams/{self.exam_id}/attempts/{aid}/questions",
            actor={"role": "student", "id": self.student},
        )
        self.assertEqual(len(protocol["items"]), 3)
        for item in protocol["items"]:
            self.assertNotIn("votes", item)
            self.assertNotIn("rounds", item)

    def test_extra_cycles_and_idempotent_vote(self):
        state = self.ready_start(self.setup_exam(mode="extra_question"))
        state = self.vote_all(state, [0.5])["attempt"]
        last = None
        for _ in range(20):
            text = state["currentQuestion"]["questionText"]
            self.assertNotEqual(last, text)
            last = text
            state = self.vote_all(state, [0.5])["attempt"]
        question = state["currentQuestion"]
        payload = {
            "presentedQuestionId": question["id"],
            "roundId": question["round"]["id"],
            "value": 0,
        }
        actor = {"role": "examinator", "id": self.members[0]}
        key = str(uuid.uuid4())
        first = self.request(
            "POST", f'/api/examiner/attempts/{state["id"]}/vote', payload, actor, key
        )
        replay = self.request(
            "POST", f'/api/examiner/attempts/{state["id"]}/vote', payload, actor, key
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["receipt"], replay["receipt"])
        self.assertEqual(replay["attempt"]["result"]["grade"], 2)

    def test_foreign_actor_and_stale_commands(self):
        state = self.ready_start(self.setup_exam())
        aid = state["id"]
        actor = {"role": "examinator", "id": self.members[0]}
        q = state["currentQuestion"]
        self.request(
            "GET",
            f"/api/examiner/attempts/{aid}",
            actor={"role": "examinator", "id": self.members[5]},
            expected=404,
        )
        self.request(
            "POST",
            f"/api/examiner/attempts/{aid}/replace-question",
            {"presentedQuestionId": q["id"], "expectedStateVersion": 1},
            actor,
            expected=409,
        )
        self.request(
            "POST",
            f"/api/examiner/attempts/{aid}/vote",
            {
                "presentedQuestionId": q["id"],
                "roundId": q["round"]["id"],
                "value": True,
            },
            actor,
            expected=400,
        )
        self.assertEqual(
            self.request("GET", f"/api/examiner/attempts/{aid}", actor=actor)[
                "attempt"
            ]["stateVersion"],
            state["stateVersion"],
        )

    def test_twenty_concurrent_rounds_preserve_every_vote(self):
        state = self.ready_start(self.setup_exam(members=6, mode="extra_question"))
        aid = state["id"]
        for index in range(20):
            question = state["currentQuestion"]
            barrier = threading.Barrier(6)
            value = 1 if index == 19 else 0.5

            def submit(ident):
                barrier.wait()
                return self.request(
                    "POST",
                    f"/api/examiner/attempts/{aid}/vote",
                    {
                        "presentedQuestionId": question["id"],
                        "roundId": question["round"]["id"],
                        "value": value,
                    },
                    {"role": "examinator", "id": ident},
                )

            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(submit, self.active_members))
            state = self.request(
                "GET",
                f"/api/examiner/attempts/{aid}",
                actor={"role": "examinator", "id": self.members[0]},
            )["attempt"]
        self.assertEqual(state["status"], "completed")
        with self.connect() as conn:
            db = UnitOfWork(conn)
            self.assertEqual(
                db.scalar(
                    "SELECT COUNT(*) FROM classic_exam_votes v JOIN classic_exam_vote_rounds r ON r.id=v.round_id WHERE r.attempt_id=%s",
                    (aid,),
                ),
                120,
            )
            self.assertEqual(
                db.scalar(
                    "SELECT COUNT(*) FROM classic_exam_vote_rounds WHERE attempt_id=%s AND status='consensus'",
                    (aid,),
                ),
                20,
            )
            self.assertEqual(
                db.scalar(
                    "SELECT COUNT(*) FROM classic_exam_vote_rounds WHERE attempt_id=%s AND status='open'",
                    (aid,),
                ),
                0,
            )
            db.cursor.close()

    def test_race_replace_against_vote_is_atomic(self):
        state = self.ready_start(self.setup_exam(members=2))
        aid = state["id"]
        q = state["currentQuestion"]
        barrier = threading.Barrier(2)
        commands = [
            (
                "replace-question",
                {
                    "presentedQuestionId": q["id"],
                    "expectedStateVersion": state["stateVersion"],
                },
            ),
            (
                "vote",
                {
                    "presentedQuestionId": q["id"],
                    "roundId": q["round"]["id"],
                    "value": 1,
                },
            ),
        ]

        def submit(pair):
            name, data = pair
            barrier.wait()
            with self.app.test_client() as client:
                response = client.post(
                    f"/api/examiner/attempts/{aid}/{name}",
                    json=data,
                    headers=self.headers({"role": "examinator", "id": self.members[0]}),
                )
                return response.status_code, response.get_json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, commands))
        self.assertEqual(sorted(r[0] for r in responses), [200, 409], responses)
        final = self.request(
            "GET",
            f"/api/examiner/attempts/{aid}",
            actor={"role": "examinator", "id": self.members[0]},
        )["attempt"]
        if final["progress"]["replacementUsed"]:
            self.assertNotEqual(final["currentQuestion"]["id"], q["id"])
            self.assertEqual(final["currentQuestion"]["round"]["votesReceived"], 0)
        else:
            self.assertEqual(final["currentQuestion"]["id"], q["id"])
            self.assertEqual(final["currentQuestion"]["round"]["votesReceived"], 1)

    def test_part_preview_detects_changed_bank_and_readiness_reserve_is_per_student(
        self,
    ):
        pending = self.setup_exam()
        base = f"/api/exams/{self.exam_id}/classic"
        preview = self.request("GET", base + f"/parts/{self.part_id}/delete-preview")
        self.request(
            "POST",
            base + "/questions",
            {
                "partId": self.part_id,
                "questionText": "New after preview",
                "answerText": "Answer",
            },
            expected=201,
        )
        self.request(
            "DELETE",
            base + f"/parts/{self.part_id}?expectedVersion=1",
            extra_headers={"X-Exam-Confirmation": preview["confirmationToken"]},
            expected=409,
        )
        privilege = self.request("GET", base + f"/students/{self.student}/privilege")
        self.request(
            "PUT",
            base + f"/students/{self.student}/privilege",
            {"replacementLimit": 100, "expectedVersion": privilege["version"]},
        )
        global_report = self.request("GET", base + "/readiness")["readiness"]
        report = self.request(
            "GET", base + f'/assignments/{self.assignment["id"]}/readiness'
        )
        self.assertTrue(global_report["isConfigured"])
        self.assertTrue(report["configurationReady"])
        self.assertFalse(report["assignmentReady"])
        self.request(
            "POST",
            f'/api/examiner/attempts/{pending["id"]}/ready',
            {},
            {"role": "examinator", "id": self.members[0]},
            expected=422,
        )
