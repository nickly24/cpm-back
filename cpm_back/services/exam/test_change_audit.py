from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cpm_back.db.mongo import get_mongo_db


EVENT_QUESTION_ADDED = "question_added"
EVENT_QUESTION_REMOVED = "question_removed"
EVENT_QUESTION_UPDATED = "question_updated"
EVENT_QUESTION_REORDERED = "question_reordered"
EVENT_METADATA_UPDATED = "metadata_updated"

MAX_TEXT_LENGTH = 4000
_index_ready = False


def _collection():
    return get_mongo_db().test_question_change_log


def _ensure_indexes():
    global _index_ready
    if _index_ready:
        return
    coll = _collection()
    coll.create_index(
        [("testId", 1), ("questionId", 1), ("changedAt", -1)],
        name="test_question_changedAt",
    )
    coll.create_index([("testId", 1), ("changedAt", -1)], name="test_changedAt")
    coll.create_index([("changeKey", 1), ("revision", -1)], name="changeKey_revision")
    coll.create_index([("eventType", 1), ("changedAt", -1)], name="event_changedAt")
    _index_ready = True


def _now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _trim_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= MAX_TEXT_LENGTH:
        return value
    return value[:MAX_TEXT_LENGTH] + "…"


def _sanitize_question_snapshot(question: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not question:
        return None
    q = deepcopy(question)
    q["text"] = _trim_text(q.get("text") or "")
    if q.get("type") in ("single", "multiple"):
        answers = []
        for item in q.get("answers") or []:
            answers.append(
                {
                    "id": item.get("id"),
                    "text": _trim_text(item.get("text") or ""),
                    "isCorrect": bool(item.get("isCorrect")),
                }
            )
        q["answers"] = answers
        q.pop("correctAnswers", None)
    elif q.get("type") == "text":
        q["correctAnswers"] = [_trim_text(value or "") for value in (q.get("correctAnswers") or [])]
        q.pop("answers", None)
    else:
        q["answers"] = q.get("answers") or []
        q["correctAnswers"] = q.get("correctAnswers") or []
    return {
        "questionId": q.get("questionId"),
        "type": q.get("type"),
        "text": q.get("text") or "",
        "points": int(q.get("points") or 0),
        "answers": q.get("answers", []),
        "correctAnswers": q.get("correctAnswers", []),
    }


def _questions_by_id(test_doc: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    for raw in test_doc.get("questions") or []:
        question = _sanitize_question_snapshot(raw)
        qid = question.get("questionId") if question else None
        if isinstance(qid, int):
            result[qid] = question
    return result


def _metadata_snapshot(test_doc: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "title",
        "direction",
        "startDate",
        "endDate",
        "timeLimitMinutes",
        "published",
        "visible",
    ]
    return {key: test_doc.get(key) for key in keys}


def _question_signature(question: Dict[str, Any]) -> Tuple[Any, ...]:
    answers = tuple(
        sorted(
            (
                str(answer.get("id")),
                answer.get("text") or "",
                bool(answer.get("isCorrect")),
            )
            for answer in (question.get("answers") or [])
        )
    )
    correct_answers = tuple(sorted(str(item or "") for item in (question.get("correctAnswers") or [])))
    return (
        question.get("type"),
        question.get("text") or "",
        int(question.get("points") or 0),
        answers,
        correct_answers,
    )


def _question_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    diff: Dict[str, Any] = {}

    field_changes: Dict[str, Dict[str, Any]] = {}
    for key in ("type", "text", "points"):
        if before.get(key) != after.get(key):
            field_changes[key] = {"before": before.get(key), "after": after.get(key)}
    if field_changes:
        diff["fieldChanges"] = field_changes

    if before.get("type") in ("single", "multiple") or after.get("type") in ("single", "multiple"):
        before_answers = {str(item.get("id")): item for item in (before.get("answers") or [])}
        after_answers = {str(item.get("id")): item for item in (after.get("answers") or [])}
        added, removed, updated = [], [], []

        for answer_id in sorted(after_answers.keys() - before_answers.keys()):
            added.append(after_answers[answer_id])
        for answer_id in sorted(before_answers.keys() - after_answers.keys()):
            removed.append(before_answers[answer_id])
        for answer_id in sorted(before_answers.keys() & after_answers.keys()):
            prev = before_answers[answer_id]
            cur = after_answers[answer_id]
            if prev.get("text") != cur.get("text") or bool(prev.get("isCorrect")) != bool(cur.get("isCorrect")):
                updated.append(
                    {
                        "id": cur.get("id"),
                        "before": {
                            "text": prev.get("text"),
                            "isCorrect": bool(prev.get("isCorrect")),
                        },
                        "after": {
                            "text": cur.get("text"),
                            "isCorrect": bool(cur.get("isCorrect")),
                        },
                    }
                )
        if added or removed or updated:
            diff["answers"] = {"added": added, "removed": removed, "updated": updated}

    if before.get("type") == "text" or after.get("type") == "text":
        prev = set(str(item or "") for item in (before.get("correctAnswers") or []))
        cur = set(str(item or "") for item in (after.get("correctAnswers") or []))
        added = sorted(cur - prev)
        removed = sorted(prev - cur)
        if added or removed:
            diff["correctAnswers"] = {"added": added, "removed": removed}

    return diff


def _metadata_diff(before_meta: Dict[str, Any], after_meta: Dict[str, Any]) -> Dict[str, Any]:
    changed = {}
    for key in sorted(set(before_meta.keys()) | set(after_meta.keys())):
        if before_meta.get(key) != after_meta.get(key):
            changed[key] = {"before": before_meta.get(key), "after": after_meta.get(key)}
    return changed


def _build_events(before_test: Dict[str, Any], after_test: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    before_q = _questions_by_id(before_test)
    after_q = _questions_by_id(after_test)
    shared_ids = sorted(before_q.keys() & after_q.keys())

    for qid in shared_ids:
        before_item = before_q[qid]
        after_item = after_q[qid]
        if before_item == after_item:
            continue
        events.append(
            {
                "questionId": qid,
                "eventType": EVENT_QUESTION_UPDATED,
                "before": before_item,
                "after": after_item,
                "diff": _question_diff(before_item, after_item),
            }
        )

    removed_ids = set(before_q.keys() - after_q.keys())
    added_ids = set(after_q.keys() - before_q.keys())

    reorder_pairs: List[Tuple[int, int]] = []
    added_by_signature: Dict[Tuple[Any, ...], List[int]] = {}
    for qid in sorted(added_ids):
        signature = _question_signature(after_q[qid])
        added_by_signature.setdefault(signature, []).append(qid)

    for qid in sorted(removed_ids):
        signature = _question_signature(before_q[qid])
        candidates = added_by_signature.get(signature) or []
        if len(candidates) != 1:
            continue
        added_qid = candidates[0]
        reorder_pairs.append((qid, added_qid))
        added_ids.discard(added_qid)
        removed_ids.discard(qid)
        added_by_signature[signature] = []

    for old_qid, new_qid in reorder_pairs:
        events.append(
            {
                "questionId": new_qid,
                "eventType": EVENT_QUESTION_REORDERED,
                "before": before_q[old_qid],
                "after": after_q[new_qid],
                "diff": {"fromQuestionId": old_qid, "toQuestionId": new_qid},
            }
        )

    for qid in sorted(removed_ids):
        events.append(
            {
                "questionId": qid,
                "eventType": EVENT_QUESTION_REMOVED,
                "before": before_q[qid],
                "after": None,
                "diff": {"removed": True},
            }
        )

    for qid in sorted(added_ids):
        events.append(
            {
                "questionId": qid,
                "eventType": EVENT_QUESTION_ADDED,
                "before": None,
                "after": after_q[qid],
                "diff": {"added": True},
            }
        )

    before_meta = _metadata_snapshot(before_test)
    after_meta = _metadata_snapshot(after_test)
    meta_changes = _metadata_diff(before_meta, after_meta)
    if meta_changes:
        events.append(
            {
                "questionId": None,
                "eventType": EVENT_METADATA_UPDATED,
                "before": before_meta,
                "after": after_meta,
                "diff": {"fieldChanges": meta_changes},
            }
        )

    return events


def _revision_for_change_key(change_key: str, cache: Dict[str, int]) -> int:
    if change_key in cache:
        cache[change_key] += 1
        return cache[change_key]

    row = _collection().find_one(
        {"changeKey": change_key},
        sort=[("revision", -1)],
        projection={"revision": 1},
    )
    current = int((row or {}).get("revision") or 0)
    cache[change_key] = current + 1
    return cache[change_key]


def log_test_changes(
    before_test: Dict[str, Any],
    after_test: Dict[str, Any],
    actor: Optional[Dict[str, Any]] = None,
    source: str = "update_test",
) -> Dict[str, Any]:
    if not before_test or not after_test:
        return {"success": False, "error": "before_and_after_required"}

    test_id = str(after_test.get("_id") or before_test.get("_id") or "")
    if not test_id:
        return {"success": False, "error": "test_id_required"}

    events = _build_events(before_test, after_test)
    if not events:
        return {"success": True, "eventsInserted": 0}

    _ensure_indexes()
    now = _now_iso()
    revision_cache: Dict[str, int] = {}
    payload = []
    actor_payload = {
        "userId": (actor or {}).get("id"),
        "role": (actor or {}).get("role"),
        "fullName": (actor or {}).get("full_name"),
    }

    for event in events:
        qid = event.get("questionId")
        change_key = f"{test_id}#{qid}" if qid is not None else f"{test_id}#meta"
        payload.append(
            {
                "testId": test_id,
                "questionId": qid,
                "changeKey": change_key,
                "eventType": event["eventType"],
                "actor": actor_payload,
                "changedAt": now,
                "revision": _revision_for_change_key(change_key, revision_cache),
                "before": event.get("before"),
                "after": event.get("after"),
                "diff": event.get("diff") or {},
                "context": {"source": source},
            }
        )

    _collection().insert_many(payload, ordered=True)
    return {"success": True, "eventsInserted": len(payload)}


def log_test_metadata_change(
    test_id: str,
    before_meta: Dict[str, Any],
    after_meta: Dict[str, Any],
    actor: Optional[Dict[str, Any]] = None,
    source: str = "metadata_update",
) -> Dict[str, Any]:
    _ensure_indexes()
    changes = _metadata_diff(before_meta or {}, after_meta or {})
    if not changes:
        return {"success": True, "eventsInserted": 0}
    change_key = f"{test_id}#meta"
    revision_cache: Dict[str, int] = {}
    payload = {
        "testId": str(test_id),
        "questionId": None,
        "changeKey": change_key,
        "eventType": EVENT_METADATA_UPDATED,
        "actor": {
            "userId": (actor or {}).get("id"),
            "role": (actor or {}).get("role"),
            "fullName": (actor or {}).get("full_name"),
        },
        "changedAt": _now_iso(),
        "revision": _revision_for_change_key(change_key, revision_cache),
        "before": before_meta or {},
        "after": after_meta or {},
        "diff": {"fieldChanges": changes},
        "context": {"source": source},
    }
    _collection().insert_one(payload)
    return {"success": True, "eventsInserted": 1}


def _serialize_log_item(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item)
    row["id"] = str(row.pop("_id"))
    return row


def list_test_changes(
    test_id: str,
    page: int = 1,
    limit: int = 20,
    question_id: Optional[int] = None,
    event_type: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_indexes()
    page = max(1, int(page or 1))
    limit = min(100, max(1, int(limit or 20)))
    query: Dict[str, Any] = {"testId": str(test_id)}
    if question_id is not None:
        query["questionId"] = int(question_id)
    if event_type:
        query["eventType"] = event_type

    coll = _collection()
    total = coll.count_documents(query)
    skip = (page - 1) * limit
    cursor = coll.find(query).sort("changedAt", -1).skip(skip).limit(limit)
    items = [_serialize_log_item(row) for row in cursor]

    total_pages = (total + limit - 1) // limit if total else 1
    return {
        "success": True,
        "items": items,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": total_pages,
            "hasNext": page < total_pages,
            "hasPrev": page > 1,
        },
    }


def list_question_changes(
    test_id: str,
    question_id: int,
    page: int = 1,
    limit: int = 20,
) -> Dict[str, Any]:
    return list_test_changes(
        test_id=test_id,
        page=page,
        limit=limit,
        question_id=question_id,
    )


def list_recent_test_changes(page: int = 1, limit: int = 20) -> Dict[str, Any]:
    _ensure_indexes()
    page = max(1, int(page or 1))
    limit = min(100, max(1, int(limit or 20)))
    coll = _collection()
    total = coll.count_documents({})
    skip = (page - 1) * limit
    cursor = coll.find({}).sort("changedAt", -1).skip(skip).limit(limit)
    items = [_serialize_log_item(row) for row in cursor]
    total_pages = (total + limit - 1) // limit if total else 1
    return {
        "success": True,
        "items": items,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": total_pages,
            "hasNext": page < total_pages,
            "hasPrev": page > 1,
        },
    }
