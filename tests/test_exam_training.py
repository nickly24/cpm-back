"""Exam-card contracts against a disposable local MySQL schema, never create_app.

Set EXAM_MIGRATION_TEST_SOCKET to an explicit local Unix socket to run integration
tests. Connections are constructed here, without reading application credentials.
"""
import os
import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from cpm_back.services.cards import exam_training as training
from cpm_back.services.cards import training_cards, training_progress
from cpm_back.services.cards.training_projection import content_fingerprint
from cpm_back.services.exams import admin, common, results
from cpm_back.blueprints.cards_bp import cards_bp
from cpm_back.blueprints.exams_v2_bp import exams_v2_bp
from cpm_back.blueprints.student_exams_bp import student_exams_bp
from cpm_back.auth.admin_permissions import enforce_admin_permissions
from scripts import exam_training_migration
from tests import test_exams_migrations as fixture


class ExamTrainingProjectionTests(unittest.TestCase):
    def test_projection_identity_content_and_multiline(self):
        row = {"id": 23, "exam_id": 7, "part_id": 3, "part_code": "A", "sort_order": 1,
               "question_text": "Вопрос\nстрока", "answer_text": "первый\n\nвторой; третий"}
        first = training.project_question(row)
        moved = training.project_question({**row, "part_id": 4, "part_code": "B", "sort_order": 100})
        self.assertEqual(first["card_ref"], "exam:7:23")
        self.assertEqual(first["content_fingerprint"], moved["content_fingerprint"])
        self.assertEqual(first["answer"], row["answer_text"])
        self.assertEqual(content_fingerprint(" Q ", " A "), content_fingerprint("Q", "A"))
        self.assertNotEqual(content_fingerprint("Q", "A\nB"), content_fingerprint("Q", "A B"))

    def test_scope_requires_positive_integer_identifiers(self):
        for value in ["", "0", "-1", "1:0", "1:2:3", "1:%", "1.0", " 1", "01"]:
            with self.subTest(value=value), self.assertRaises(training.TrainingError):
                training.parse_scope(value)
        self.assertEqual(training.parse_scope("9:23"), (9, 23))


