"""Pure question selection. Cycles advance only in the selected part."""

import random
from .common import fail


def select_question(
    questions,
    usage,
    progress,
    purpose,
    last_question_id=None,
    source_mode=None,
    source_part_id=None,
    exclude=None,
    rng=None,
):
    rng = rng or random.SystemRandom()
    candidates = []
    for part in progress:
        part_id = part["source_part_id"]
        if purpose == "regular" and part["consensus_count"] >= part["required_count"]:
            continue
        if (
            purpose == "tie_breaker"
            and source_mode == "specific_part"
            and part_id != source_part_id
        ):
            continue
        pool = [
            q
            for q in questions
            if q["source_part_id"] == part_id
            and q["id"] != exclude
            and not usage.get(q["id"], {}).get("permanently_excluded", False)
        ]
        cycle = part["cycle_no"]
        if purpose == "regular":
            available = [
                q
                for q in pool
                if not usage.get(q["id"], {}).get("ever_presented", False)
            ]
        else:
            available = [
                q
                for q in pool
                if usage.get(q["id"], {}).get("last_cycle_no", 0) < cycle
            ]
            if not available:
                cycle += 1
                available = pool
            available = [q for q in available if q["id"] != last_question_id]
        if available:
            weight = (
                part["required_count"] - part["consensus_count"]
                if purpose == "regular"
                else 1
            )
            candidates.append((part_id, cycle, available, weight))
    if not candidates:
        fail(
            "question_pool_exhausted",
            "Нет доступного вопроса. Обратитесь к администратору.",
            422,
        )
    ticket = rng.randrange(sum(item[3] for item in candidates))
    for part_id, cycle, available, weight in candidates:
        if ticket < weight:
            return rng.choice(available), part_id, cycle
        ticket -= weight
    raise AssertionError("Invalid random selection")
