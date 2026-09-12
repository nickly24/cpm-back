"""Offline verification of the explicitly approved legacy cleanup policy."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "cleanup", Path(__file__).resolve().parents[1] / "scripts/exams_cleanup_legacy.py"
)
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


class CleanupTests(unittest.TestCase):
    def row(self, id, student=1, grade=5):
        return dict(
            id=id,
            exam_id=53,
            student_id=student,
            val=12.0,
            points=float(grade),
            examinator="examiner",
        )

    def test_newest_id_wins_even_when_grade_is_lower(self):
        plan = cleanup.cleanup_plan(
            [self.row(1), self.row(3, grade=4), self.row(2)], {1}
        )
        self.assertEqual(plan["olderDuplicateIds"], [1, 2])

    def test_all_missing_student_rows_are_removed_once(self):
        plan = cleanup.cleanup_plan(
            [self.row(1, student=9), self.row(2, student=9)], {1}
        )
        self.assertEqual(plan["deleteIds"], [1, 2])
        self.assertEqual(plan["olderDuplicateIds"], [])

    def test_valid_rows_untouched(self):
        self.assertEqual(cleanup.cleanup_plan([self.row(1)], {1})["deleteIds"], [])

    def test_reviewed_plan_never_authorizes_orphan_exam_deletion(self):
        report = {
            "duplicateResults": [],
            "orphanResults": [{"id": 1, "missingStudent": 0}],
        }
        self.assertEqual(cleanup.reviewed_ids(report), set())

    def test_backup_restores_exact_values_and_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [self.row(1)]
            path = cleanup.save_backup(
                Path(folder),
                rows,
                "CREATE TABLE exam_sessions (...)",
                {"deleteIds": [1]},
            )
            self.assertEqual(json.loads(path.read_text())["rows"], rows)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_fingerprint_changes_when_grade_changes(self):
        self.assertNotEqual(
            cleanup.fingerprint([self.row(1)]),
            cleanup.fingerprint([self.row(1, grade=4)]),
        )


if __name__ == "__main__":
    unittest.main()
