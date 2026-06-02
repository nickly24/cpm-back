"""Попытки прохождения теста (in_progress) в MongoDB test_attempts."""
import random
from datetime import datetime

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.create_test import get_test_by_id
from cpm_back.services.exam.create_test_session import (
    get_test_session_by_student_and_test,
    insert_completed_test_session,
)
from cpm_back.services.exam.scoring import score_attempt_answers
from cpm_back.services.exam.test_sanitize import enrich_questions_with_locks, questions_in_order
from cpm_back.services.exam.test_time import (
    compute_expires_at,
    is_test_window_open,
    now_utc_iso,
    remaining_seconds,
    to_datetime,
)

STATUS_IN_PROGRESS = "in_progress"
STATUS_SUBMITTED = "submitted"
STATUS_EXPIRED = "expired"

_index_ensured = False


def _ensure_indexes():
    global _index_ensured
    if _index_ensured:
        return
    db = get_mongo_db()
    db.test_attempts.create_index(
        [("studentId", 1), ("testId", 1), ("status", 1)],
        name="student_test_status",
    )
    try:
        db.test_attempts.create_index(
            [("studentId", 1), ("testId", 1)],
            unique=True,
            partialFilterExpression={"status": STATUS_IN_PROGRESS},
            name="unique_in_progress_attempt",
        )
    except Exception as e:
        print(f"test_attempts index: {e}")
    _index_ensured = True


def normalize_student_id(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _collection():
    _ensure_indexes()
    return get_mongo_db().test_attempts


def _get_test_document(test_id):
    try:
        return get_mongo_db().tests.find_one({"_id": ObjectId(test_id)})
    except Exception:
        return None


def build_question_order(test):
    questions = test.get("questions") or []
    question_ids = [q.get("questionId") for q in questions if q.get("questionId") is not None]
    if not question_ids:
        return None, "test_has_no_questions"
    unique_ids = list(dict.fromkeys(question_ids))
    if len(unique_ids) != len(question_ids):
        return None, "duplicate_question_ids_in_test"
    order = unique_ids[:]
    random.shuffle(order)
    if len(order) != len(unique_ids) or len(set(order)) != len(order):
        return None, "invalid_question_order"
    return order, None


def _serialize_attempt(doc, test=None, include_questions=False):
    if not doc:
        return None
    attempt_id = str(doc["_id"])
    expires_at = doc.get("expiresAt")
    time_expired = doc.get("status") == STATUS_EXPIRED
    if not time_expired and expires_at:
        if remaining_seconds(expires_at) <= 0 and doc.get("status") == STATUS_IN_PROGRESS:
            time_expired = True
    answers = doc.get("answers") or []
    answered_ids = [a.get("questionId") for a in answers if a.get("questionId") is not None]
    payload = {
        "attemptId": attempt_id,
        "studentId": doc.get("studentId"),
        "testId": doc.get("testId"),
        "status": STATUS_EXPIRED if time_expired and doc.get("status") == STATUS_IN_PROGRESS else doc.get("status"),
        "isPractice": bool(doc.get("isPractice")),
        "startedAt": doc.get("startedAt"),
        "expiresAt": expires_at,
        "remainingSeconds": 0 if time_expired else remaining_seconds(expires_at),
        "timeExpired": time_expired,
        "questionOrder": doc.get("questionOrder") or [],
        "answers": answers,
        "answeredCount": len(answered_ids),
        "totalQuestions": len(doc.get("questionOrder") or []),
        "linkedSessionId": doc.get("linkedSessionId"),
    }
    if include_questions and test:
        sanitized = questions_in_order(test, payload["questionOrder"])
        payload["questions"] = enrich_questions_with_locks(sanitized, answered_ids)
    return payload


def _mark_expired_if_needed(doc):
    if not doc or doc.get("status") != STATUS_IN_PROGRESS:
        return doc
    if remaining_seconds(doc.get("expiresAt")) <= 0:
        _collection().update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": STATUS_EXPIRED, "expiredAt": now_utc_iso()}},
        )
        doc = _collection().find_one({"_id": doc["_id"]})
    return doc


