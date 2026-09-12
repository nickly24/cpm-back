"""XLSX/parser and opt-in local MySQL/Flask exam-import integration tests.

EXAM_MIGRATION_TEST_SOCKET must explicitly point at the disposable local server.
No configured production database, Flask application factory, or Mongo is used.
"""

from datetime import datetime, timedelta
from io import BytesIO
import importlib
import os
import unittest
from unittest.mock import patch
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from tests import test_classic_exam_results as result_fixture

common = result_fixture.common
imports = importlib.import_module(result_fixture.DOMAIN + ".imports")
admin = importlib.import_module(result_fixture.DOMAIN + ".admin")


def workbook_bytes(rows, extra_sheets=()):
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    for title, sheet_rows in extra_sheets:
        sheet = workbook.create_sheet(title)
        for row in sheet_rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def raw_row(input, number=2, excluded=False):
    return {
        "rowId": "row-" + str(number),
        "sourceRow": number,
        "excluded": excluded,
        "input": input,
    }


class XLSXParserTests(unittest.TestCase):
    def test_question_aliases_case_and_whitespace(self):
        content = workbook_bytes(
            [[" ЧАСТЬ ", "Вопрос", "Эталонный ответ"], ["a", "Что?", "Ответ"]],
            [("Empty", [])],
        )
        self.assertEqual(
            imports.parse_xlsx(content, "QUESTIONS.XLSX", "questions"),
            [raw_row({"partCode": "a", "questionText": "Что?", "answerText": "Ответ"})],
        )

    def test_zero_values_and_numeric_logins_are_preserved(self):
        content = workbook_bytes(
            [
                ["student_login", "points", "grade", "examinator"],
                [2081, 0, 0, "Преподаватель"],
            ]
        )
        parsed = imports.parse_xlsx(content, "grades.xlsx", "outside")[0]["input"]
        self.assertEqual(parsed["points"], 0)
        self.assertEqual(parsed["grade"], 0)
        self.assertEqual(parsed["studentLogin"], 2081)

    def test_formula_boolean_date_and_excel_errors_remain_invalid_markers(self):
        content = workbook_bytes(
            [
                ["student_id", "points", "grade", "examinator"],
                [2081, "=1+1", True, datetime(2026, 9, 12)],
            ]
        )
        parsed = imports.parse_xlsx(content, "grades.xlsx", "outside")[0]["input"]
        self.assertEqual(parsed["points"]["invalidExcelCell"], "f")
        self.assertIn("invalidExcelCell", parsed["grade"])
        self.assertIn("invalidExcelCell", parsed["examinator"])

    def test_unknown_duplicate_headers_and_missing_headers_are_rejected(self):
        cases = [
            (
                ["part_code", "Часть", "question_text", "answer_text"],
                "duplicate_import_headers",
            ),
            (
                ["part_code", "question_text", "answer_text", "execute"],
                "unknown_import_header",
            ),
            (["part_code", "question_text"], "missing_import_headers"),
        ]
        for headers, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(common.ExamError) as error:
                    imports.parse_xlsx(
                        workbook_bytes([headers, ["A", "B", "C", "D"][: len(headers)]]),
                        "bank.xlsx",
                        "questions",
                    )
                self.assertEqual(error.exception.code, code)

    def test_multiple_nonempty_sheets_are_rejected(self):
        data = [["part_code", "question_text", "answer_text"], ["A", "Q", "A"]]
        with self.assertRaises(common.ExamError) as error:
            imports.parse_xlsx(
                workbook_bytes(data, [("Another", data)]), "bank.xlsx", "questions"
            )
        self.assertEqual(error.exception.code, "multiple_import_sheets")

    def test_column_and_data_row_limits(self):
        with self.assertRaises(common.ExamError) as error:
            imports.parse_xlsx(
                workbook_bytes([["x"] * 33, ["x"] * 33]), "bank.xlsx", "questions"
            )
        self.assertEqual(error.exception.code, "import_columns_limit")
        with self.assertRaises(common.ExamError) as error:
            imports.parse_xlsx(
                workbook_bytes(
                    [["part_code", "question_text", "answer_text"]]
                    + [["A", "Q", "A"]] * 5001
                ),
                "bank.xlsx",
                "questions",
            )
        self.assertEqual(error.exception.code, "import_rows_limit")

    def test_invalid_file_size_and_extension(self):
        for data, filename, expected in (
            (b"not a zip", "fake.xlsx", "invalid_import_file"),
            (b"x", "file.xls", "invalid_import_file"),
            (b"x" * (imports.MAX_FILE + 1), "big.xlsx", "payload_too_large"),
        ):
            with self.assertRaises(common.ExamError) as error:
                imports.parse_xlsx(data, filename, "outside")
            self.assertEqual(error.exception.code, expected)

    def test_entity_declarations_are_rejected_before_openpyxl(self):
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr(
                "xl/workbook.xml", '<!DOCTYPE x [<!ENTITY boom "bad">]><x/>'
            )
        with self.assertRaises(common.ExamError) as error:
            imports.parse_xlsx(output.getvalue(), "unsafe.xlsx", "questions")
        self.assertEqual(error.exception.code, "invalid_import_file")
        self.assertIn("DTD", error.exception.message)


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit local MySQL socket not configured",
)
class LocalImportTests(unittest.TestCase):
    setUp = result_fixture.ClassicResultTests.setUp
    tearDown = result_fixture.ClassicResultTests.tearDown
    query = result_fixture.ClassicResultTests.query
    http_api = result_fixture.ClassicResultTests.http_api

    def session(self, kind, rows, exam_id=None):
        exam_id = self.exam_id if exam_id is None else exam_id
        session = imports.create_session(
            self.db, kind, exam_id, self.admin, "upload.xlsx", rows
        )
        self.connection.commit()
        return session

    def row(self, kind, session, exam_id=None, actor=None):
        return imports.session_row(
            self.db,
            kind,
            session["id"],
            actor or self.admin,
            self.exam_id if exam_id is None else exam_id,
            lock=True,
        )

    def add_student(self, identifier=2082, login="student2"):
        self.db.execute(
            "INSERT INTO students(id,full_name,class,tg_name) VALUES(%s,%s,10,'')",
            (identifier, "Студент " + str(identifier)),
        )
        self.db.execute(
            "INSERT INTO auth_users(ref_id,role,username) VALUES(%s,'student',%s)",
            (identifier, login),
        )
        self.connection.commit()

    def test_owner_role_scope_and_expiry(self):
        session = self.session(
            "questions",
            [raw_row({"partCode": "A", "questionText": "Q", "answerText": "A"})],
        )
        for actor, exam in (
            ({"role": "admin", "id": 2}, self.exam_id),
            ({"role": "staff_admin", "id": 1}, self.exam_id),
            (self.admin, 53),
        ):
            with self.assertRaises(common.ExamError) as error:
                imports.session_row(self.db, "questions", session["id"], actor, exam)
            self.assertEqual(error.exception.status, 404)
        self.db.execute(
            "UPDATE classic_exam_question_import_sessions SET expires_at=%s WHERE id=%s",
            (common.now() - timedelta(seconds=1), session["id"]),
        )
        self.connection.commit()
        with self.assertRaises(common.ExamError) as error:
            self.row("questions", session)
        self.assertEqual(error.exception.status, 410)

    def test_duplicate_edit_exclude_and_undo_recompute_all_rows(self):
        session = self.session(
            "questions",
            [
                raw_row({"partCode": "A", "questionText": " Q\r\n", "answerText": "A"}),
                raw_row({"partCode": "a", "questionText": "Q", "answerText": "A"}, 3),
            ],
        )
        self.assertEqual(session["summary"]["errors"], 2)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM classic_exam_parts"), 0)
        updated = imports.update_session(
            self.db,
            "questions",
            self.row("questions", session),
            {
                "expectedPreviewVersion": 1,
                "changes": [{"rowId": "row-3", "excluded": True}],
            },
        )
        self.connection.commit()
        self.assertEqual(updated["summary"]["errors"], 0)
        self.assertEqual(updated["previewVersion"], 2)
        self.assertEqual([row["rowId"] for row in updated["rows"]], ["row-3"])
        with self.assertRaises(common.ExamError) as error:
            imports.update_session(
                self.db,
                "questions",
                self.row("questions", session),
                {"expectedPreviewVersion": 1, "changes": []},
            )
        self.assertEqual(error.exception.code, "import_preview_modified")
        undo = imports.update_session(
            self.db,
            "questions",
            self.row("questions", session),
            {
                "expectedPreviewVersion": 2,
                "changes": [{"rowId": "row-3", "excluded": False}],
            },
        )
        self.assertEqual(undo["summary"]["errors"], 2)

    def test_preview_cannot_inject_new_rows_or_resolved_values(self):
        session = self.session(
            "questions",
            [raw_row({"partCode": "A", "questionText": "Q", "answerText": "A"})],
        )
        for change in (
            {"rowId": "row-99", "excluded": True},
            {"rowId": "row-2", "resolved": {"partId": 99}},
            {"rowId": "row-2", "excluded": 1},
            {"rowId": "row-2", "input": []},
        ):
            with self.assertRaises(common.ExamError):
                imports.update_session(
                    self.db,
                    "questions",
                    self.row("questions", session),
                    {"expectedPreviewVersion": 1, "changes": [change]},
                )
        self.assertEqual(self.row("questions", session)["preview_version"], 1)

    def test_questions_commit_is_atomic_and_config_bumps_once(self):
        session = self.session(
            "questions",
            [
                raw_row({"partCode": "A", "questionText": "Q1", "answerText": "A1"}),
                raw_row({"partCode": "B", "questionText": "Q2", "answerText": "A2"}, 3),
            ],
        )
        before = self.db.scalar(
            "SELECT config_version FROM classic_exam_settings WHERE exam_id=%s",
            (self.exam_id,),
        )
        result = imports.commit_session(
            self.db,
            "questions",
            self.row("questions", session),
            self.admin,
            {"expectedPreviewVersion": 1},
        )
        self.connection.commit()
        self.assertEqual(result["counts"]["partsCreated"], 2)
        self.assertEqual(result["counts"]["questionsCreated"], 2)
        self.assertEqual(
            self.db.scalar(
                "SELECT config_version FROM classic_exam_settings WHERE exam_id=%s",
                (self.exam_id,),
            ),
            before + 1,
        )
        repeated = imports.commit_session(
            self.db,
            "questions",
            self.row("questions", session),
            self.admin,
            {"expectedPreviewVersion": 1},
        )
        self.assertTrue(repeated["replayed"])
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_questions"), 2
        )
        self.assertGreater(
            self.row("questions", session)["expires_at"],
            common.now() + timedelta(days=6),
        )
        committed_dto = imports.session_dto(
            self.db, "questions", self.row("questions", session)
        )
        self.assertEqual(
            committed_dto["summary"],
            {
                "total": 2,
                "included": 2,
                "excluded": 0,
                "creatable": 0,
                "conflicts": 0,
                "errors": 0,
            },
        )
        self.assertEqual(committed_dto["commitResult"]["counts"]["questionsCreated"], 2)

    def test_partial_sql_failure_rolls_back_all_domain_rows(self):
        session = self.session(
            "questions",
            [
                raw_row({"partCode": "A", "questionText": "Q1", "answerText": "A1"}),
                raw_row({"partCode": "B", "questionText": "Q2", "answerText": "A2"}, 3),
            ],
        )
        original = admin.save_question
        call_count = [0]

        def fail_second(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("simulated SQL failure after first created question")
            return original(*args, **kwargs)

        with patch.object(admin, "save_question", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                imports.commit_session(
                    self.db,
                    "questions",
                    self.row("questions", session),
                    self.admin,
                    {"expectedPreviewVersion": 1},
                )
        self.connection.rollback()
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_questions"), 0
        )
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM classic_exam_parts"), 0)
        self.assertEqual(self.row("questions", session)["status"], "editable")
        self.assertEqual(
            self.db.scalar(
                "SELECT config_version FROM classic_exam_settings WHERE exam_id=%s",
                (self.exam_id,),
            ),
            1,
        )

    def test_privilege_changed_after_preview_blocks_every_assignment(self):
        self.add_student()
        rows = [
            raw_row({"studentId": 2081, "examinator1Id": 1, "replacementLimit": 1}),
            raw_row({"studentId": 2082, "examinator1Id": 2, "replacementLimit": 2}, 3),
        ]
        session = self.session("assignments", rows)
        self.assertEqual(session["summary"]["errors"], 0)
        self.assertNotIn("privilegeBaselineVersion", session["rows"][0])
        admin.save_privilege(
            self.db, self.exam_id, 2081, {"replacementLimit": 3, "expectedVersion": 0}
        )
        self.connection.commit()
        with self.assertRaises(common.ExamError) as error:
            imports.commit_session(
                self.db,
                "assignments",
                self.row("assignments", session),
                self.admin,
                {"expectedPreviewVersion": 1},
            )
        self.assertEqual(error.exception.code, "import_conflict")
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM classic_exam_assignments"), 0
        )
        updated = imports.update_session(
            self.db,
            "assignments",
            self.row("assignments", session),
            {"expectedPreviewVersion": 1, "changes": [{"rowId": "row-2"}]},
        )
        self.connection.commit()
        self.assertEqual(updated["summary"]["errors"], 0)
        successful = imports.commit_session(
            self.db,
            "assignments",
            self.row("assignments", session),
            self.admin,
            {"expectedPreviewVersion": updated["previewVersion"]},
        )
        self.connection.commit()
        self.assertEqual(successful["counts"]["assignmentsCreated"], 2)
        self.assertEqual(
            self.db.scalar(
                "SELECT replacement_limit FROM classic_exam_student_privileges WHERE exam_id=%s AND student_id=2081",
                (self.exam_id,),
            ),
            1,
        )

    def test_numeric_login_never_becomes_id_and_dual_identity_conflict(self):
        self.add_student(2082, "2081")
        person = imports.resolve_person(self.db, "student", login=2081)
        self.assertEqual(person["id"], 2082)
        with self.assertRaises(common.ExamError) as error:
            imports.resolve_person(self.db, "student", id_value=2081, login=2081)
        self.assertEqual(error.exception.code, "identifier_login_conflict")

    def test_student_change_refreshes_baseline_only_for_explicitly_edited_row(self):
        self.add_student()
        session = self.session(
            "assignments",
            [
                raw_row({"studentId": 2081, "examinator1Id": 1, "replacementLimit": 1}),
                raw_row(
                    {"studentId": 2082, "examinator1Id": 2, "replacementLimit": 2}, 3
                ),
            ],
        )
        admin.save_privilege(
            self.db, self.exam_id, 2082, {"replacementLimit": 3, "expectedVersion": 0}
        )
        self.connection.commit()
        updated = imports.update_session(
            self.db,
            "assignments",
            self.row("assignments", session),
            {"expectedPreviewVersion": 1, "changes": [{"rowId": "row-2"}]},
        )
        self.assertEqual(updated["summary"]["errors"], 1)
        with self.assertRaises(common.ExamError):
            imports.commit_session(
                self.db,
                "assignments",
                self.row("assignments", session),
                self.admin,
                {"expectedPreviewVersion": updated["previewVersion"]},
            )

    def test_outside_existing_result_is_conflict_and_zero_grade_imports(self):
        conflict = self.session(
            "outside",
            [
                raw_row(
                    {
                        "studentId": 2081,
                        "points": 0,
                        "grade": 0,
                        "examinator": "Преподаватель",
                    }
                )
            ],
            53,
        )
        self.assertEqual(conflict["summary"]["errors"], 1)
        outside = self.db.insert(
            "exams",
            {
                "name": None,
                "date": "2026-09-12",
                "exam_type": "outside_lms",
                "direction_id": 6,
            },
        )
        session = self.session(
            "outside",
            [
                raw_row(
                    {
                        "studentId": 2081,
                        "points": 0,
                        "grade": 0,
                        "examinator": "Преподаватель",
                    }
                )
            ],
            outside,
        )
        self.assertEqual(session["summary"]["errors"], 0)
        result = imports.commit_session(
            self.db,
            "outside",
            self.row("outside", session, outside),
            self.admin,
            {"expectedPreviewVersion": 1},
        )
        self.connection.commit()
        self.assertEqual(result["counts"]["resultsCreated"], 1)
        self.assertEqual(
            self.db.scalar(
                "SELECT points FROM exam_sessions WHERE exam_id=%s", (outside,)
            ),
            0,
        )

    def test_commission_reuse_is_unordered_and_no_accounts_created(self):
        self.add_student()
        before = self.db.scalar("SELECT COUNT(*) FROM auth_users")
        session = self.session(
            "assignments",
            [
                raw_row({"studentId": 2081, "examinator1Id": 1, "examinator2Id": 2}),
                raw_row({"studentId": 2082, "examinator1Id": 2, "examinator2Id": 1}, 3),
            ],
        )
        result = imports.commit_session(
            self.db,
            "assignments",
            self.row("assignments", session),
            self.admin,
            {"expectedPreviewVersion": 1},
        )
        self.connection.commit()
        self.assertEqual(result["counts"]["commissionsCreated"], 1)
        self.assertEqual(result["counts"]["assignmentsCreated"], 2)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM auth_users"), before)

    def test_packet_budget_and_all_excluded_are_explicit_errors(self):
        session = self.session(
            "questions",
            [
                raw_row(
                    {"partCode": "A", "questionText": "Q", "answerText": "A"},
                    excluded=True,
                )
            ],
        )
        with self.assertRaises(common.ExamError) as error:
            imports.commit_session(
                self.db,
                "questions",
                self.row("questions", session),
                self.admin,
                {"expectedPreviewVersion": 1},
            )
        self.assertEqual(error.exception.code, "import_no_rows")
        with patch.object(self.db, "scalar", return_value=4096):
            with self.assertRaises(common.ExamError) as error:
                imports.payload_json(self.db, [raw_row({"questionText": "Q"})])
        self.assertEqual(error.exception.status, 413)

    def test_http_parse_put_commit_replay_and_owner_enforcement(self):
        # The fixture maps the real blueprint to this isolated domain package.
        actor = [self.admin]
        content = workbook_bytes(
            [["part_code", "question_text", "answer_text"], ["A", "Q", "A"]]
        )
        with self.http_api("exam_imports_bp.py", "exam_imports_bp", actor) as client:
            prefix = f"/api/exams/{self.exam_id}/imports/questions"
            headers = {
                "Authorization": "Bearer local-test",
                "Idempotency-Key": str(uuid.uuid4()),
            }
            created = client.post(
                prefix + "/parse",
                data={"file": (BytesIO(content), "questions.xlsx")},
                headers=headers,
            )
            self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
            session_id = created.get_json()["data"]["session"]["id"]
            repeated = client.post(
                prefix + "/parse",
                data={"file": (BytesIO(content), "questions.xlsx")},
                headers=headers,
            )
            self.assertEqual(repeated.status_code, 200, repeated.get_data(as_text=True))
            self.assertEqual(repeated.get_json()["data"]["session"]["id"], session_id)
            actor[0] = {"role": "staff_admin", "id": 1}
            self.assertEqual(
                client.get(prefix + f"/sessions/{session_id}").status_code, 404
            )
            actor[0] = self.admin
            headers["Idempotency-Key"] = str(uuid.uuid4())
            update = client.put(
                prefix + f"/sessions/{session_id}",
                json={
                    "expectedPreviewVersion": 1,
                    "changes": [
                        {
                            "rowId": "row-2",
                            "input": {
                                "partCode": "A",
                                "questionText": "Changed",
                                "answerText": "A",
                            },
                        }
                    ],
                },
                headers=headers,
            )
            self.assertEqual(update.status_code, 200, update.get_data(as_text=True))
            version = update.get_json()["data"]["session"]["previewVersion"]
            headers["Idempotency-Key"] = str(uuid.uuid4())
            committed = client.post(
                prefix + f"/sessions/{session_id}/commit",
                json={"expectedPreviewVersion": version},
                headers=headers,
            )
            self.assertEqual(
                committed.status_code, 200, committed.get_data(as_text=True)
            )
            repeated = client.post(
                prefix + f"/sessions/{session_id}/commit",
                json={"expectedPreviewVersion": version},
                headers=headers,
            )
            self.assertTrue(repeated.get_json()["data"]["result"]["replayed"])
            self.assertEqual(
                self.db.scalar("SELECT COUNT(*) FROM classic_exam_questions"), 1
            )
            altered = client.post(
                prefix + f"/sessions/{session_id}/commit",
                json={"expectedPreviewVersion": version + 1},
                headers=headers,
            )
            self.assertEqual(altered.status_code, 409)


if __name__ == "__main__":
    unittest.main()
