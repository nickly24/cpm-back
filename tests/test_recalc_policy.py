import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAM = ROOT / "cpm_back" / "services" / "exam"


def _load(name, filename):
    path = EXAM / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("test_recalc_policy_under_test", "test_recalc_policy.py")
scoring = _load("scoring_under_test", "scoring.py")


def _opt(oid, text, correct=False):
    return {"id": oid, "text": text, "isCorrect": correct}


def _q(qid, qtype="single", points=1, answers=None, text="Q", correct_answers=None):
    item = {
        "questionId": qid,
        "type": qtype,
        "text": text,
        "points": points,
        "answers": answers if answers is not None else [],
        "correctAnswers": correct_answers if correct_answers is not None else [],
    }
    return item


def _test(questions):
    return {"_id": "t1", "title": "T", "questions": questions}


class DecideSessionRecalcTests(unittest.TestCase):
    def test_unchanged_skips(self):
        t = _test(
            [
                _q(
                    1,
                    answers=[_opt("a", "A", True), _opt("b", "B", False)],
                )
            ]
        )
        d = policy.decide_session_recalc(t, deepcopy(t))
        self.assertFalse(d.needs_recalc)
        self.assertEqual(d.reasons, ())

    def test_metadata_and_text_only_skip(self):
        before = _test(
            [_q(1, text="Old", answers=[_opt("a", "A", True), _opt("b", "B")])]
        )
        after = _test(
            [_q(1, text="New wording", answers=[_opt("a", "A", True), _opt("b", "B")])]
        )
        after["title"] = "Renamed"
        after["startDate"] = "2026-01-01"
        after["published"] = False
        d = policy.decide_session_recalc(before, after)
        self.assertFalse(d.needs_recalc)

    def test_option_text_only_skip(self):
        before = _test([_q(1, answers=[_opt("a", "A", True), _opt("b", "B")])])
        after = _test([_q(1, answers=[_opt("a", "A", True), _opt("b", "B renamed")])])
        d = policy.decide_session_recalc(before, after)
        self.assertFalse(d.needs_recalc)

    def test_add_question_skips(self):
        before = _test([_q(1, answers=[_opt("a", "A", True)])])
        after = _test(
            [
                _q(1, answers=[_opt("a", "A", True)]),
                _q(2, answers=[_opt("c", "C", True)]),
            ]
        )
        d = policy.decide_session_recalc(before, after)
        self.assertFalse(d.needs_recalc)

    def test_add_incorrect_option_skips(self):
        before = _test([_q(1, answers=[_opt("a", "A", True), _opt("b", "B")])])
        after = _test(
            [_q(1, answers=[_opt("a", "A", True), _opt("b", "B"), _opt("c", "C")])]
        )
        d = policy.decide_session_recalc(before, after)
        self.assertFalse(d.needs_recalc)

    def test_add_correct_option_skips(self):
        before = _test(
            [
                _q(
                    1,
                    qtype="multiple",
                    answers=[_opt("a", "A", True), _opt("b", "B")],
                )
            ]
        )
        after = _test(
            [
                _q(
                    1,
                    qtype="multiple",
                    answers=[_opt("a", "A", True), _opt("b", "B"), _opt("c", "C", True)],
                )
            ]
        )
        d = policy.decide_session_recalc(before, after)
        self.assertFalse(d.needs_recalc)

    def test_text_correct_only_add_skips(self):
        before = _test([_q(1, qtype="text", correct_answers=["alpha"])])
        after = _test([_q(1, qtype="text", correct_answers=["alpha", "beta"])])
        d = policy.decide_session_recalc(before, after)
        self.assertFalse(d.needs_recalc)

    def test_remove_question_recalcs(self):
        before = _test(
            [
                _q(1, answers=[_opt("a", "A", True)]),
                _q(2, answers=[_opt("b", "B", True)]),
            ]
        )
        after = _test([_q(1, answers=[_opt("a", "A", True)])])
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_QUESTION_REMOVED, d.reasons)

    def test_points_changed_recalcs(self):
        before = _test([_q(1, points=1, answers=[_opt("a", "A", True)])])
        after = _test([_q(1, points=3, answers=[_opt("a", "A", True)])])
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_POINTS_CHANGED, d.reasons)

    def test_correct_flag_changed_recalcs(self):
        before = _test([_q(1, answers=[_opt("a", "A", True), _opt("b", "B")])])
        after = _test([_q(1, answers=[_opt("a", "A"), _opt("b", "B", True)])])
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_CORRECT_FLAG_CHANGED, d.reasons)

    def test_remove_correct_option_recalcs(self):
        before = _test([_q(1, answers=[_opt("a", "A", True), _opt("b", "B")])])
        after = _test([_q(1, answers=[_opt("b", "B", True)])])
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_OPTION_REMOVED, d.reasons)

    def test_remove_incorrect_option_recalcs(self):
        before = _test([_q(1, answers=[_opt("a", "A", True), _opt("b", "B")])])
        after = _test([_q(1, answers=[_opt("a", "A", True)])])
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_OPTION_REMOVED, d.reasons)

    def test_text_correct_removed_recalcs(self):
        before = _test([_q(1, qtype="text", correct_answers=["alpha", "beta"])])
        after = _test([_q(1, qtype="text", correct_answers=["alpha"])])
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_TEXT_CORRECT_REMOVED, d.reasons)

    def test_type_change_recalcs_and_excludes(self):
        before = _test([_q(1, qtype="single", answers=[_opt("a", "A", True)])])
        after = _test(
            [
                _q(
                    1,
                    qtype="multiple",
                    answers=[_opt("a", "A", True), _opt("b", "B")],
                )
            ]
        )
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_QUESTION_TYPE_CHANGED, d.reasons)
        self.assertEqual(d.exclude_question_ids, frozenset({1}))

    def test_mixed_add_question_and_points_recalcs(self):
        before = _test([_q(1, points=1, answers=[_opt("a", "A", True)])])
        after = _test(
            [
                _q(1, points=5, answers=[_opt("a", "A", True)]),
                _q(2, answers=[_opt("c", "C", True)]),
            ]
        )
        d = policy.decide_session_recalc(before, after)
        self.assertTrue(d.needs_recalc)
        self.assertIn(policy.REASON_POINTS_CHANGED, d.reasons)
        self.assertEqual(d.exclude_question_ids, frozenset())


