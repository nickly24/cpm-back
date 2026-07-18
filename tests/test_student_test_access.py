import unittest
from datetime import datetime, timedelta

from cpm_back.services.exam.student_test_access import resolve_student_test_access
from cpm_back.services.exam.test_time import MOSCOW_TZ


class StudentTestAccessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 18, 12, 0, tzinfo=MOSCOW_TZ)

    def test_hidden_test_blocks_review_and_practice_after_completion(self):
        access = resolve_student_test_access(
            {"visible": False, "endDate": (self.now - timedelta(hours=1)).isoformat()},
            has_completed_session=True,
            current_time=self.now,
        )
        self.assertFalse(access.can_view_results)
        self.assertFalse(access.can_practice)
        self.assertEqual(access.practice_error, "practice_not_published")

    def test_visible_completed_test_allows_review_and_practice_while_active(self):
        access = resolve_student_test_access(
            {"visible": True, "endDate": (self.now + timedelta(hours=1)).isoformat()},
            has_completed_session=True,
            current_time=self.now,
        )
        self.assertTrue(access.can_view_results)
        self.assertTrue(access.can_practice)

    def test_visible_active_test_without_session_blocks_practice(self):
        access = resolve_student_test_access(
            {"visible": True, "endDate": (self.now + timedelta(hours=1)).isoformat()},
            has_completed_session=False,
            current_time=self.now,
        )
        self.assertFalse(access.can_view_results)
        self.assertFalse(access.can_practice)
        self.assertEqual(access.practice_error, "practice_before_official_completion")

    def test_visible_missed_test_allows_practice_but_not_review(self):
        access = resolve_student_test_access(
            {"visible": True, "endDate": (self.now - timedelta(seconds=1)).isoformat()},
            has_completed_session=False,
            current_time=self.now,
        )
        self.assertFalse(access.can_view_results)
        self.assertTrue(access.can_practice)

    def test_pending_official_attempt_blocks_practice_after_window(self):
        access = resolve_student_test_access(
            {"visible": True, "endDate": (self.now - timedelta(hours=1)).isoformat()},
            has_completed_session=False,
            has_open_official_attempt=True,
            current_time=self.now,
        )
        self.assertFalse(access.can_practice)
        self.assertEqual(access.practice_error, "official_attempt_pending")

    def test_external_test_is_never_changed_by_internal_policy(self):
        access = resolve_student_test_access(
            {"visible": True, "endDate": (self.now - timedelta(days=1)).isoformat()},
            has_completed_session=True,
            is_external=True,
            current_time=self.now,
        )
        self.assertFalse(access.can_view_results)
        self.assertFalse(access.can_practice)


if __name__ == "__main__":
    unittest.main()
