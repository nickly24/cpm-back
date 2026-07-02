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
    from cpm_back.services.exam.test_definition_cache import get_test_document_cached

    return get_test_document_cached(test_id)


def _answer_payload_from_data(answer_data):
    question_id = answer_data.get("questionId")
    a_type = answer_data.get("type")
    if a_type not in ("single", "multiple", "text"):
        return None, "invalid_answer_type"
    payload = {
        "questionId": question_id,
        "type": a_type,
    }
    if a_type == "single":
        payload["selectedAnswer"] = answer_data.get("selectedAnswer")
    elif a_type == "multiple":
        payload["selectedAnswers"] = answer_data.get("selectedAnswers") or []
    elif a_type == "text":
        payload["textAnswer"] = answer_data.get("textAnswer")
    return payload, None


def _answers_equal(existing, new_answer):
    if not existing or not new_answer:
        return False
    if existing.get("questionId") != new_answer.get("questionId"):
        return False
    if existing.get("type") != new_answer.get("type"):
        return False
    a_type = new_answer.get("type")
    if a_type == "single":
        return existing.get("selectedAnswer") == new_answer.get("selectedAnswer")
    if a_type == "multiple":
        return sorted(existing.get("selectedAnswers") or []) == sorted(
            new_answer.get("selectedAnswers") or []
        )
    if a_type == "text":
        return (existing.get("textAnswer") or "").strip() == (
            new_answer.get("textAnswer") or ""
        ).strip()
    return False


def _attempt_accepts_answers(doc):
    """Можно ли дописать ответы (активная сдача или догон после expire)."""
    doc = _mark_expired_if_needed(doc)
    status = doc.get("status")
    if status == STATUS_IN_PROGRESS:
        if remaining_seconds(doc.get("expiresAt")) <= 0:
            doc = _mark_expired_if_needed(doc)
            status = doc.get("status")
    if status == STATUS_EXPIRED:
        return doc, None
    if status == STATUS_IN_PROGRESS and remaining_seconds(doc.get("expiresAt")) > 0:
        return doc, None
    if status == STATUS_IN_PROGRESS:
        return doc, "time_expired"
    return doc, "attempt_not_active"


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


def _apply_answer_to_doc(doc, answer_data):
    """Один ответ в документ попытки. Возвращает (doc, error, changed, idempotent)."""
    question_id = answer_data.get("questionId")
    if question_id is None:
        return doc, "question_id_required", False, False

    order = doc.get("questionOrder") or []
    if question_id not in order:
        return doc, "invalid_question_id", False, False

    new_answer, err = _answer_payload_from_data(answer_data)
    if err:
        return doc, err, False, False

    answers = list(doc.get("answers") or [])
    for existing in answers:
        if existing.get("questionId") == question_id:
            if _answers_equal(existing, new_answer):
                return doc, None, False, True
            return doc, "answer_locked", False, False

    answers.append(new_answer)
    doc = dict(doc)
    doc["answers"] = answers
    return doc, None, True, False


def _persist_attempt_answers(doc, changed):
    if not changed:
        return doc
    _collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"answers": doc.get("answers") or [], "updatedAt": now_utc_iso()}},
    )
    return _collection().find_one({"_id": doc["_id"]})


def patch_answer(attempt_id, student_id, answer_data, include_questions=False):
    student_id = normalize_student_id(student_id)
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}

    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}

    doc, gate_err = _attempt_accepts_answers(doc)
    if gate_err:
        return {"success": False, "error": gate_err}

    doc, err, changed, idempotent = _apply_answer_to_doc(doc, answer_data)
    if err:
        return {"success": False, "error": err}

    if changed:
        doc = _persist_attempt_answers(doc, True)
    elif not idempotent:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})

    test = _get_test_document(doc.get("testId")) if include_questions else None
    payload = {
        "success": True,
        "attempt": _serialize_attempt(doc, test, include_questions=include_questions),
    }
    if idempotent:
        payload["idempotent"] = True
    return payload