def _insert_attempt_doc(coll, doc, test):
    try:
        result = coll.insert_one(doc)
    except DuplicateKeyError:
        existing = coll.find_one({
            "studentId": doc["studentId"],
            "testId": doc["testId"],
            "status": STATUS_IN_PROGRESS,
        })
        if existing:
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, test, include_questions=True),
            }
        return {"success": False, "error": "attempt_conflict"}

    doc["_id"] = result.inserted_id
    return {
        "success": True,
        "resumed": False,
        "attempt": _serialize_attempt(doc, test, include_questions=True),
    }


def _resume_pending_attempt(coll, student_id, test_id, test, is_practice):
    filters = {
        "studentId": student_id,
        "testId": str(test_id),
        "isPractice": bool(is_practice),
    }
    existing = coll.find_one({**filters, "status": STATUS_IN_PROGRESS})
    if existing:
        existing = _mark_expired_if_needed(existing)
        if existing.get("status") in (STATUS_IN_PROGRESS, STATUS_EXPIRED):
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, test, include_questions=True),
            }

    expired = coll.find_one({**filters, "status": STATUS_EXPIRED})
    if expired:
        return {
            "success": True,
            "resumed": True,
            "attempt": _serialize_attempt(expired, test, include_questions=True),
        }
    return None


def start_attempt(student_id, test_id, is_practice=False):
    student_id = normalize_student_id(student_id)
    if student_id is None:
        return {"success": False, "error": "invalid_student_id"}

    test = _get_test_document(test_id)
    if not test:
        return {"success": False, "error": "test_not_found"}

    coll = _collection()

    if is_practice:
        if not get_test_session_by_student_and_test(student_id, test_id):
            return {"success": False, "error": "test_not_completed"}

        resumed = _resume_pending_attempt(coll, student_id, test_id, test, True)
        if resumed:
            return resumed

        order, order_err = build_question_order(test)
        if order_err:
            return {"success": False, "error": order_err}

        started_at = now_utc_iso()
        time_limit = int(test.get("timeLimitMinutes") or 0)
        expires_at = compute_expires_at(started_at, time_limit)

        doc = {
            "studentId": student_id,
            "testId": str(test_id),
            "status": STATUS_IN_PROGRESS,
            "isPractice": True,
            "startedAt": started_at,
            "expiresAt": expires_at,
            "questionOrder": order,
            "answers": [],
            "createdAt": started_at,
        }
        return _insert_attempt_doc(coll, doc, test)

    open_ok, window_err = is_test_window_open(test)
    if not open_ok:
        return {"success": False, "error": window_err}

    if get_test_session_by_student_and_test(student_id, test_id):
        return {"success": False, "error": "test_already_completed"}

    existing = coll.find_one({
        "studentId": student_id,
        "testId": str(test_id),
        "status": STATUS_IN_PROGRESS,
        "isPractice": False,
    })
    if existing:
        existing = _mark_expired_if_needed(existing)
        if existing.get("status") == STATUS_IN_PROGRESS:
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, test, include_questions=True),
            }
        if existing.get("status") == STATUS_EXPIRED:
            if get_test_session_by_student_and_test(student_id, test_id):
                return {"success": False, "error": "attempt_expired"}
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, test, include_questions=True),
            }

    expired_pending = coll.find_one({
        "studentId": student_id,
        "testId": str(test_id),
        "status": STATUS_EXPIRED,
        "isPractice": False,
    })
    if expired_pending and not get_test_session_by_student_and_test(student_id, test_id):
        return {
            "success": True,
            "resumed": True,
            "attempt": _serialize_attempt(expired_pending, test, include_questions=True),
        }

    order, order_err = build_question_order(test)
    if order_err:
        return {"success": False, "error": order_err}

    started_at = now_utc_iso()
    time_limit = int(test.get("timeLimitMinutes") or 0)
    expires_at = compute_expires_at(started_at, time_limit)

    doc = {
        "studentId": student_id,
        "testId": str(test_id),
        "status": STATUS_IN_PROGRESS,
        "isPractice": False,
        "startedAt": started_at,
        "expiresAt": expires_at,
        "questionOrder": order,
        "answers": [],
        "createdAt": started_at,
    }
    return _insert_attempt_doc(coll, doc, test)


