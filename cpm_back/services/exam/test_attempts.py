"""Попытки прохождения теста (in_progress) в MongoDB test_attempts."""
import hashlib
import json
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
from cpm_back.services.exam.scoring import score_answer_from_raw, score_attempt_answers
from cpm_back.services.exam.student_test_access import resolve_student_test_access
from cpm_back.services.exam.test_sanitize import enrich_questions_with_locks, questions_in_order
from cpm_back.services.exam.test_time import (
    MOSCOW_TZ,
    build_attempt_time_fields,
    compute_expires_at,
    is_test_window_open,
    now_utc_iso,
    remaining_seconds,
    to_datetime,
)
from cpm_back.services.exam.test_versions import ensure_test_version, get_test_version

STATUS_IN_PROGRESS = "in_progress"
STATUS_SUBMITTED = "submitted"
STATUS_EXPIRED = "expired"
STATUS_EXPIRED_PENDING_UPLOAD = "expired_pending_upload"
STATUS_FINALIZING = "finalizing"

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
    db.test_attempts.create_index([("studentId", 1), ("status", 1), ("isPractice", 1), ("testId", 1)], name="student_attempt_catalog")
    db.test_attempts.create_index([("testId", 1), ("status", 1)], name="test_attempt_status")
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
    if doc.get("isPractice"):
        if doc.get("status") == STATUS_IN_PROGRESS:
            return doc, None
        return doc, "attempt_not_active"
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


def _definition_for_attempt(doc):
    version = get_test_version(doc.get("testVersionId")) if doc.get("testVersionId") else None
    if version:
        version = dict(version)
        version.pop("_id", None)
        version["_immutableVersionId"] = str(doc.get("testVersionId"))
        return version
    return _get_test_document(doc.get("testId"))


def _practice_access_error(doc, student_id):
    current_test = get_test_by_id(doc.get("testId"))
    if not current_test:
        return "test_not_found"
    completed_session = get_test_session_by_student_and_test(
        student_id, doc.get("testId")
    )
    access = resolve_student_test_access(
        current_test,
        has_completed_session=bool(completed_session),
        has_open_official_attempt=_has_open_official_attempt(
            student_id, doc.get("testId")
        ),
    )
    return None if access.can_practice else access.practice_error


def _has_open_official_attempt(student_id, test_id):
    return bool(_collection().find_one({
        "studentId": normalize_student_id(student_id),
        "testId": str(test_id),
        "isPractice": False,
        "status": {
            "$in": [
                STATUS_IN_PROGRESS,
                STATUS_EXPIRED,
                STATUS_EXPIRED_PENDING_UPLOAD,
                STATUS_FINALIZING,
            ],
        },
    }, {"_id": 1}))


def _validate_answer(answer, question):
    if not isinstance(answer, dict) or not question:
        return None, "invalid_question_id"
    if answer.get("type") != question.get("type"):
        return None, "invalid_answer_type"
    qid = question.get("questionId")
    q_type = question.get("type")
    base = {"questionId": qid, "type": q_type}
    allowed = {str(item.get("id")) for item in (question.get("answers") or [])}
    if q_type == "single":
        selected = answer.get("selectedAnswer")
        if selected is None or str(selected) not in allowed:
            return None, "invalid_answer_option"
        base["selectedAnswer"] = str(selected)
    elif q_type == "multiple":
        selected = [str(value) for value in (answer.get("selectedAnswers") or [])]
        if not selected or len(selected) != len(set(selected)) or not set(selected).issubset(allowed):
            return None, "invalid_answer_option"
        base["selectedAnswers"] = selected
    elif q_type == "text":
        text = str(answer.get("textAnswer") or "").strip()
        if not text:
            return None, "invalid_text_answer"
        base["textAnswer"] = text
    else:
        return None, "invalid_answer_type"
    return base, None


def _practice_correct_payload(question):
    q_type = question.get("type")
    if q_type in ("single", "multiple"):
        return {
            "correctOptionIds": [
                str(option.get("id"))
                for option in (question.get("answers") or [])
                if option.get("isCorrect")
            ],
        }
    if q_type == "text":
        return {
            "correctAnswers": [
                str(value) for value in (question.get("correctAnswers") or [])
            ],
        }
    return {}