def patch_answers_batch(attempt_id, student_id, answers_list):
    """Пакетная синхронизация ответов (офлайн-очередь). Лёгкий attempt в ответе."""
    student_id = normalize_student_id(student_id)
    if not isinstance(answers_list, list) or not answers_list:
        return {"success": False, "error": "answers_required"}

    if len(answers_list) > 500:
        return {"success": False, "error": "answers_batch_too_large"}

    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}

    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}

    doc, gate_err = _attempt_accepts_answers(doc)
    if gate_err:
        return {"success": False, "error": gate_err}

    synced = []
    skipped = []
    errors = []
    any_changed = False
    seen_question_ids = set()

    for item in answers_list:
        qid = item.get("questionId")
        if qid in seen_question_ids:
            skipped.append(qid)
            continue
        seen_question_ids.add(qid)

        doc, err, changed, idempotent = _apply_answer_to_doc(doc, item)
        if err:
            errors.append({"questionId": qid, "error": err})
            continue
        if changed:
            any_changed = True
            synced.append(qid)
        elif idempotent:
            skipped.append(qid)

    if any_changed:
        doc = _persist_attempt_answers(doc, True)
    else:
        doc = _collection().find_one({"_id": doc["_id"]})

    return {
        "success": len(errors) == 0,
        "attempt": _serialize_attempt(doc, None, include_questions=False),
        "syncedQuestionIds": synced,
        "skippedQuestionIds": skipped,
        "errors": errors,
    }


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
    raw_answers = doc.get("answers") or []
    if not is_practice and order and not raw_answers:
        return {"success": False, "error": "empty_attempt_answers"}

    raw_by_qid = {a.get("questionId"): a for a in raw_answers}
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


def force_submit_attempt_admin(attempt_id):
    """Админское принудительное завершение попытки по текущим серверным ответам."""
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}

    if not doc:
        return {"success": False, "error": "attempt_not_found"}

    status = doc.get("status")
    if status == STATUS_SUBMITTED:
        return {"success": False, "error": "attempt_not_active"}

    if status not in (STATUS_IN_PROGRESS, STATUS_EXPIRED):
        return {"success": False, "error": "attempt_not_active"}

    test = _get_test_document(doc.get("testId"))
    if not test:
        return {"success": False, "error": "test_not_found"}

    student_id = normalize_student_id(doc.get("studentId"))
    if get_test_session_by_student_and_test(student_id, doc.get("testId")):
        return {"success": False, "error": "test_already_completed"}

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

    if bool(doc.get("isPractice")):
        _collection().update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": STATUS_SUBMITTED,
                    "submittedAt": now_utc_iso(),
                    "answers": scored_answers,
                    "practiceScore": score,
                    "adminForcedSubmit": True,
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
                "adminForcedSubmit": True,
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


def _parse_status_filter(status_filter):
    if not status_filter or status_filter in ("active", "pending"):
        return [STATUS_IN_PROGRESS, STATUS_EXPIRED]
    if status_filter == "all":
        return [STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_SUBMITTED]
    parts = [p.strip() for p in str(status_filter).split(",") if p.strip()]
    allowed = {STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_SUBMITTED}
    selected = [p for p in parts if p in allowed]
    return selected or [STATUS_IN_PROGRESS, STATUS_EXPIRED]


def _attempts_match_query(test_id, status_filter=None, student_ids=None):
    from cpm_back.services.exam.student_names import build_student_id_mongo_filter

    statuses = _parse_status_filter(status_filter)
    query = {
        "testId": str(test_id),
        "status": {"$in": statuses},
        "isPractice": False,
    }
    student_filter = build_student_id_mongo_filter(student_ids)
    if student_filter:
        query = {"$and": [query, student_filter]}
    return query


def count_attempts_by_test(test_id, status_filter=None, student_ids=None):
    return _collection().count_documents(
        _attempts_match_query(test_id, status_filter, student_ids),
    )


def count_attempts_by_test_and_status(test_id, statuses):
    coll = _collection()
    base = {"testId": str(test_id), "isPractice": False, "status": {"$in": statuses}}
    return coll.count_documents(base)


def list_attempts_by_test(test_id, status_filter=None, student_ids=None):
    """Без пагинации — для обратной совместимости (не использовать в админ UI)."""
    coll = _collection()
    query = _attempts_match_query(test_id, status_filter, student_ids)
    cursor = coll.find(query).sort("startedAt", -1)
    items = []
    for doc in cursor:
        items.append(_serialize_attempt_list_item(doc))
    return items


