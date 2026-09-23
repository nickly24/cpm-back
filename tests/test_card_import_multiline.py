"""Offline XLSX → preview → import tests; never load the app or database config."""

import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from openpyxl import Workbook


SERVICES = Path(__file__).resolve().parents[1] / "cpm_back" / "services"
PACKAGE = "_offline_multiline_card_import"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SERVICES / "card_import")]
sys.modules[PACKAGE] = package


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parser = load_module(PACKAGE + ".parse_excel", SERVICES / "card_import/parse_excel.py")
preview_service = load_module(
    PACKAGE + ".build_preview", SERVICES / "card_import/build_preview.py"
)
transform = load_module(
    "_offline_multiline_card_transform", SERVICES / "card_transform/to_draft_canvas.py"
)


def no_database(*args, **kwargs):
    raise AssertionError("No database connections are allowed in multiline tests")


def stub_module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


# The runner's infrastructure dependencies are inert stubs at import time.
# Tests replace only these local module bindings with in-memory mocks.
with patch.dict(sys.modules, {
    "cpm_back.db.mysql_pool": stub_module(
        "cpm_back.db.mysql_pool", get_db_connection=no_database,
        close_db_connection=no_database,
    ),
    "cpm_back.services.cards.training_projection": stub_module(
        "cpm_back.services.cards.training_projection",
        manual_card_ref=lambda card_id: f"manual:{card_id}",
    ),
    "cpm_back.services.cards.training_progress": stub_module(
        "cpm_back.services.cards.training_progress",
        delete_progress_for_card_refs=no_database,
    ),
    "cpm_back.services.user_import.import_jobs": stub_module(
        "cpm_back.services.user_import.import_jobs", _update_job=no_database,
        get_import_job=no_database, save_job_results=no_database,
    ),
}):
    runner = load_module(
        PACKAGE + ".import_runner", SERVICES / "card_import/import_runner.py"
    )


def workbook_bytes(question, answer):
    workbook = Workbook()
    workbook.active.append(["Вопрос", "Ответ"])
    workbook.active.append([question, answer])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def preview_cursor(existing_cards=()):
    cursor = MagicMock()

    def execute(sql, params=None):
        if "FROM directions" in sql:
            cursor.fetchone.return_value = {"id": 1, "name": "Направление"}
        elif "FROM card_themes" in sql:
            cursor.fetchone.return_value = {"id": 2, "name": "Раздел", "direction_id": 1}
        elif "MAX(sort_order)" in sql:
            cursor.fetchone.return_value = {"max_order": 4}
        elif "SELECT question, answer" in sql:
            cursor.fetchall.return_value = list(existing_cards)

    cursor.execute.side_effect = execute
    cursor.lastrowid = 10
    return cursor