@unittest.skipUnless(os.environ.get("EXAM_MIGRATION_TEST_SOCKET"), "Explicit local MySQL socket required")
class ExamTrainingIntegrationTests(unittest.TestCase):
    query = fixture.LocalMySQLMigrationTests.query
    tearDown = fixture.LocalMySQLMigrationTests.tearDown

    def setUp(self):
        fixture.LocalMySQLMigrationTests.setUp(self)
        fixture.migration.run_migrations(self.connection, apply=True)
        # Create just the two historical training tables, never execute migration
        # 013's destructive content cleanup, ALTERs or DROP statements.
        historical = (fixture.ROOT / "migrations/013_training_unification.sql").read_text()
        for sql in re.findall(r"CREATE TABLE IF NOT EXISTS student_(?:card_progress|section_study_settings)\s*\([\s\S]*?;", historical):
            self.query(sql)
        exam_training_migration.run_migration(self.connection, apply=True)
        self.query("CREATE TABLE card_themes(id INT PRIMARY KEY,name VARCHAR(255),direction_id INT)")
        self.query("CREATE TABLE cards(id INT PRIMARY KEY,theme_id INT,question TEXT,answer TEXT,sort_order INT)")
        self.query("INSERT INTO card_themes VALUES(10,'Ручной раздел',6)")
        self.query("INSERT INTO cards VALUES(10,10,'Вопрос вручную','Ответ вручную',1)")
        self.query("INSERT INTO exams(id,exam_type,direction_id,training_enabled) VALUES(70,'classic',6,0),(71,'classic',6,1)")
        self.query("INSERT INTO classic_exam_settings(exam_id) VALUES(70),(71)")
        self.query("INSERT INTO classic_exam_parts(id,exam_id,code,sort_order) VALUES(700,70,'B',1),(701,70,'A',2),(702,70,'C',3),(710,71,'A',1)")
        self.query("INSERT INTO classic_exam_questions(id,exam_id,part_id,question_text,answer_text,content_hash,sort_order) VALUES(7000,70,700,'Вопрос 1','Первый\n\nВторой','h1',1),(7001,70,701,'Вопрос 2','Ответ 2','h2',1),(7100,71,710,'Другой','Ответ','h3',1)")
        self.actor = {"role": "student", "id": 2081}
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, JWT_SECRET_KEY="synthetic-exam-training")
        self.app.before_request(enforce_admin_permissions)
        self.app.register_blueprint(cards_bp)
        self.app.register_blueprint(exams_v2_bp)
        self.app.register_blueprint(student_exams_bp)
        self.client = self.app.test_client()
        for target in (
            "cpm_back.db.mysql_pool.get_db_connection",
            "cpm_back.services.cards.exam_training.get_db_connection",
            "cpm_back.services.cards.training_cards.get_db_connection",
            "cpm_back.services.cards.training_progress.get_db_connection",
        ):
            p = patch(target, side_effect=self.connect)
            p.start()
            self.addCleanup(p.stop)
        p = patch("cpm_back.auth.jwt_auth.get_current_user", side_effect=lambda: self.actor)
        p.start()
        self.addCleanup(p.stop)
        for target, value in (("get_directions", [{"id": 6, "name": "Тестовое направление"}]), ("get_visible_training_tests", [])):
            p = patch.object(training_cards, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def connect(self):
        import mysql.connector
        assert self.database.startswith("cpm_exam_schema_test_")
        return mysql.connector.connect(unix_socket=os.environ["EXAM_MIGRATION_TEST_SOCKET"],
                                       user="root", password="", database=self.database, autocommit=False)

    def enable(self):
        self.query("UPDATE exams SET training_enabled=1 WHERE id=70")

    def read(self, ref="70"):
        return self.client.get(f"/section-study/2081/exam/{ref}")

    def admin_request(self, method, path, payload=None):
        before = self.actor
        self.actor = {"role": "admin", "id": 1}
        try:
            return self.client.open(path, method=method, json=payload,
                                    headers={"Authorization": "Bearer synthetic", "Idempotency-Key": str(uuid4())})
        finally:
            self.actor = before

    def mark(self, card=None, ref="70", **overrides):
        if card is None:
            card = self.read().json["cards"][0]
        payload = {"student_id": 2081, "section_kind": "exam", "section_ref_id": ref,
                   "card_ref": card["card_ref"], "content_fingerprint": card["content_fingerprint"], **overrides}
        return self.client.post("/mark-card-learned", json=payload)

    def test_publication_hidden_preview_empty_and_outside(self):
        self.assertEqual(self.read().status_code, 403)
        config_before = self.query("SELECT config_version FROM classic_exam_settings WHERE exam_id=70")
        with patch.object(training, "fetch_progress_map", side_effect=AssertionError("Preview must not read progress")), patch.object(training, "get_study_settings", side_effect=AssertionError("Preview must not read settings")):
            preview = self.admin_request("GET", "/api/exams/70/training/preview")
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertTrue(preview.json["data"]["preview"])
        self.assertEqual(preview.json["data"]["stats"]["total"], 2)
        self.assertEqual(preview.json["data"]["parts"][0]["part_code"], "B")
        self.assertEqual(self.query("SELECT COUNT(*) n FROM student_card_progress")[0]["n"], 0)
        enabled = self.admin_request("PATCH", "/api/exams/70/training", {"enabled": True, "expectedVersion": 1})
        self.assertEqual(enabled.status_code, 200, enabled.json)
        self.assertTrue(enabled.json["data"]["exam"]["trainingEnabled"])
        self.assertEqual(enabled.json["data"]["exam"]["version"], 2)
        self.assertEqual(config_before, self.query("SELECT config_version FROM classic_exam_settings WHERE exam_id=70"))
        self.assertEqual(self.read().status_code, 200)
        conflict = self.admin_request("PATCH", "/api/exams/70/training", {"enabled": False, "expectedVersion": 1})
        self.assertEqual(conflict.status_code, 409)
        self.query("DELETE FROM classic_exam_questions WHERE exam_id=70")
        self.assertEqual(self.read().json["error"], "exam_empty")
        self.assertEqual(self.admin_request("GET", "/api/exams/70/training/preview").json["data"]["cards"], [])
        self.assertEqual(self.admin_request("PATCH", "/api/exams/70/training", {"enabled": True, "expectedVersion": 2}).status_code, 422)
        self.assertEqual(self.admin_request("GET", "/api/exams/53/training").status_code, 422)

    def test_student_scope_stats_catalog_and_empty_part(self):
        self.enable()
        view = self.read().json
        self.assertEqual([c["question_id"] for c in view["cards"]], [7000, 7001])
        self.assertEqual([p["refId"] for p in view["parts"]], ["70:700", "70:701", "70:702"])
        self.assertEqual(self.read("70:702").json["cards"], [])
        self.assertEqual(self.read("70:999").status_code, 404)
        self.assertEqual(self.read("999").status_code, 404)
        self.assertEqual(self.mark(view["cards"][0]).status_code, 201)
        whole, part = self.read().json, self.read("70:700").json
        self.assertEqual(whole["stats"]["progress_percent"], 50)
        self.assertEqual(part["stats"]["progress_percent"], 100)
        for stats in [whole["stats"], part["stats"], *[p["stats"] for p in whole["parts"]]]:
            self.assertEqual(stats["total"], stats["learned"] + stats["unlearned"] + stats["answer_changed"])
        tree = self.client.get("/get-training-tree/2081").json
        direction = tree["directions"][0]
        self.assertEqual(direction["total_cards"], 4)  # two exams plus manual card, no nested double count
        catalog = self.client.get("/training-sections/2081/6?kind=exam&progress=in_progress&search=70").json
        self.assertEqual([s["exam_id"] for s in catalog["sections"]], [70])
        self.query("UPDATE exams SET training_enabled=0 WHERE id=70")
        self.assertNotIn(70, [s["exam_id"] for s in self.client.get("/training-sections/2081/6?kind=exam").json["sections"]])

    def test_progress_idempotence_content_change_move_and_student_independence(self):
        self.enable()
        card = self.read().json["cards"][0]
        for ref in ["70", "70:700", "70"]:
            self.assertEqual(self.mark(card, ref).status_code, 201)
        self.assertEqual(self.query("SELECT section_ref_id FROM student_card_progress WHERE student_id=2081"), [{"section_ref_id": "70"}])
        self.actor = {"role": "student", "id": 2082}
        other = self.client.get("/section-study/2082/exam/70").json
        self.assertEqual(other["stats"]["learned"], 0)
        self.actor = {"role": "student", "id": 2081}
        moved = self.admin_request("PATCH", "/api/exams/70/classic/questions/7000", {"partId": 701, "expectedVersion": 1})
        self.assertEqual(moved.status_code, 200, moved.json)
        self.assertEqual(self.read("70:701").json["stats"]["learned"], 1)
        changed = self.admin_request("PATCH", "/api/exams/70/classic/questions/7000", {"answerText": "Новый\nответ", "expectedVersion": 2})
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.read().json["stats"]["answer_changed"], 1)
        stale = self.mark(card)
        self.assertEqual(stale.status_code, 409, stale.json)
        self.assertEqual(stale.json["error"], "content_changed")
        self.assertEqual(stale.json["details"]["card"]["answer"], "Новый\nответ")
        self.assertEqual(self.query("SELECT content_fingerprint FROM student_card_progress WHERE student_id=2081")[0]["content_fingerprint"], card["content_fingerprint"])
        self.assertEqual(self.mark(stale.json["card"]).status_code, 201)
        for _ in range(2):
            self.assertEqual(self.client.delete("/mark-card-learned/2081/exam:70:7000").status_code, 200)
        self.assertEqual(self.read().json["stats"]["unlearned"], 2)

    def test_settings_scopes_resume_and_invalid_inputs(self):
        self.enable()
        root_path = "/section-study-settings/2081/exam/70"
        part_path = root_path + ":700"
        response = self.client.put(root_path, json={"batch_size": 20, "study_mode": "all", "last_batch_index": 55, "last_part_id": None})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["settings"]["last_batch_index"], 0)
        response = self.client.put(part_path, json={"batch_size": 30, "study_mode": "stale", "last_part_id": 700})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.read().json["settings"], {"batch_size": 20, "study_mode": "all", "last_batch_index": 0, "last_part_id": 700})
        self.assertEqual(self.read("70:700").json["settings"]["batch_size"], 30)
        self.assertEqual(self.client.put(part_path, json={"last_batch_index": -1}).json["settings"]["last_batch_index"], -1)
        self.assertEqual(self.read("70:700").json["settings"]["last_batch_index"], -1)
        for payload in [{"last_part_id": 710}, {"batch_size": "20"}, {"study_mode": "bogus"}, {"last_batch_index": -2}]:
            self.assertGreaterEqual(self.client.put(part_path, json=payload).status_code, 400)
        self.client.put(root_path, json={"last_part_id": None})
        self.assertIsNone(self.read().json["settings"]["last_part_id"])

    def test_disabled_rejects_reads_marks_unmarks_settings_without_losing_progress(self):
        self.enable()
        card = self.read().json["cards"][0]
        self.mark(card)
        self.query("UPDATE exams SET training_enabled=0 WHERE id=70")
        self.assertEqual(self.mark(card).status_code, 403)
        self.assertEqual(self.client.delete("/mark-card-learned/2081/exam:70:7000").status_code, 403)
        self.assertEqual(self.client.put("/section-study-settings/2081/exam/70", json={}).status_code, 403)
        self.assertEqual(self.client.get("/section-batch/2081/exam/70/0").status_code, 403)
        self.assertEqual(self.query("SELECT COUNT(*) n FROM student_card_progress")[0]["n"], 1)
        self.enable()
        self.assertEqual(self.read().json["stats"]["learned"], 1)

    def test_permissions_ownership_ref_spoofing_and_preview(self):
        self.enable()
        card = self.read().json["cards"][0]
        self.assertEqual(self.mark(card, student_id=2082).status_code, 403)
        self.assertEqual(self.client.get("/section-study/2082/exam/70").status_code, 403)
        self.assertEqual(self.mark(card, ref="71").status_code, 404)
        self.assertEqual(self.mark(card, ref="70:701").status_code, 404)
        self.assertEqual(self.mark(card, section_kind="manual").status_code, 400)
        self.assertEqual(self.client.get("/api/exams/70/training/preview").status_code, 403)
        self.actor = {"role": "staff_admin", "id": 1, "permissions": {"train": {"view": True, "edit": True}}}
        self.assertEqual(self.client.get("/api/exams/70/training/preview").status_code, 403)
        self.assertEqual(self.client.post("/mark-card-learned", json={"student_id": 2081, "section_kind": "exam", "section_ref_id": "70", "card_ref": card["card_ref"], "content_fingerprint": card["content_fingerprint"]}).status_code, 403)
        self.actor["permissions"]["exams"] = {"view": True}
        self.assertEqual(self.client.get("/api/exams/70/training/preview").status_code, 200)
        self.assertEqual(self.client.patch("/api/exams/70/training", json={"enabled": False, "expectedVersion": 1}, headers={"Authorization": "Bearer synthetic"}).status_code, 403)
        self.assertEqual(self.client.get("/api/exams/70/training/preview?section_ref_id=71:710").status_code, 404)

    def test_question_part_and_exam_delete_cleanup_are_source_scoped(self):
        self.enable()
        card = self.read().json["cards"][0]
        self.mark(card)
        self.mark(self.read("71").json["cards"][0], "71")
        self.query("INSERT INTO student_card_progress(student_id,section_kind,section_ref_id,card_ref,content_fingerprint) VALUES(2081,'manual','10','card:10','untouched')")
        self.client.put("/section-study-settings/2081/exam/70:700", json={"last_part_id": 700})
        deletion = self.admin_request("DELETE", "/api/exams/70/classic/questions/7000?expectedVersion=1")
        self.assertEqual(deletion.status_code, 204, deletion.json)
        self.assertEqual(self.mark(card).status_code, 404)
        preview = self.admin_request("GET", "/api/exams/70/classic/parts/700/delete-preview").json["data"]
        before = self.actor
        self.actor = {"role": "admin", "id": 1}
        response = self.client.delete("/api/exams/70/classic/parts/700?expectedVersion=1", headers={"Authorization": "Bearer synthetic", "Idempotency-Key": str(uuid4()), "X-Exam-Confirmation": preview["confirmationToken"]})
        self.actor = before
        self.assertEqual(response.status_code, 204, response.json)
        self.assertEqual(self.query("SELECT section_ref_id,last_part_id FROM student_section_study_settings WHERE section_ref_id LIKE '70%'"), [{"section_ref_id": "70", "last_part_id": 0}])
        self.assertEqual(self.read().json["settings"]["last_part_id"], 0)
        self.mark(self.read().json["cards"][0])
        preview = self.admin_request("GET", "/api/exams/70/classic/parts/701/delete-preview").json["data"]
        self.actor = {"role": "admin", "id": 1}
        response = self.client.delete("/api/exams/70/classic/parts/701?expectedVersion=1", headers={"Authorization": "Bearer synthetic", "Idempotency-Key": str(uuid4()), "X-Exam-Confirmation": preview["confirmationToken"]})
        self.actor = before
        self.assertEqual(response.status_code, 204, response.json)
        self.assertEqual(self.query("SELECT COUNT(*) n FROM student_card_progress WHERE section_ref_id='70'")[0]["n"], 0)
        preview = self.admin_request("GET", "/api/exams/70/delete-preview").json["data"]
        self.actor = {"role": "admin", "id": 1}
        response = self.client.delete("/api/exams/70", headers={"Authorization": "Bearer synthetic", "Idempotency-Key": str(uuid4()), "X-Exam-Confirmation": preview["confirmationToken"]})
        self.actor = before
        self.assertEqual(response.status_code, 204, response.json)
        self.assertEqual({r["card_ref"] for r in self.query("SELECT card_ref FROM student_card_progress")}, {"exam:71:7100", "card:10"})
        self.assertEqual(self.query("SELECT COUNT(*) n FROM student_section_study_settings")[0]["n"], 0)
        self.assertEqual(self.read().status_code, 404)

    def test_all_questions_beyond_a_page_and_legacy_manual_source(self):
        self.enable()
        for index in range(41):
            self.query("INSERT INTO classic_exam_questions(exam_id,part_id,question_text,answer_text,content_hash,sort_order) VALUES(70,700,%s,'Ответ',%s,%s)", (f"Вопрос {index+3}", f"hash{index}", index+2))
        view = self.read().json
        self.assertEqual(view["stats"]["total"], 43)
        self.assertEqual(len(view["batches"]), 5)
        self.assertEqual(len(self.read("70:700").json["cards"]), 42)
        manual = self.client.get("/section-study/2081/manual/10")
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(manual.json["cards"][0]["card_ref"], "card:10")
        self.assertEqual(manual.json["stats"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