class RebuildScopedSessionAnswersTests(unittest.TestCase):
    def test_does_not_inject_new_questions(self):
        questions = [
            _q(1, points=1, answers=[_opt("a", "A", True), _opt("b", "B")]),
            _q(2, points=1, answers=[_opt("c", "C", True)]),
        ]
        session_answers = [
            {
                "questionId": 1,
                "type": "single",
                "selectedAnswer": "a",
                "points": 1,
                "isCorrect": True,
            }
        ]
        new_answers, score = scoring.rebuild_scoped_session_answers(
            session_answers, questions
        )
        self.assertEqual(len(new_answers), 1)
        self.assertEqual(new_answers[0]["questionId"], 1)
        self.assertEqual(score, 100)

    def test_drops_deleted_and_excluded_questions(self):
        questions = [
            _q(1, points=2, answers=[_opt("a", "A", True)]),
            _q(3, points=2, answers=[_opt("c", "C", True)]),
        ]
        session_answers = [
            {
                "questionId": 1,
                "type": "single",
                "selectedAnswer": "a",
                "points": 1,
                "isCorrect": True,
            },
            {
                "questionId": 2,
                "type": "single",
                "selectedAnswer": "x",
                "points": 1,
                "isCorrect": True,
            },
            {
                "questionId": 3,
                "type": "single",
                "selectedAnswer": "c",
                "points": 1,
                "isCorrect": True,
            },
        ]
        new_answers, score = scoring.rebuild_scoped_session_answers(
            session_answers,
            questions,
            exclude_question_ids={3},
        )
        self.assertEqual([a["questionId"] for a in new_answers], [1])
        self.assertEqual(new_answers[0]["points"], 2)
        self.assertEqual(score, 100)

    def test_recomputes_after_correct_answer_change(self):
        questions = [
            _q(1, points=1, answers=[_opt("a", "A"), _opt("b", "B", True)]),
        ]
        session_answers = [
            {
                "questionId": 1,
                "type": "single",
                "selectedAnswer": "a",
                "points": 1,
                "isCorrect": True,
            }
        ]
        new_answers, score = scoring.rebuild_scoped_session_answers(
            session_answers, questions
        )
        self.assertFalse(new_answers[0]["isCorrect"])
        self.assertEqual(new_answers[0]["points"], 0)
        self.assertEqual(score, 0)

    def test_points_change_updates_score_percent(self):
        questions = [
            _q(1, points=1, answers=[_opt("a", "A", True)]),
            _q(2, points=3, answers=[_opt("b", "B", True)]),
        ]
        session_answers = [
            {
                "questionId": 1,
                "type": "single",
                "selectedAnswer": "a",
                "points": 1,
                "isCorrect": True,
            },
            {
                "questionId": 2,
                "type": "single",
                "selectedAnswer": "wrong",
                "points": 0,
                "isCorrect": False,
            },
        ]
        _, score = scoring.rebuild_scoped_session_answers(session_answers, questions)
        # earned 1 / max 4 = 25
        self.assertEqual(score, 25)


if __name__ == "__main__":
    unittest.main()
