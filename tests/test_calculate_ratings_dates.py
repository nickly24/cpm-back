import importlib.util
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "cpm_back"
    / "services"
    / "exam"
    / "calculate_ratings.py"
)
SPEC = importlib.util.spec_from_file_location("calculate_ratings_under_test", MODULE_PATH)
calculate_ratings = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(calculate_ratings)


class RatingTestDateTests(unittest.TestCase):
    def test_moscow_offset_start_date_is_eligible(self):
        self.assertTrue(
            calculate_ratings._is_mongo_test_eligible_for_rating(
                {"startDate": "2026-07-18T09:00:00+03:00"},
                calculate_ratings._coerce_rating_date("2026-07-18"),
                calculate_ratings._coerce_rating_date("2026-07-18"),
            )
        )

    def test_utc_start_date_is_converted_to_moscow(self):
        parsed = calculate_ratings._parse_test_start_date("2026-07-17T22:30:00Z")
        self.assertEqual(parsed.isoformat(), "2026-07-18T01:30:00+03:00")

    def test_naive_legacy_start_date_is_interpreted_as_moscow(self):
        parsed = calculate_ratings._parse_test_start_date("2026-07-18T09:00:00")
        self.assertEqual(parsed.isoformat(), "2026-07-18T09:00:00+03:00")

    def test_datetime_and_date_boundaries_are_timezone_aware(self):
        for value in (
            "2026-07-18",
            date(2026, 7, 18),
            datetime(2026, 7, 18),
            datetime(2026, 7, 17, 21, tzinfo=timezone.utc),
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(calculate_ratings._coerce_rating_date(value).tzinfo)

    def test_overall_test_rating_is_average_of_direction_averages(self):
        direction_averages = {"Математика": 100.0, "Физика": 0.0}
        overall = calculate_ratings._average_direction_ratings(direction_averages)
        self.assertEqual(overall, 50.0)

    def test_direction_without_tests_does_not_participate(self):
        # A direction with no tests has no bucket and is not passed to the formula.
        direction_averages = {"Математика": 80.0}
        overall = calculate_ratings._average_direction_ratings(direction_averages)
        self.assertEqual(overall, 80.0)

    def test_no_test_directions_produce_zero_rating(self):
        self.assertEqual(calculate_ratings._average_direction_ratings({}), 0.0)

    def test_published_and_active_filters_are_preserved(self):
        date_from = calculate_ratings._coerce_rating_date("2026-07-18")
        date_to = calculate_ratings._coerce_rating_date("2026-07-18")
        self.assertFalse(
            calculate_ratings._is_mongo_test_eligible_for_rating(
                {"startDate": "2026-07-18T09:00:00+03:00", "published": False},
                date_from,
                date_to,
            )
        )
        self.assertFalse(
            calculate_ratings._is_mongo_test_eligible_for_rating(
                {"startDate": "2026-07-18T09:00:00+03:00", "isActive": False},
                date_from,
                date_to,
            )
        )


if __name__ == "__main__":
    unittest.main()
