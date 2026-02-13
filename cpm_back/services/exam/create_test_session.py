from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from cpm_back.db.mongo import get_mongo_db

_index_ensured = False


def _ensure_unique_index():
    global _index_ensured
    if _index_ensured:
        return
    try:
        db = get_mongo_db()
        db.test_sessions.create_index(
            [("studentId", 1), ("testId", 1)],
            unique=True,
            name="unique_student_test"
        )
        _index_ensured = True
    except Exception as e:
        print(f"Ошибка при создании индекса: {e}")


def create_test_session(student_id, test_id, test_title, answers, score=None, time_spent_minutes=None):
    _ensure_unique_index()
    db = get_mongo_db()
    test_sessions_collection = db.test_sessions
    existing_session = test_sessions_collection.find_one({"studentId": student_id, "testId": test_id})
    if existing_session:
        return {
            "success": False,
            "error": "Тест уже сдан",
            "message": "Для данного студента и теста уже существует завершенная сессия",
            "existingSessionId": str(existing_session["_id"]),
            "existingScore": existing_session.get("score"),
            "completedAt": existing_session.get("completedAt")
        }
    if score is None:
        score = sum(int(answer.get("points", 0)) for answer in answers)
    test_session = {
        "studentId": student_id,
        "testId": test_id,
        "testTitle": test_title,
        "answers": answers,
        "score": score,
        "timeSpentMinutes": time_spent_minutes,
        "completedAt": datetime.utcnow().isoformat() + "Z",
        "createdAt": datetime.utcnow().isoformat() + "Z"
    }
    try:
        result = test_sessions_collection.insert_one(test_session)
        return {"success": True, "sessionId": str(result.inserted_id), "message": "Тест-сессия успешно создана"}
    except DuplicateKeyError:
        existing_session = test_sessions_collection.find_one({"studentId": student_id, "testId": test_id})
        return {
            "success": False,
            "error": "Тест уже сдан",
            "message": "Обнаружено дублирование на уровне базы данных",
            "existingSessionId": str(existing_session["_id"]) if existing_session else None,
            "existingScore": existing_session.get("score") if existing_session else None,
            "completedAt": existing_session.get("completedAt") if existing_session else None
        }


def get_test_session_by_id(session_id):
    db = get_mongo_db()
    session = db.test_sessions.find_one({"_id": ObjectId(session_id)})
    if session:
        session["_id"] = str(session["_id"])
        return session
    return None