def _practice_feedback(answer, question):
    scored = score_answer_from_raw(answer, question)
    return {
        "questionId": question.get("questionId"),
        "answer": scored,
        "isCorrect": bool(scored.get("isCorrect")),
        "points": int(scored.get("points") or 0),
        "correct": _practice_correct_payload(question),
    }


def sync_attempt_commits(attempt_id, student_id, commits):
    """Idempotent v2 mirror. Local committed answers remain authoritative."""
    student_id = normalize_student_id(student_id)
    if not isinstance(commits, list) or not commits:
        return {"success": False, "error": "commits_required"}
    if len(commits) > 25:
        return {"success": False, "error": "commits_batch_too_large"}
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id), "studentId": student_id})
    except Exception:
        doc = None
    if not doc:
        return {"success": False, "error": "attempt_not_found"}
    doc = _mark_expired_if_needed(doc)
    if doc.get("status") != STATUS_IN_PROGRESS:
        return {"success": False, "error": "time_expired"}

    definition = _definition_for_attempt(doc) or {}
    by_qid = {q.get("questionId"): q for q in (definition.get("questions") or [])}
    acked, conflicts, errors = [], [], []
    seen_ids = {item.get("commitId") for item in (doc.get("commits") or [])}
    for item in commits:
        commit_id = str(item.get("commitId") or "")
        qid = item.get("questionId")
        if not commit_id:
            errors.append({"questionId": qid, "error": "commit_id_required"})
            continue
        if commit_id in seen_ids:
            acked.append(commit_id)
            continue
        if not isinstance(qid, int) or isinstance(qid, bool) or qid <= 0:
            errors.append({"questionId": qid, "commitId": commit_id, "error": "invalid_question_id"})
            continue
        normalized, error = _validate_answer(item, by_qid.get(qid))
        if error:
            errors.append({"questionId": qid, "commitId": commit_id, "error": error})
            continue
        existing = next((a for a in (doc.get("answers") or []) if a.get("questionId") == qid), None)
        if existing and not _answers_equal(existing, normalized):
            conflicts.append({"questionId": qid, "commitId": commit_id, "error": "server_answer_conflict"})
            continue
        commit_doc = {
            **normalized,
            "commitId": commit_id,
            "sequence": int(item.get("sequence") or 0),
            "committedAtMoscow": item.get("committedAtMoscow"),
        }
        if commit_doc["sequence"] <= 0:
            errors.append({"questionId": qid, "commitId": commit_id, "error": "invalid_commit_sequence"})
            continue
        committed_at = item.get("committedAtMoscow")
        try:
            parsed_committed_at = datetime.fromisoformat(str(committed_at))
        except Exception:
            parsed_committed_at = None
        if (
            not parsed_committed_at
            or not parsed_committed_at.tzinfo
            or parsed_committed_at.utcoffset() is None
            or parsed_committed_at.utcoffset().total_seconds() != 10_800
        ):
            errors.append({"questionId": qid, "commitId": commit_id, "error": "invalid_committed_at"})
            continue
        commit_doc["committedAtMoscow"] = parsed_committed_at.astimezone(MOSCOW_TZ).isoformat()
        query = {"_id": doc["_id"], "status": STATUS_IN_PROGRESS, "commits.commitId": {"$ne": commit_id}}
        update = {"$push": {"commits": commit_doc}, "$set": {"lastSyncAtMoscow": datetime.now(MOSCOW_TZ).isoformat()}}
        if not existing:
            query["answers.questionId"] = {"$ne": qid}
            update["$push"]["answers"] = normalized
        result = _collection().update_one(query, update)
        if result.modified_count or _collection().find_one({"_id": doc["_id"], "commits.commitId": commit_id}):
            acked.append(commit_id)
            seen_ids.add(commit_id)
            continue
        fresh = _collection().find_one({"_id": doc["_id"]}) or {}
        fresh_answer = next((a for a in (fresh.get("answers") or []) if a.get("questionId") == qid), None)
        if fresh_answer and _answers_equal(fresh_answer, normalized):
            mirrored = _collection().update_one(
                {"_id": doc["_id"], "status": STATUS_IN_PROGRESS, "commits.commitId": {"$ne": commit_id}},
                {"$push": {"commits": commit_doc}, "$set": {"lastSyncAtMoscow": datetime.now(MOSCOW_TZ).isoformat()}},
            )
            if mirrored.modified_count or _collection().find_one({"_id": doc["_id"], "commits.commitId": commit_id}):
                acked.append(commit_id)
                seen_ids.add(commit_id)
        elif fresh_answer:
            conflicts.append({"questionId": qid, "commitId": commit_id, "error": "server_answer_conflict"})

    server_count = len((_collection().find_one({"_id": doc["_id"]}, {"answers": 1}) or {}).get("answers") or [])
    return {
        "success": not errors and not conflicts,
        "ackedCommitIds": acked,
        "conflicts": conflicts,
        "errors": errors,
        "serverAnswerCount": server_count,
        "serverNowMoscow": datetime.now(MOSCOW_TZ).isoformat(),
        "serverNowEpochMs": int(datetime.now(MOSCOW_TZ).timestamp() * 1000),
    }


