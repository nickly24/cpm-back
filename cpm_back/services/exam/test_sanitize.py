"""Вопросы теста без ключей для студента при прохождении."""


def sanitize_question(question):
    q_type = question.get("type")
    sanitized = {
        "questionId": question.get("questionId"),
        "type": q_type,
        "text": question.get("text"),
        "points": question.get("points"),
    }
    if q_type in ("single", "multiple"):
        sanitized["answers"] = [
            {"id": a.get("id"), "text": a.get("text")}
            for a in (question.get("answers") or [])
        ]
    return sanitized


def questions_in_order(test, question_order):
    test_id = test.get("_id") or test.get("id")
    if test_id is not None:
        from cpm_back.services.exam.exam_memory_cache import get_sanitized_questions_map_cached

        qmap = get_sanitized_questions_map_cached(str(test_id))
        return [dict(qmap[qid]) for qid in question_order if qid in qmap]

    by_id = {q.get("questionId"): q for q in test.get("questions", [])}
    return [sanitize_question(by_id[qid]) for qid in question_order if qid in by_id]


def enrich_questions_with_locks(sanitized_questions, answered_question_ids):
    answered = set(answered_question_ids)
    result = []
    for q in sanitized_questions:
        item = dict(q)
        item["locked"] = q.get("questionId") in answered
        result.append(item)
    return result