def _normalize_student_id(value):
    """В MongoDB studentId может быть int (из JSON); из URL приходит строка — приводим к int при возможности."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def get_test_sessions_by_student(student_id):
    sid = _normalize_student_id(student_id)
    db = get_mongo_db()
    # Ищем и по int, и по строке — в БД studentId может быть и так и так
    if sid is not None:
        q = {"studentId": {"$in": [sid, str(sid)]}}
    else:
        q = {"studentId": {"$in": [student_id, str(student_id)] if student_id is not None else []}}
    sessions = db.test_sessions.find(
        q,
        {"_id": 1, "testId": 1, "testTitle": 1, "score": 1, "completedAt": 1, "timeSpentMinutes": 1}
    ).sort("completedAt", -1)
    return [{
        "id": str(s["_id"]),
        "testId": str(s["testId"]) if s.get("testId") is not None else None,
        "testTitle": s.get("testTitle"),
        "score": s.get("score"),
        "completedAt": s.get("completedAt"),
        "timeSpentMinutes": s.get("timeSpentMinutes")
    } for s in sessions]


def get_test_sessions_by_test(test_id):
    db = get_mongo_db()
    sessions = db.test_sessions.find(
        {"testId": test_id},
        {"_id": 1, "studentId": 1, "testTitle": 1, "score": 1, "completedAt": 1, "timeSpentMinutes": 1}
    ).sort("completedAt", -1)
    return [{
        "id": str(s["_id"]),
        "studentId": s["studentId"],
        "testTitle": s["testTitle"],
        "score": s.get("score"),
        "completedAt": s["completedAt"],
        "timeSpentMinutes": s.get("timeSpentMinutes")
    } for s in sessions]


def get_test_session_stats(session_id):
    db = get_mongo_db()
    session = db.test_sessions.find_one({"_id": ObjectId(session_id)})
    if not session:
        return None
    total_questions = len(session["answers"])
    correct_answers = sum(1 for a in session["answers"] if a.get("isCorrect", False))
    total_points = sum(int(a.get("points", 0)) for a in session["answers"])
    question_types = {}
    for answer in session["answers"]:
        q_type = answer.get("type", "unknown")
        if q_type not in question_types:
            question_types[q_type] = {"count": 0, "correct": 0, "points": 0}
        question_types[q_type]["count"] += 1
        if answer.get("isCorrect", False):
            question_types[q_type]["correct"] += 1
        question_types[q_type]["points"] += int(answer.get("points", 0))
    return {
        "sessionId": str(session["_id"]),
        "studentId": session["studentId"],
        "testTitle": session["testTitle"],
        "totalQuestions": total_questions,
        "correctAnswers": correct_answers,
        "accuracy": round((correct_answers / total_questions) * 100, 2) if total_questions > 0 else 0,
        "totalPoints": total_points,
        "timeSpentMinutes": session.get("timeSpentMinutes"),
        "completedAt": session["completedAt"],
        "questionTypes": question_types,
        "answers": session["answers"]
    }


def get_test_session_by_student_and_test(student_id, test_id):
    db = get_mongo_db()
    sid = _normalize_student_id(student_id)
    try:
        test_id_oid = ObjectId(test_id) if test_id else None
    except (TypeError, ValueError):
        test_id_oid = None
    # Пробуем варианты как в MongoDB: studentId 906 (int), testId "698ed194..." (string)
    candidates = []
    if sid is not None:
        candidates.append((sid, test_id))
        if test_id_oid is not None:
            candidates.append((sid, test_id_oid))
        candidates.append((str(sid), test_id))
        if test_id_oid is not None:
            candidates.append((str(sid), test_id_oid))
    if student_id is not None and sid is None:
        candidates.append((student_id, test_id))
        if test_id_oid is not None:
            candidates.append((student_id, test_id_oid))
    for s_id, t_id in candidates:
        session = db.test_sessions.find_one({"studentId": s_id, "testId": t_id})
        if session:
            session["_id"] = str(session["_id"])
            session["id"] = session["_id"]
            if session.get("testId") is not None:
                session["testId"] = str(session["testId"])
            return session
    return None


def _normalize_text(value):
    return "" if value is None else str(value).strip().lower()


def _score_single(selected_answer_id, question):
    correct_ids = {a.get("id") for a in question.get("answers", []) if a.get("isCorrect")}
    is_correct = selected_answer_id in correct_ids
    points = int(question.get("points", 0)) if is_correct else 0
    return points, is_correct


def _score_multiple(selected_answer_ids, question):
    total_available = int(question.get("points", 0))
    selected_set = set(selected_answer_ids or [])
    all_correct_ids = {a.get("id") for a in question.get("answers", []) if a.get("isCorrect")}
    all_incorrect_ids = {a.get("id") for a in question.get("answers", []) if not a.get("isCorrect")}
    if selected_set.issuperset(all_correct_ids) and selected_set.isdisjoint(all_incorrect_ids):
        return total_available, True
    return 0, False


def _score_text(text_answer, question):
    normalized = _normalize_text(text_answer)
    correct_list = [_normalize_text(val) for val in (question.get("correctAnswers") or [])]
    is_correct = normalized in correct_list if correct_list else False
    points = int(question.get("points", 0)) if is_correct else 0
    return points, is_correct


def _recompute_answer(existing_answer, question):
    a_type = existing_answer.get("type") or question.get("type")
    updated = dict(existing_answer)
    if a_type == "single":
        pts, ok = _score_single(existing_answer.get("selectedAnswer"), question)
    elif a_type == "multiple":
        pts, ok = _score_multiple(existing_answer.get("selectedAnswers", []), question)
    elif a_type == "text":
        pts, ok = _score_text(existing_answer.get("textAnswer"), question)
    else:
        pts, ok = 0, False
    updated["type"] = a_type
    updated["points"] = int(pts)
    updated["isCorrect"] = bool(ok)
    return updated


def _placeholder_answer_for_new_question(question):
    a_type = question.get("type")
    base = {"questionId": question.get("questionId"), "type": a_type, "points": 0, "isCorrect": False}
    if a_type == "single":
        base["selectedAnswer"] = None
    elif a_type == "multiple":
        base["selectedAnswers"] = []
    elif a_type == "text":
        base["textAnswer"] = ""
    return base


def recalc_test_sessions(test_id):
    db = get_mongo_db()
    tests_collection = db.tests
    sessions_collection = db.test_sessions
    test = tests_collection.find_one({"_id": ObjectId(test_id)})
    if not test:
        return {"updated": 0, "sessions": 0, "error": "Test not found"}
    questions = test.get("questions", [])
    question_by_id = {q.get("questionId"): q for q in questions}
    current_title = test.get("title")
    updated_count = 0
    total_sessions = 0
    for session in sessions_collection.find({"testId": test_id}):
        total_sessions += 1
        answers = session.get("answers", [])
        answer_by_qid = {a.get("questionId"): a for a in answers if "questionId" in a}
        new_answers = []
        for a in answers:
            qid = a.get("questionId")
            q_spec = question_by_id.get(qid)
            if q_spec:
                new_answers.append(_recompute_answer(a, q_spec))
        for q in questions:
            qid = q.get("questionId")
            if qid not in answer_by_qid:
                new_answers.append(_placeholder_answer_for_new_question(q))
        earned_points = sum(int(a.get("points", 0)) for a in new_answers)
        max_points = sum(int(q.get("points", 0)) for q in questions)
        new_score = round((earned_points / max_points) * 100, 2) if max_points > 0 else 0
        update_doc = {"answers": new_answers, "score": int(new_score), "testTitle": current_title or session.get("testTitle")}
        if sessions_collection.update_one({"_id": session["_id"]}, {"$set": update_doc}).modified_count:
            updated_count += 1
    return {"updated": updated_count, "sessions": total_sessions}