def _canonical_snapshot_hash(snapshot):
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def finalize_attempt_v2(attempt_id, student_id, payload):
    """Idempotent finalization saga; final local snapshot wins over the mirror."""
    student_id = normalize_student_id(student_id)
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id), "studentId": student_id})
    except Exception:
        doc = None
    if not doc:
        return {"success": False, "error": "attempt_not_found"}
    if doc.get("schemaVersion") != 2 or doc.get("isPractice"):
        return {"success": False, "error": "v2_attempt_required"}
    if doc.get("status") == STATUS_SUBMITTED and doc.get("linkedSessionId"):
        return {
            "success": True,
            "alreadyFinalized": True,
            "sessionId": doc.get("linkedSessionId"),
            "score": doc.get("finalScore"),
            "stats": doc.get("finalStats"),
        }

    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict):
        return {"success": False, "error": "snapshot_required"}
    supplied_answers = snapshot.get("answers") or []
    if not isinstance(supplied_answers, list):
        return {"success": False, "error": "invalid_snapshot"}
    if str(snapshot.get("attemptId") or "") != str(attempt_id):
        return {"success": False, "error": "attempt_id_mismatch"}
    if snapshot.get("reason") not in ("manual", "timeout"):
        return {"success": False, "error": "invalid_snapshot_reason"}
    try:
        completed_at = datetime.fromisoformat(str(snapshot.get("completedAtMoscow")))
    except Exception:
        completed_at = None
    if (
        not completed_at
        or not completed_at.tzinfo
        or completed_at.utcoffset() is None
        or completed_at.utcoffset().total_seconds() != 10_800
    ):
        return {"success": False, "error": "invalid_completed_at"}

    upload_deadline = to_datetime(doc.get("uploadDeadlineMoscow"))
    if (
        doc.get("status") != STATUS_FINALIZING
        and upload_deadline
        and datetime.now(MOSCOW_TZ) > upload_deadline.astimezone(MOSCOW_TZ)
    ):
        return {"success": False, "error": "upload_window_closed"}
    if str(snapshot.get("testVersionId") or "") != str(doc.get("testVersionId") or ""):
        return {"success": False, "error": "test_version_mismatch"}

    definition = _definition_for_attempt(doc) or {}
    questions = definition.get("questions") or []
    by_qid = {q.get("questionId"): q for q in questions}
    normalized_answers, errors, seen_qids = [], [], set()
    for item in supplied_answers:
        qid = item.get("questionId") if isinstance(item, dict) else None
        if qid in seen_qids:
            errors.append({"questionId": qid, "error": "duplicate_question_id"})
            continue
        seen_qids.add(qid)
        normalized, error = _validate_answer(item, by_qid.get(qid))
        if error:
            errors.append({"questionId": qid, "error": error})
        else:
            normalized_answers.append(normalized)
    if errors:
        return {"success": False, "error": "invalid_snapshot", "errors": errors}

    order = doc.get("questionOrder") or []
    raw_by_qid = {item["questionId"]: item for item in normalized_answers}
    scored_answers, score = score_attempt_answers(questions, order, raw_by_qid)
    correct = sum(1 for item in scored_answers if item.get("isCorrect"))
    stats = {
        "correctAnswers": correct,
        "totalQuestions": len(order),
        "accuracy": round((correct / len(order)) * 100) if order else 0,
        "totalPoints": sum(int(item.get("points") or 0) for item in scored_answers),
    }
    canonical_snapshot = {
        "attemptId": str(attempt_id),
        "testVersionId": str(doc.get("testVersionId")),
        "answers": supplied_answers,
        "completedAtMoscow": snapshot.get("completedAtMoscow"),
        "reason": snapshot.get("reason") or "manual",
        "answeredCount": len(supplied_answers),
        "unansweredCount": max(0, len(order) - len(supplied_answers)),
    }
    snapshot_hash = _canonical_snapshot_hash(canonical_snapshot)
    if snapshot.get("snapshotHash") != snapshot_hash:
        return {"success": False, "error": "snapshot_hash_mismatch"}
    mirror_by_qid = {a.get("questionId"): a for a in (doc.get("answers") or [])}
    conflicts = [qid for qid, answer in raw_by_qid.items() if qid in mirror_by_qid and not _answers_equal(mirror_by_qid[qid], answer)]

    if doc.get("status") != STATUS_FINALIZING:
        _collection().update_one(
            {"_id": doc["_id"], "status": {"$in": [STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD]}},
            {"$set": {
                "status": STATUS_FINALIZING,
                "finalSnapshot": canonical_snapshot,
                "finalSnapshotHash": snapshot_hash,
                "finalScore": score,
                "finalStats": stats,
                "finalConflictingQuestionIds": conflicts,
                "answers": normalized_answers,
                "finalizingAtMoscow": datetime.now(MOSCOW_TZ).isoformat(),
            }},
        )
    session_result = insert_completed_test_session(
        student_id=student_id,
        test_id=doc.get("testId"),
        test_title=definition.get("title") or "",
        answers=scored_answers,
        score=score,
        time_spent_minutes=max(1, int((datetime.now(MOSCOW_TZ) - to_datetime(doc.get("startedAtMoscow"))).total_seconds() // 60)) if to_datetime(doc.get("startedAtMoscow")) else 1,
        question_order=order,
        attempt_id=str(attempt_id),
        test_version_id=doc.get("testVersionId"),
        final_snapshot_hash=snapshot_hash,
    )
    if not session_result.get("success") and session_result.get("error") != "test_already_completed":
        return session_result
    session_id = session_result.get("sessionId") or session_result.get("existingSessionId")
    _collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "status": STATUS_SUBMITTED,
            "submittedAtMoscow": datetime.now(MOSCOW_TZ).isoformat(),
            "linkedSessionId": session_id,
            "finalScore": score,
            "finalStats": stats,
        }},
    )
    return {"success": True, "sessionId": session_id, "score": score, "stats": stats, "conflictCount": len(conflicts)}


