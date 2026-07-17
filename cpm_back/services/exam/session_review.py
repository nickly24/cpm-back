"""Просмотр завершённой сдачи: порядок вопросов и ответы студента."""
from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.create_test import get_test_by_id
from cpm_back.services.exam.create_test_session import get_test_session_by_id
from cpm_back.services.exam.test_sanitize import sanitize_question
from cpm_back.services.exam.visibility import can_show_correct_answers
from cpm_back.services.exam.test_versions import get_test_version


def _correct_answer_payload(question, show_correct):
    if not show_correct:
        return None
    q_type = question.get("type")
    if q_type == "text":
        return {"correctAnswers": question.get("correctAnswers") or []}
    if q_type in ("single", "multiple"):
        return {
            "correctOptionIds": [a.get("id") for a in question.get("answers", []) if a.get("isCorrect")],
            "answers": question.get("answers"),
        }
    return None


def build_session_review(session_id, role):
    session = get_test_session_by_id(session_id)
    if not session:
        return {"success": False, "error": "session_not_found"}

    test_id = session.get("testId")
    test = get_test_version(session.get("testVersionId")) if session.get("testVersionId") else None
    if not test:
        test = get_test_by_id(test_id) if test_id else None
    if not test:
        return {"success": False, "error": "test_not_found"}

    show_correct = can_show_correct_answers(role, test_id)
    question_order = session.get("questionOrder")
    if not question_order:
        question_order = [q.get("questionId") for q in test.get("questions", [])]

    by_id = {q.get("questionId"): q for q in test.get("questions", [])}
    answer_by_qid = {a.get("questionId"): a for a in session.get("answers", [])}

    items = []
    for qid in question_order:
        question = by_id.get(qid)
        if not question:
            continue
        student_answer = answer_by_qid.get(qid)
        item = {
            "questionId": qid,
            "question": sanitize_question(question),
            "studentAnswer": _student_answer_view(student_answer),
            "points": student_answer.get("points") if student_answer else 0,
            "isCorrect": student_answer.get("isCorrect") if student_answer else False,
        }
        correct = _correct_answer_payload(question, show_correct)
        if correct:
            item["correct"] = correct
        items.append(item)

    return {
        "success": True,
        "review": {
            "sessionId": str(session.get("_id") or session.get("id")),
            "testId": test_id,
            "testTitle": session.get("testTitle"),
            "score": session.get("score"),
            "completedAt": session.get("completedAt"),
            "timeSpentMinutes": session.get("timeSpentMinutes"),
            "questionOrder": question_order,
            "visible": bool(test.get("visible", False)),
            "showCorrectAnswers": show_correct,
            "items": items,
        },
    }


def _student_answer_view(answer):
    if not answer:
        return None
    view = {"type": answer.get("type"), "questionId": answer.get("questionId")}
    if answer.get("type") == "single":
        view["selectedAnswer"] = answer.get("selectedAnswer")
    elif answer.get("type") == "multiple":
        view["selectedAnswers"] = answer.get("selectedAnswers")
    elif answer.get("type") == "text":
        view["textAnswer"] = answer.get("textAnswer")
    return view