class CardImportMultilineTests(unittest.TestCase):
    def test_one_excel_row_stays_one_card_with_internal_blank_lines(self):
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=repr(newline)):
                parsed = parser.parse_cards_excel(workbook_bytes(
                    f"  Вопрос{newline}уточнение  ",
                    f" Первый; второй{newline}{newline}Третий / четвёртый, пятый ",
                ))
                self.assertTrue(parsed["status"])
                self.assertEqual(parsed["rows"], [{
                    "row": 2,
                    "question": "Вопрос\nуточнение",
                    "answer": "Первый; второй\n\nТретий / четвёртый, пятый",
                }])

    def test_numbers_and_plain_text_are_preserved(self):
        parsed = parser.parse_cards_excel(workbook_bytes(0, "Ответ; один / два, три"))
        self.assertEqual(parsed["rows"][0]["question"], "0")
        self.assertEqual(parsed["rows"][0]["answer"], "Ответ; один / два, три")

    def test_preview_edits_and_json_roundtrip_preserve_internal_linebreaks(self):
        parsed = parser.parse_cards_excel(workbook_bytes("Вопрос", "Один\nДва"))
        cursor = preview_cursor()
        preview = preview_service.build_preview_from_rows(
            cursor, parsed["rows"], direction_id=1, theme_id=2,
        )
        preview["cards"][0]["question"] = "Изменённый\r\nвопрос"
        preview["cards"][0]["answer"] = " Один\r\n\r\nНовый; пункт\rТри "
        saved = preview_service.rebuild_preview_from_cards(cursor, preview)
        persisted = json.loads(json.dumps(saved, ensure_ascii=False))
        rebuilt = preview_service.rebuild_preview_from_cards(cursor, persisted)
        self.assertEqual(rebuilt["cards"][0]["question"], "Изменённый\nвопрос")
        self.assertEqual(rebuilt["cards"][0]["answer"], " Один\n\nНовый; пункт\nТри ")
        self.assertEqual(rebuilt["summary"]["cards_to_import"], 1)
        self.assertIsNone(preview_service.validate_preview_for_commit(rebuilt))

    def test_autosaving_preview_keeps_the_enter_that_starts_a_new_line(self):
        cursor = preview_cursor()
        preview = preview_service.build_preview_from_rows(
            cursor, [{"row": 2, "question": "Вопрос", "answer": "Один"}],
            direction_id=1, theme_id=2,
        )
        preview["cards"][0]["answer"] += "\r\n"
        saved = preview_service.rebuild_preview_from_cards(cursor, preview)
        self.assertEqual(saved["cards"][0]["answer"], "Один\n")
        saved["cards"][0]["answer"] += "Два"
        saved = preview_service.rebuild_preview_from_cards(cursor, saved)
        self.assertEqual(saved["cards"][0]["answer"], "Один\nДва")

    def test_duplicate_detection_does_not_flatten_stored_text(self):
        cursor = preview_cursor([{"question": "Вопрос", "answer": "Один Два"}])
        preview = preview_service.build_preview_from_rows(
            cursor, [{"row": 2, "question": "Вопрос", "answer": "Один\n\nДва"}],
            direction_id=1, theme_id=2,
        )
        self.assertEqual(preview["cards"][0]["action"], "warning")
        self.assertEqual(preview["cards"][0]["answer"], "Один\n\nДва")

    def test_import_writes_single_multiline_answer_then_draft_keeps_it_whole(self):
        parsed = parser.parse_cards_excel(workbook_bytes("Вопрос\nУточнение", "Один\nДва"))
        cursor = preview_cursor()
        preview = preview_service.build_preview_from_rows(
            cursor, parsed["rows"], direction_id=1, theme_id=2,
        )
        preview["cards"][0]["answer"] = "Один\r\n\r\nДва; три"
        # A real persisted preview is JSON; the runner rebuilds it before insertion.
        stored_preview = json.loads(json.dumps(preview, ensure_ascii=False))
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch.object(runner, "get_db_connection", return_value=connection), \
                patch.object(runner, "close_db_connection"), \
                patch.object(runner, "get_import_job", return_value={"session_id": 1}), \
                patch.object(runner, "_load_preview_for_job", return_value=stored_preview), \
                patch.object(runner, "save_job_results"), \
                patch.object(runner, "_update_job") as update_job:
            runner.run_cards_import(1)

        inserted = [
            call.args[1] for call in cursor.execute.call_args_list
            if "INSERT INTO cards" in call.args[0]
        ]
        self.assertEqual(inserted, [("Вопрос\nУточнение", "Один\n\nДва; три", 2, 5)])
        self.assertEqual(update_job.call_args.kwargs["status"], "completed")
        self.assertEqual(update_job.call_args.kwargs["successful"], 1)
        payload = transform.build_draft_payload({
            "cards": [{"id": 10, "question": inserted[0][0], "answer": inserted[0][1]}],
        })
        questions = payload["canvas"]["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["text"], "Вопрос\nУточнение")
        self.assertEqual(len(questions[0]["answers"]), 1)
        self.assertEqual(questions[0]["answers"][0]["text"], "Один\n\nДва; три")


if __name__ == "__main__":
    unittest.main()