def build_question_order(test):
    questions = test.get("questions") or []
    question_ids = [q.get("questionId") for q in questions if q.get("questionId") is not None]
    if not question_ids:
        return None, "test_has_no_questions"
    if any(not isinstance(qid, int) or isinstance(qid, bool) or qid <= 0 for qid in question_ids):
        return None, "invalid_question_ids_in_test"
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
    schema_version = int(doc.get("schemaVersion") or 1)
    public_started_at = doc.get("startedAtMoscow") if schema_version == 2 else doc.get("startedAt")
    public_expires_at = doc.get("answerDeadlineMoscow") if schema_version == 2 else expires_at
    is_practice = bool(doc.get("isPractice"))
    time_expired = (not is_practice) and doc.get("status") in (STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD)
    if not is_practice and not time_expired and expires_at:
        if remaining_seconds(expires_at) <= 0 and doc.get("status") == STATUS_IN_PROGRESS:
            time_expired = True
    answers = doc.get("answers") or []
    answered_ids = [a.get("questionId") for a in answers if a.get("questionId") is not None]
    payload = {
        "attemptId": attempt_id,
        "studentId": doc.get("studentId"),
        "testId": doc.get("testId"),
        "status": STATUS_EXPIRED if time_expired and doc.get("status") == STATUS_IN_PROGRESS else doc.get("status"),
        "isPractice": is_practice,
        "hasTimeLimit": not is_practice,
        "startedAt": public_started_at,
        "expiresAt": public_expires_at,
        "remainingSeconds": 0 if is_practice or time_expired else remaining_seconds(expires_at),
        "timeExpired": time_expired,
        "questionOrder": doc.get("questionOrder") or [],
        "answers": answers,
        "answeredCount": len(answered_ids),
        "totalQuestions": len(doc.get("questionOrder") or []),
        "linkedSessionId": doc.get("linkedSessionId"),
        "serverReceivedAnswerCount": len(answers),
        "lastSyncAtMoscow": doc.get("lastSyncAtMoscow"),
        "finalSnapshotHash": doc.get("finalSnapshotHash"),
        "finalSnapshotConflicted": bool(doc.get("finalConflictingQuestionIds")),
        "uploadedAtMoscow": doc.get("submittedAtMoscow"),
        "schemaVersion": schema_version,
        "testVersionId": doc.get("testVersionId"),
        "serverNowMoscow": build_attempt_time_fields(
            doc.get("startedAtMoscow") or doc.get("startedAt"), 0
        )["serverNowMoscow"],
        "serverNowEpochMs": int(datetime.now(MOSCOW_TZ).timestamp() * 1000),
    }
    for key in (
        "startedAtMoscow", "startedAtEpochMs", "answerDeadlineMoscow",
        "answerDeadlineEpochMs", "uploadDeadlineMoscow", "uploadDeadlineEpochMs",
    ):
        if doc.get(key) is not None:
            payload[key] = doc.get(key)
    if include_questions and test:
        sanitized = questions_in_order(test, payload["questionOrder"])
        payload["questions"] = enrich_questions_with_locks(sanitized, answered_ids)
        if is_practice:
            by_qid = {q.get("questionId"): q for q in (test.get("questions") or [])}
            payload["practiceFeedback"] = [
                _practice_feedback(answer, by_qid.get(answer.get("questionId")))
                for answer in answers
                if by_qid.get(answer.get("questionId"))
            ]
    return payload


