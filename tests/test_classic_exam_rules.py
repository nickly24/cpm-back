"""Offline domain tests: no application factory, network or production data."""

from decimal import Decimal
import random
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask
from mysql.connector.errors import PoolError

from cpm_back.services.exams.common import (
    ExamError,
    integer,
    decimal_number,
    enum_value,
    text_value,
    parse_datetime,
)
from cpm_back.services.exams.scoring import (
    vote_value,
    validate_thresholds,
    grade_for_score,
    round_total,
    resolve_extra_vote,
)
from cpm_back.services.exams.selection import select_question
from cpm_back.services.exams.common import transaction, endpoint


class ClassicExamRulesTests(unittest.TestCase):
    def test_pool_overload_wait_is_bounded_and_never_starts_transaction(self):
        app = Flask(__name__)

        @app.get("/local-overload")
        @endpoint("examinator")
        def overloaded(actor):
            with transaction() as db:
                raise AssertionError("An exhausted pool cannot enter the transaction")

        with patch(
            "cpm_back.auth.jwt_auth.get_current_user",
            return_value={"role": "examinator", "id": 1},
        ), patch(
            "cpm_back.db.mysql_pool.get_db_connection",
            side_effect=PoolError("Pool exhausted"),
        ) as get, patch(
            "cpm_back.services.exams.common.time.monotonic", side_effect=[10, 10.5]
        ), patch(
            "cpm_back.services.exams.common.time.sleep"
        ) as sleep:
            response = app.test_client().get("/local-overload")
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"], "exam_temporarily_unavailable")
        self.assertEqual(response.headers["Retry-After"], "1")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_short_pool_exhaustion_retries_only_connection_acquisition(self):
        connection = MagicMock()
        with patch(
            "cpm_back.db.mysql_pool.get_db_connection",
            side_effect=[PoolError("Pool exhausted"), connection],
        ) as get, patch(
            "cpm_back.services.exams.common.time.monotonic", side_effect=[10, 10.1]
        ), patch(
            "cpm_back.services.exams.common.time.sleep"
        ) as sleep:
            with transaction(write=True):
                pass
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once()
        connection.start_transaction.assert_called_once_with(
            isolation_level="READ COMMITTED"
        )
        connection.commit.assert_called_once()

    def test_exact_numeric_validation(self):
        for value in (True, False, 1.0, "1", None, [], {}):
            with self.subTest(value=value), self.assertRaises(ExamError):
                integer(value)
        for value in (True, "1", float("nan"), float("inf"), -1, 1.001, 10000000000):
            with self.subTest(value=value), self.assertRaises(ExamError):
                decimal_number(value)
        self.assertEqual(decimal_number(0), Decimal("0"))
        self.assertEqual(decimal_number(9999999999.99), Decimal("9999999999.99"))
        for value in (True, {}, [], None, "0.5", 0.25, float("nan")):
            with self.subTest(value=value), self.assertRaises(ExamError):
                vote_value(value)
        for value in (None, {}, [], 1, False):
            with self.subTest(value=value), self.assertRaises(ExamError):
                enum_value(value, {"up": "up"}, "mode")

    def test_time_and_text(self):
        self.assertEqual(parse_datetime("2026-09-12T10:00:00+03:00").hour, 7)
        for value in ("2026-09-12T10:00:00", True, {}, "bad"):
            with self.assertRaises(ExamError):
                parse_datetime(value)
        self.assertEqual(text_value("  A\r\nB\rC  ", "q", None, 100), "A\nB\nC")
        self.assertEqual(len(text_value("я" * 30720, "q", None, 61440)), 30720)
        with self.assertRaises(ExamError):
            text_value("я" * 30721, "q", None, 61440)

    def test_thresholds_and_every_half_score(self):
        rows = [{"grade": i, "minScore": i} for i in range(6)]
        self.assertEqual(validate_thresholds(5, rows), [])
        for maximum in range(5, 50):
            for numerator in range(maximum * 2 + 1):
                score = Decimal(numerator) / 2
                for direction in ("up", "down"):
                    rounded = round_total(score, direction)
                    self.assertGreaterEqual(rounded, 0)
                    self.assertLessEqual(rounded, maximum)
                    self.assertEqual(grade_for_score(rounded, rows), min(5, rounded))
        for rows_bad in (
            [],
            rows[:-1],
            rows + rows[:1],
            [dict(r, minScore=0) for r in rows],
        ):
            self.assertTrue(validate_thresholds(5, rows_bad))
        self.assertTrue(validate_thresholds(4, rows))
        self.assertEqual(resolve_extra_vote(Decimal(".5"), "repeat"), "repeat")
        self.assertEqual(resolve_extra_vote(Decimal("1"), "repeat"), "up")

    def test_any_part_singleton_cycles_alternate(self):
        questions = [{"id": 1, "source_part_id": 1}, {"id": 2, "source_part_id": 2}]
        progress = [
            {
                "source_part_id": i,
                "required_count": 1,
                "consensus_count": 1,
                "cycle_no": 1,
            }
            for i in (1, 2)
        ]
        usage = {
            i: {
                "last_cycle_no": 1,
                "ever_presented": True,
                "permanently_excluded": False,
            }
            for i in (1, 2)
        }
        last = 2
        for _ in range(2000):
            q, part, cycle = select_question(
                questions,
                usage,
                progress,
                "tie_breaker",
                last,
                "any_part",
                rng=random.Random(1),
            )
            self.assertNotEqual(q["id"], last)
            usage[q["id"]]["last_cycle_no"] = cycle
            next(p for p in progress if p["source_part_id"] == part)["cycle_no"] = cycle
            last = q["id"]

    def test_replacement_never_reuses_excluded_questions(self):
        questions = [{"id": i, "source_part_id": 1} for i in range(1, 6)]
        progress = [
            {
                "source_part_id": 1,
                "required_count": 1,
                "consensus_count": 0,
                "cycle_no": 1,
            }
        ]
        usage = {
            1: {
                "last_cycle_no": 1,
                "ever_presented": True,
                "permanently_excluded": True,
            }
        }
        q, _, _ = select_question(questions, usage, progress, "regular", exclude=2)
        self.assertNotIn(q["id"], (1, 2))
        usage.update(
            {
                i: {
                    "last_cycle_no": 1,
                    "ever_presented": True,
                    "permanently_excluded": True,
                }
                for i in range(2, 6)
            }
        )
        with self.assertRaises(ExamError):
            select_question(
                questions, usage, progress, "tie_breaker", 1, "specific_part", 1
            )