def get_attempt_for_student(attempt_id, student_id):
    student_id = normalize_student_id(student_id)
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}
    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}
    doc = _mark_expired_if_needed(doc)
    test = _get_test_document(doc.get("testId"))
    return {"success": True, "attempt": _serialize_attempt(doc, test, include_questions=True)}


def get_active_attempt(student_id, test_id):
    student_id = normalize_student_id(student_id)
    doc = _collection().find_one({
        "studentId": student_id,
        "testId": str(test_id),
        "status": STATUS_IN_PROGRESS,
        "isPractice": False,
    })
    if not doc:
        return {"success": True, "attempt": None}
    doc = _mark_expired_if_needed(doc)
    if doc.get("status") != STATUS_IN_PROGRESS:
        return {"success": True, "attempt": None}
    test = _get_test_document(test_id)
    return {"success": True, "attempt": _serialize_attempt(doc, test, include_questions=True)}


def get_active_attempt_summary(student_id, test_id):
    """Краткая информация для списка тестов."""
    result = get_active_attempt(student_id, test_id)
    if not result.get("success") or not result.get("attempt"):
        return None
    a = result["attempt"]
    return {
        "id": a["attemptId"],
        "expiresAt": a["expiresAt"],
        "remainingSeconds": a["remainingSeconds"],
        "answeredCount": a["answeredCount"],
        "totalQuestions": a["totalQuestions"],
        "expired": False,
    }


def get_expired_attempt_summary(student_id, test_id):
    """Истекшая попытка, ожидающая мягкой отправки ответов."""
    student_id = normalize_student_id(student_id)
    if get_test_session_by_student_and_test(student_id, test_id):
        return None

    doc = _collection().find_one({
        "studentId": student_id,
        "testId": str(test_id),
        "status": STATUS_EXPIRED,
        "isPractice": False,
    })
    if not doc:
        return None

    answers = doc.get("answers") or []
    return {
        "id": str(doc["_id"]),
        "expiresAt": doc.get("expiresAt"),
        "remainingSeconds": 0,
        "answeredCount": len(answers),
        "totalQuestions": len(doc.get("questionOrder") or []),
        "expired": True,
    }


def get_pending_attempt_summary(student_id, test_id):
    """Активная или истекшая (на отправку) попытка для списка."""
    expired = get_expired_attempt_summary(student_id, test_id)
    if expired:
        return expired
    return get_active_attempt_summary(student_id, test_id)


def patch_answer(attempt_id, student_id, answer_data):
    student_id = normalize_student_id(student_id)
    question_id = answer_data.get("questionId")
    if question_id is None:
        return {"success": False, "error": "question_id_required"}

    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}

    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}

    doc = _mark_expired_if_needed(doc)
    if doc.get("status") != STATUS_IN_PROGRESS:
        return {"success": False, "error": "attempt_not_active"}

    if remaining_seconds(doc.get("expiresAt")) <= 0:
        return {"success": False, "error": "time_expired"}

    order = doc.get("questionOrder") or []
    if question_id not in order:
        return {"success": False, "error": "invalid_question_id"}

    answers = doc.get("answers") or []
    for existing in answers:
        if existing.get("questionId") == question_id:
            return {"success": False, "error": "answer_locked"}

    a_type = answer_data.get("type")
    if a_type not in ("single", "multiple", "text"):
        return {"success": False, "error": "invalid_answer_type"}

    new_answer = {
        "questionId": question_id,
        "type": a_type,
    }
    if a_type == "single":
        new_answer["selectedAnswer"] = answer_data.get("selectedAnswer")
    elif a_type == "multiple":
        new_answer["selectedAnswers"] = answer_data.get("selectedAnswers") or []
    elif a_type == "text":
        new_answer["textAnswer"] = answer_data.get("textAnswer")

    answers.append(new_answer)
    _collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"answers": answers, "updatedAt": now_utc_iso()}},
    )
    updated = _collection().find_one({"_id": doc["_id"]})
    test = _get_test_document(doc.get("testId"))
    return {"success": True, "attempt": _serialize_attempt(updated, test, include_questions=True)}