def _mark_expired_if_needed(doc):
    if not doc or doc.get("status") != STATUS_IN_PROGRESS:
        return doc
    if doc.get("isPractice"):
        return doc
    if remaining_seconds(doc.get("expiresAt")) <= 0:
        _collection().update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "status": STATUS_EXPIRED_PENDING_UPLOAD if doc.get("schemaVersion") == 2 else STATUS_EXPIRED,
                "expiredAt": now_utc_iso(),
            }},
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
            "isPractice": bool(doc.get("isPractice")),
        })
        if existing:
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, _definition_for_attempt(existing) or test, include_questions=True),
            }
        return {"success": False, "error": "attempt_conflict"}

    doc["_id"] = result.inserted_id
    definition = _definition_for_attempt(doc) or test
    return {
        "success": True,
        "resumed": False,
        "attempt": _serialize_attempt(doc, definition, include_questions=True),
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
        if existing.get("status") in (STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD):
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, _definition_for_attempt(existing) or test, include_questions=True),
            }

    if is_practice:
        return None
    expired = coll.find_one({**filters, "status": {"$in": [STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD]}})
    if expired:
        return {
            "success": True,
            "resumed": True,
            "attempt": _serialize_attempt(expired, _definition_for_attempt(expired) or test, include_questions=True),
        }
    return None


def start_attempt(student_id, test_id, is_practice=False, client_schema_version=None):
    student_id = normalize_student_id(student_id)
    if student_id is None:
        return {"success": False, "error": "invalid_student_id"}

    test = _get_test_document(test_id)
    if not test:
        return {"success": False, "error": "test_not_found"}

    coll = _collection()
    schema_version = 1 if is_practice else (2 if int(client_schema_version or 1) >= 2 else 1)

    if is_practice:
        current_test = get_test_by_id(test_id) or test
        completed_session = get_test_session_by_student_and_test(student_id, test_id)
        access = resolve_student_test_access(
            current_test,
            has_completed_session=bool(completed_session),
            has_open_official_attempt=_has_open_official_attempt(
                student_id, test_id
            ),
        )
        if not access.can_practice:
            return {"success": False, "error": access.practice_error}

        resumed = _resume_pending_attempt(coll, student_id, test_id, test, True)
        if resumed:
            return resumed

        order, order_err = build_question_order(test)
        if order_err:
            return {"success": False, "error": order_err}

        started_at = now_utc_iso()
        version = ensure_test_version(test)

        doc = {
            "studentId": student_id,
            "testId": str(test_id),
            "status": STATUS_IN_PROGRESS,
            "isPractice": True,
            "startedAt": started_at,
            "expiresAt": None,
            "hasTimeLimit": False,
            "questionOrder": order,
            "answers": [],
            "createdAt": started_at,
            "schemaVersion": 1,
        }
        if version:
            doc["testVersionId"] = str(version["_id"])
        return _insert_attempt_doc(coll, doc, test)

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
                "attempt": _serialize_attempt(existing, _definition_for_attempt(existing) or test, include_questions=True),
            }
        if existing.get("status") in (STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD):
            if get_test_session_by_student_and_test(student_id, test_id):
                return {"success": False, "error": "attempt_expired"}
            return {
                "success": True,
                "resumed": True,
                "attempt": _serialize_attempt(existing, _definition_for_attempt(existing) or test, include_questions=True),
            }

    expired_pending = coll.find_one({
        "studentId": student_id,
        "testId": str(test_id),
        "status": {"$in": [STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD]},
        "isPractice": False,
    })
    if expired_pending and not get_test_session_by_student_and_test(student_id, test_id):
        return {
            "success": True,
            "resumed": True,
            "attempt": _serialize_attempt(expired_pending, _definition_for_attempt(expired_pending) or test, include_questions=True),
        }

    open_ok, window_err = is_test_window_open(test)
    if not open_ok:
        return {"success": False, "error": window_err}

    order, order_err = build_question_order(test)
    if order_err:
        return {"success": False, "error": order_err}

    started_at = now_utc_iso()
    time_limit = int(test.get("timeLimitMinutes") or 0)
    expires_at = compute_expires_at(started_at, time_limit)
    time_fields = build_attempt_time_fields(started_at, time_limit)
    version = ensure_test_version(test) if schema_version == 2 else None

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
        "schemaVersion": schema_version,
        **time_fields,
    }
    if version:
        doc["testVersionId"] = str(version["_id"])
    return _insert_attempt_doc(coll, doc, test)


