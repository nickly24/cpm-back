"""Расчёт баллов по вопросам теста (единая логика для submit и recalc)."""


def normalize_text(value):
    return "" if value is None else str(value).strip().lower()


def score_single(selected_answer_id, question):
    correct_ids = {a.get("id") for a in question.get("answers", []) if a.get("isCorrect")}
    is_correct = selected_answer_id in correct_ids
    points = int(question.get("points", 0)) if is_correct else 0
    return points, is_correct


def score_multiple(selected_answer_ids, question):
    total_available = int(question.get("points", 0))
    selected_set = set(selected_answer_ids or [])
    all_correct_ids = {a.get("id") for a in question.get("answers", []) if a.get("isCorrect")}
    all_known_ids = {a.get("id") for a in question.get("answers", [])}
    if selected_set == all_correct_ids and selected_set.issubset(all_known_ids):
        return total_available, True
    return 0, False


def score_text(text_answer, question):
    normalized = normalize_text(text_answer)
    correct_list = [normalize_text(val) for val in (question.get("correctAnswers") or [])]
    is_correct = normalized in correct_list if correct_list else False
    points = int(question.get("points", 0)) if is_correct else 0
    return points, is_correct


def score_answer_from_raw(raw_answer, question):
    """Сырой ответ студента (без points/isCorrect) → scored answer dict."""
    a_type = raw_answer.get("type") or question.get("type")
    updated = {
        "questionId": raw_answer.get("questionId"),
        "type": a_type,
    }
    if a_type == "single":
        updated["selectedAnswer"] = raw_answer.get("selectedAnswer")
        pts, ok = score_single(raw_answer.get("selectedAnswer"), question)
    elif a_type == "multiple":
        updated["selectedAnswers"] = raw_answer.get("selectedAnswers") or []
        pts, ok = score_multiple(updated["selectedAnswers"], question)
    elif a_type == "text":
        updated["textAnswer"] = raw_answer.get("textAnswer")
        pts, ok = score_text(raw_answer.get("textAnswer"), question)
    else:
        pts, ok = 0, False
    updated["points"] = int(pts)
    updated["isCorrect"] = bool(ok)
    return updated


def recompute_answer(existing_answer, question):
    return score_answer_from_raw(existing_answer, question)


def placeholder_answer_for_new_question(question):
    a_type = question.get("type")
    base = {"questionId": question.get("questionId"), "type": a_type, "points": 0, "isCorrect": False}
    if a_type == "single":
        base["selectedAnswer"] = None
    elif a_type == "multiple":
        base["selectedAnswers"] = []
    elif a_type == "text":
        base["textAnswer"] = ""
    return base


def score_attempt_answers(questions, question_order, raw_answers_by_qid):
    """
    Считает scored answers в порядке question_order и итоговый score (0–100).
    raw_answers_by_qid: dict questionId -> raw answer dict
    """
    question_by_id = {q.get("questionId"): q for q in questions}
    scored = []
    for qid in question_order:
        question = question_by_id.get(qid)
        if not question:
            continue
        raw = raw_answers_by_qid.get(qid)
        if raw:
            scored.append(score_answer_from_raw({**raw, "questionId": qid}, question))
        else:
            scored.append(placeholder_answer_for_new_question(question))
    earned = sum(int(a.get("points", 0)) for a in scored)
    max_points = sum(int(q.get("points", 0)) for q in questions)
    score = round((earned / max_points) * 100, 2) if max_points > 0 else 0
    return scored, int(score)


def rebuild_scoped_session_answers(session_answers, questions, exclude_question_ids=None):
    """
    Пересчёт ответов старой session по текущему определению теста (ТЗ):
    - удалённые / exclude (type-change) вопросы выбрасываются;
    - оставшиеся пересчитываются;
    - новые вопросы теста НЕ добавляются (не раздувают max);
    - score = int(round(earned / max_of_remaining * 100, 2)).
    """
    exclude = set(exclude_question_ids or ())
    question_by_id = {q.get("questionId"): q for q in (questions or [])}
    new_answers = []
    for answer in session_answers or []:
        if not isinstance(answer, dict):
            continue
        qid = answer.get("questionId")
        if qid is None or qid in exclude:
            continue
        question = question_by_id.get(qid)
        if not question:
            continue
        new_answers.append(recompute_answer(answer, question))
    earned = sum(int(a.get("points", 0)) for a in new_answers)
    max_points = sum(
        int(question_by_id[a.get("questionId")].get("points", 0))
        for a in new_answers
        if a.get("questionId") in question_by_id
    )
    score = round((earned / max_points) * 100, 2) if max_points > 0 else 0
    return new_answers, int(score)
