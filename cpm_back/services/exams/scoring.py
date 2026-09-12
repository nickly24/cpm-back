"""Exact, pure score calculations; extra questions resolve rounding only."""

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from .common import fail, integer

VOTES = (Decimal("0"), Decimal("0.5"), Decimal("1"))


def vote_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        fail("invalid_vote_value")
    result = Decimal(str(value))
    if result not in VOTES:
        fail("invalid_vote_value")
    return result


def calculate_max_score(parts):
    return sum(
        (p.get("question_weight") or p.get("weight") or 0)
        * (p.get("question_count") or p.get("quota") or 0)
        for p in parts
    )


def calculate_question_score(vote, weight):
    if not isinstance(vote, Decimal) or vote not in VOTES:
        fail("invalid_vote_value")
    return vote * integer(weight, "weight", 1, 1000)


def round_total(total, direction):
    if not isinstance(total, Decimal) or not total.is_finite() or total < 0:
        fail("invalid_total")
    if direction not in ("up", "down"):
        fail("invalid_rounding")
    return int(
        total.to_integral_value(
            rounding=ROUND_CEILING if direction == "up" else ROUND_FLOOR
        )
    )


def resolve_extra_vote(vote, half_mode):
    if vote == Decimal("1"):
        return "up"
    if vote == Decimal("0"):
        return "down"
    if vote != Decimal("0.5") or half_mode not in ("repeat", "round_up", "round_down"):
        fail("invalid_tie_breaker_mode")
    return {"repeat": "repeat", "round_up": "up", "round_down": "down"}[half_mode]


def validate_thresholds(max_score, rows):
    errors = []

    def add(code):
        errors.append(
            {
                "code": code,
                "field": "thresholds",
                "message": {
                    "grade_thresholds_required": "Задайте шесть порогов оценок от 0 до 5",
                    "max_score_too_low": "Максимальный балл должен быть не меньше 5",
                    "invalid_threshold_set": "Нужны оценки 0–5 и целые пороги, начиная с нуля",
                    "threshold_not_increasing": "Пороги должны строго возрастать",
                    "threshold_above_max": "Порог превышает максимальный балл",
                }[code],
                "details": {},
            }
        )

    if max_score < 5:
        add("max_score_too_low")
    if not rows:
        add("grade_thresholds_required")
        return errors
    if (
        not isinstance(rows, list)
        or len(rows) != 6
        or any(
            not isinstance(r, dict)
            or set(r) != {"grade", "minScore"}
            or type(r["grade"]) is not int
            or type(r["minScore"]) is not int
            for r in rows
        )
    ):
        add("invalid_threshold_set")
        return errors
    ordered = sorted(rows, key=lambda r: r["grade"])
    if (
        [r["grade"] for r in ordered] != list(range(6))
        or ordered[0]["minScore"] != 0
        or any(r["minScore"] < 0 for r in ordered)
    ):
        add("invalid_threshold_set")
    if any(a["minScore"] >= b["minScore"] for a, b in zip(ordered, ordered[1:])):
        add("threshold_not_increasing")
    if any(r["minScore"] > max_score for r in ordered):
        add("threshold_above_max")
    return errors


def grade_for_score(score, thresholds):
    return max(r["grade"] for r in thresholds if r["minScore"] <= score)


def build_grade_ranges(max_score, thresholds):
    if validate_thresholds(max_score, thresholds):
        return []
    rows = sorted(thresholds, key=lambda row: row["grade"])
    return [
        {
            "grade": r["grade"],
            "from": r["minScore"],
            "to": rows[i + 1]["minScore"] - 1 if i < 5 else max_score,
        }
        for i, r in enumerate(rows)
    ]