def get_attempt_for_student(attempt_id, student_id):
    student_id = normalize_student_id(student_id)
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}
    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}
    if doc.get("isPractice"):
        access_error = _practice_access_error(doc, student_id)
        if access_error:
            return {"success": False, "error": access_error}
    doc = _mark_expired_if_needed(doc)
    test = _definition_for_attempt(doc)
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
    test = _definition_for_attempt(doc)
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
        "status": {"$in": [STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD]},
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
    if doc.get("isPractice"):
        access_error = _practice_access_error(doc, student_id)
        if access_error:
            return {"success": False, "error": access_error}

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

    test = _definition_for_attempt(doc) if include_questions else None
    payload = {
        "success": True,
        "attempt": _serialize_attempt(doc, test, include_questions=include_questions),
    }
    if idempotent:
        payload["idempotent"] = True
    return payload


def check_practice_answer(attempt_id, student_id, answer_data):
    """Зафиксировать ответ тренировки и сразу вернуть проверку."""
    student_id = normalize_student_id(student_id)
    try:
        doc = _collection().find_one({"_id": ObjectId(attempt_id)})
    except Exception:
        return {"success": False, "error": "attempt_not_found"}

    if not doc or doc.get("studentId") != student_id:
        return {"success": False, "error": "attempt_not_found"}
    if not doc.get("isPractice"):
        return {"success": False, "error": "practice_attempt_required"}
    if doc.get("status") != STATUS_IN_PROGRESS:
        return {"success": False, "error": "attempt_not_active"}

    access_error = _practice_access_error(doc, student_id)
    if access_error:
        return {"success": False, "error": access_error}

    question_id = answer_data.get("questionId") if isinstance(answer_data, dict) else None
    if question_id not in (doc.get("questionOrder") or []):
        return {"success": False, "error": "invalid_question_id"}

    definition = _definition_for_attempt(doc) or {}
    question = next(
        (
            item
            for item in (definition.get("questions") or [])
            if item.get("questionId") == question_id
        ),
        None,
    )
    normalized, error = _validate_answer(answer_data, question)
    if error:
        return {"success": False, "error": error}

    existing = next(
        (
            item
            for item in (doc.get("answers") or [])
            if item.get("questionId") == question_id
        ),
        None,
    )
    idempotent = False
    if existing:
        if not _answers_equal(existing, normalized):
            return {"success": False, "error": "answer_locked"}
        scored = score_answer_from_raw(existing, question)
        idempotent = True
    else:
        scored = score_answer_from_raw(normalized, question)
        result = _collection().update_one(
            {
                "_id": doc["_id"],
                "studentId": student_id,
                "status": STATUS_IN_PROGRESS,
                "isPractice": True,
                "answers.questionId": {"$ne": question_id},
            },
            {
                "$push": {"answers": scored},
                "$set": {"updatedAt": now_utc_iso()},
            },
        )
        if not result.modified_count:
            fresh = _collection().find_one({"_id": doc["_id"]}) or {}
            existing = next(
                (
                    item
                    for item in (fresh.get("answers") or [])
                    if item.get("questionId") == question_id
                ),
                None,
            )
            if not existing or not _answers_equal(existing, normalized):
                return {"success": False, "error": "answer_locked"}
            scored = score_answer_from_raw(existing, question)
            idempotent = True

    fresh = _collection().find_one({"_id": doc["_id"]}) or doc
    payload = {
        "success": True,
        "feedback": _practice_feedback(scored, question),
        "answeredCount": len(fresh.get("answers") or []),
        "totalQuestions": len(fresh.get("questionOrder") or []),
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
    if doc.get("isPractice"):
        access_error = _practice_access_error(doc, student_id)
        if access_error:
            return {"success": False, "error": access_error}

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
    if is_practice:
        access_error = _practice_access_error(doc, student_id)
        if access_error:
            return {"success": False, "error": access_error}
    doc = _mark_expired_if_needed(doc)
    status = doc.get("status")

    if status == STATUS_SUBMITTED:
        return {"success": False, "error": "attempt_not_active"}

    if status not in (STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD):
        return {"success": False, "error": "attempt_not_active"}

    if not is_practice and status == STATUS_IN_PROGRESS and remaining_seconds(doc.get("expiresAt")) <= 0:
        doc = _mark_expired_if_needed(doc)
        status = doc.get("status")

    if not is_practice and status == STATUS_IN_PROGRESS and remaining_seconds(doc.get("expiresAt")) <= 0:
        return {"success": False, "error": "time_expired"}

    test = _definition_for_attempt(doc)
    if not test:
        return {"success": False, "error": "test_not_found"}

    questions = test.get("questions") or []
    order = doc.get("questionOrder") or []
    raw_answers = doc.get("answers") or []
    if not is_practice and order and not raw_answers:
        return {"success": False, "error": "empty_attempt_answers"}

    raw_by_qid = {a.get("questionId"): a for a in raw_answers}
    scored_answers, score = score_attempt_answers(questions, order, raw_by_qid)

    started = to_datetime(doc.get("startedAt"))
    completed = datetime.now(MOSCOW_TZ)
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

    if status not in (STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD):
        return {"success": False, "error": "attempt_not_active"}

    test = _definition_for_attempt(doc)
    if not test:
        return {"success": False, "error": "test_not_found"}

    student_id = normalize_student_id(doc.get("studentId"))
    if get_test_session_by_student_and_test(student_id, doc.get("testId")):
        return {"success": False, "error": "test_already_completed"}

    questions = test.get("questions") or []
    order = doc.get("questionOrder") or []
    raw_by_qid = {a.get("questionId"): a for a in (doc.get("answers") or [])}
    scored_answers, score = score_attempt_answers(questions, order, raw_by_qid)

    started = to_datetime(doc.get("startedAt"))
    completed = datetime.now(MOSCOW_TZ)
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
        return [STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD]
    if status_filter == "all":
        return [STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD, STATUS_FINALIZING, STATUS_SUBMITTED]
    parts = [p.strip() for p in str(status_filter).split(",") if p.strip()]
    allowed = {STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD, STATUS_FINALIZING, STATUS_SUBMITTED}
    selected = [p for p in parts if p in allowed]
    return selected or [STATUS_IN_PROGRESS, STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD]


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
                "startedAtMoscow": 1,
                "answerDeadlineMoscow": 1,
                "uploadDeadlineMoscow": 1,
                "lastSyncAtMoscow": 1,
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
            "startedAtMoscow": row.get("startedAtMoscow"),
            "answerDeadlineMoscow": row.get("answerDeadlineMoscow"),
            "uploadDeadlineMoscow": row.get("uploadDeadlineMoscow"),
            "lastSyncAtMoscow": row.get("lastSyncAtMoscow"),
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
    time_expired = doc.get("status") in (STATUS_EXPIRED, STATUS_EXPIRED_PENDING_UPLOAD)
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
        "startedAt": doc.get("startedAtMoscow") or doc.get("startedAt"),
        "expiresAt": doc.get("answerDeadlineMoscow") or expires_at,
        "answerDeadlineMoscow": doc.get("answerDeadlineMoscow"),
        "uploadDeadlineMoscow": doc.get("uploadDeadlineMoscow"),
        "lastSyncAtMoscow": doc.get("lastSyncAtMoscow"),
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

    test = _definition_for_attempt(doc)
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