def list_attempts_by_test_paginated(
    test_id, skip=0, limit=10, status_filter=None, student_ids=None,
):
    coll = _collection()
    query = _attempts_match_query(test_id, status_filter, student_ids)
    cursor = coll.aggregate([
        {"$match": query},
        {"$sort": {"startedAt": -1}},
        {"$skip": int(skip)},
        {"$limit": int(limit)},
        {
            "$project": {
                "_id": 1,
                "studentId": 1,
                "testId": 1,
                "status": 1,
                "isPractice": 1,
                "startedAt": 1,
                "expiresAt": 1,
                "linkedSessionId": 1,
                "submittedAt": 1,
                "answeredCount": {"$size": {"$ifNull": ["$answers", []]}},
                "totalQuestions": {"$size": {"$ifNull": ["$questionOrder", []]}},
            }
        },
    ])
    items = []
    for row in cursor:
        doc = {
            "_id": row["_id"],
            "studentId": row.get("studentId"),
            "testId": row.get("testId"),
            "status": row.get("status"),
            "isPractice": row.get("isPractice"),
            "startedAt": row.get("startedAt"),
            "expiresAt": row.get("expiresAt"),
            "linkedSessionId": row.get("linkedSessionId"),
            "submittedAt": row.get("submittedAt"),
            "answers": [{}] * row.get("answeredCount", 0),
            "questionOrder": [None] * row.get("totalQuestions", 0),
        }
        items.append(_serialize_attempt_list_item(doc))
    return items


def _serialize_attempt_list_item(doc):
    if not doc:
        return None
    expires_at = doc.get("expiresAt")
    time_expired = doc.get("status") == STATUS_EXPIRED
    if not time_expired and expires_at and doc.get("status") == STATUS_IN_PROGRESS:
        if remaining_seconds(expires_at) <= 0:
            time_expired = True
    answers = doc.get("answers") or []
    order = doc.get("questionOrder") or []
    status = doc.get("status")
    if time_expired and status == STATUS_IN_PROGRESS:
        status = STATUS_EXPIRED
    return {
        "attemptId": str(doc["_id"]),
        "studentId": doc.get("studentId"),
        "testId": doc.get("testId"),
        "status": status,
        "isPractice": bool(doc.get("isPractice")),
        "startedAt": doc.get("startedAt"),
        "expiresAt": expires_at,
        "remainingSeconds": 0 if time_expired else remaining_seconds(expires_at),
        "timeExpired": time_expired,
        "answeredCount": len(answers),
        "totalQuestions": len(order),
        "linkedSessionId": doc.get("linkedSessionId"),
        "submittedAt": doc.get("submittedAt"),
    }


def _admin_question_payload(question):
    q_type = question.get("type")
    payload = {
        "questionId": question.get("questionId"),
        "type": q_type,
        "text": question.get("text"),
        "points": question.get("points"),
    }
    if q_type in ("single", "multiple"):
        payload["answers"] = question.get("answers") or []
    if q_type == "text":
        payload["correctAnswers"] = question.get("correctAnswers") or []
    return payload


def _admin_answer_view(raw_answer, question):
    if not raw_answer:
        return None
    view = {
        "questionId": raw_answer.get("questionId"),
        "type": raw_answer.get("type"),
    }
    q_type = raw_answer.get("type")
    if q_type == "single":
        view["selectedAnswer"] = raw_answer.get("selectedAnswer")
    elif q_type == "multiple":
        view["selectedAnswers"] = raw_answer.get("selectedAnswers") or []
    elif q_type == "text":
        view["textAnswer"] = raw_answer.get("textAnswer")
    if question:
        view["questionText"] = question.get("text")
    return view


def get_attempt_admin_detail(attempt_id, brief=False):
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}
    if not doc:
        return {"success": False, "error": "attempt_not_found"}

    doc = _mark_expired_if_needed(doc)
    if brief:
        return {
            "success": True,
            "attempt": _serialize_attempt_list_item(doc),
        }

    test = _get_test_document(doc.get("testId"))
    from cpm_back.services.exam.student_names import (
        get_student_names_by_ids,
        resolve_student_name,
    )

    names = get_student_names_by_ids([doc.get("studentId")])
    sid = doc.get("studentId")
    order = doc.get("questionOrder") or []
    by_qid = {q.get("questionId"): q for q in (test.get("questions") or []) if test}
    raw_by_qid = {a.get("questionId"): a for a in (doc.get("answers") or [])}

    items = []
    for qid in order:
        question = by_qid.get(qid)
        items.append({
            "questionId": qid,
            "question": _admin_question_payload(question) if question else None,
            "studentAnswer": _admin_answer_view(raw_by_qid.get(qid), question),
            "answered": qid in raw_by_qid,
        })

    return {
        "success": True,
        "attempt": _serialize_attempt(doc, test, include_questions=False),
        "studentFullName": resolve_student_name(sid, names) or f"Студент #{sid}",
        "testTitle": (test or {}).get("title"),
        "items": items,
    }


def delete_attempt_by_id(attempt_id):
    try:
        result = _collection().delete_one({"_id": ObjectId(attempt_id)})
        return result.deleted_count > 0
    except Exception:
        return False