def submit_attempt(attempt_id, student_id):
    student_id = normalize_student_id(student_id)
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}

    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}

    is_practice = bool(doc.get("isPractice"))
    doc = _mark_expired_if_needed(doc)
    status = doc.get("status")

    if status == STATUS_SUBMITTED:
        return {"success": False, "error": "attempt_not_active"}

    if status not in (STATUS_IN_PROGRESS, STATUS_EXPIRED):
        return {"success": False, "error": "attempt_not_active"}

    if status == STATUS_IN_PROGRESS and remaining_seconds(doc.get("expiresAt")) <= 0:
        doc = _mark_expired_if_needed(doc)
        status = doc.get("status")

    if status == STATUS_IN_PROGRESS and remaining_seconds(doc.get("expiresAt")) <= 0:
        return {"success": False, "error": "time_expired"}

    test = _get_test_document(doc.get("testId"))
    if not test:
        return {"success": False, "error": "test_not_found"}

    questions = test.get("questions") or []
    order = doc.get("questionOrder") or []
    raw_by_qid = {a.get("questionId"): a for a in (doc.get("answers") or [])}
    scored_answers, score = score_attempt_answers(questions, order, raw_by_qid)

    from datetime import datetime as dt
    started = to_datetime(doc.get("startedAt"))
    completed = dt.utcnow()
    time_spent = 1
    if started:
        time_spent = max(1, int((completed - started).total_seconds() // 60) or 1)

    correct_answers = sum(1 for answer in scored_answers if answer.get("isCorrect"))
    total_questions = len(order)
    accuracy = round((correct_answers / total_questions) * 100) if total_questions else 0
    stats = {
        "correctAnswers": correct_answers,
        "totalQuestions": total_questions,
        "accuracy": accuracy,
        "totalPoints": sum(int(a.get("points", 0)) for a in scored_answers),
    }

    if is_practice:
        _collection().update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": STATUS_SUBMITTED,
                    "submittedAt": now_utc_iso(),
                    "answers": scored_answers,
                    "practiceScore": score,
                }
            },
        )
        return {
            "success": True,
            "isPractice": True,
            "score": score,
            "answers": scored_answers,
            "timeSpentMinutes": time_spent,
            "stats": stats,
        }

    open_ok, window_err = is_test_window_open(test)
    if not open_ok:
        return {"success": False, "error": window_err}

    if get_test_session_by_student_and_test(student_id, doc.get("testId")):
        return {"success": False, "error": "test_already_completed"}

    session_result = insert_completed_test_session(
        student_id=student_id,
        test_id=doc.get("testId"),
        test_title=test.get("title") or doc.get("testTitle") or "",
        answers=scored_answers,
        score=score,
        time_spent_minutes=time_spent,
        question_order=order,
    )
    if not session_result.get("success"):
        return session_result

    _collection().update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": STATUS_SUBMITTED,
                "submittedAt": now_utc_iso(),
                "linkedSessionId": session_result.get("sessionId"),
            }
        },
    )
    return {
        "success": True,
        "sessionId": session_result.get("sessionId"),
        "score": score,
        "answers": scored_answers,
        "timeSpentMinutes": time_spent,
        "stats": stats,
    }
