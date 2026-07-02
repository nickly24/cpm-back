from datetime import datetime, timedelta
from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.create_test import create_test, get_test_by_id


LOCK_TTL_SECONDS = 90


def _now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
    return None


def _serialize_draft(doc):
    if not doc:
        return None
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


def _empty_canvas():
    return {
        "questions": [],
        "layout": {},
    }


def _question_to_canvas(question, index):
    qid = f"q_{question.get('questionId') or index + 1}"
    q_type = question.get("type") or "single"
    answers = []
    if q_type == "text":
        for i, value in enumerate(question.get("correctAnswers") or []):
            answers.append({
                "id": f"{qid}_text_{i + 1}",
                "kind": "textAnswer",
                "text": value,
                "isCorrect": True,
            })
    else:
        for answer in question.get("answers") or []:
            answers.append({
                "id": f"{qid}_{answer.get('id') or len(answers) + 1}",
                "kind": "answer",
                "text": answer.get("text") or "",
                "isCorrect": bool(answer.get("isCorrect")),
            })
    return {
        "id": qid,
        "type": q_type,
        "text": question.get("text") or "",
        "points": int(question.get("points") or 1),
        "answers": answers,
    }


def _canvas_from_test(test):
    questions = [
        _question_to_canvas(question, index)
        for index, question in enumerate(test.get("questions") or [])
    ]
    layout = {}
    for index, question in enumerate(questions):
        row = index // 3
        col = index % 3
        layout[question["id"]] = {"x": 120 + col * 360, "y": 120 + row * 260}
    return {"questions": questions, "layout": layout}


def _base_metadata(payload=None):
    payload = payload or {}
    return {
        "title": payload.get("title") or "Новый драфт теста",
        "direction": payload.get("direction") or "",
        "startDate": payload.get("startDate") or "",
        "endDate": payload.get("endDate") or "",
        "timeLimitMinutes": int(payload.get("timeLimitMinutes") or 30),
        "published": bool(payload.get("published", True)),
        "visible": bool(payload.get("visible", False)),
    }


def list_test_drafts(status="active"):
    db = get_mongo_db()
    query = {}
    if status and status != "all":
        query["status"] = status
    cursor = db.test_drafts.find(query).sort("updatedAt", -1)
    return [_serialize_draft(doc) for doc in cursor]


def get_test_draft(draft_id):
    try:
        db = get_mongo_db()
        return _serialize_draft(db.test_drafts.find_one({"_id": ObjectId(draft_id)}))
    except Exception:
        return None


def create_test_draft(payload, current_user=None):
    db = get_mongo_db()
    now = _now_iso()
    user_id = (current_user or {}).get("id")
    user_name = (current_user or {}).get("full_name")
    doc = {
        **_base_metadata(payload),
        "canvas": (payload or {}).get("canvas") or _empty_canvas(),
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
        "createdBy": user_id,
        "createdByName": user_name,
        "updatedBy": user_id,
        "updatedByName": user_name,
        "publishedTestId": None,
        "lockedBy": None,
        "lockedByName": None,
        "lockedUntil": None,
    }
    result = db.test_drafts.insert_one(doc)
    return get_test_draft(str(result.inserted_id))


def create_test_draft_from_test(test_id, current_user=None):
    test = get_test_by_id(test_id)
    if not test:
        return None
    payload = {
        "title": f"{test.get('title') or 'Тест'} — драфт",
        "direction": test.get("direction") or "",
        "startDate": test.get("startDate") or "",
        "endDate": test.get("endDate") or "",
        "timeLimitMinutes": test.get("timeLimitMinutes") or 30,
        "published": test.get("published", True),
        "visible": test.get("visible", False),
        "canvas": _canvas_from_test(test),
    }
    return create_test_draft(payload, current_user=current_user)


def update_test_draft(draft_id, payload, current_user=None):
    db = get_mongo_db()
    update = {}
    for key in ["title", "direction", "startDate", "endDate", "published", "visible", "canvas"]:
        if key in payload:
            update[key] = payload[key]
    if "timeLimitMinutes" in payload:
        update["timeLimitMinutes"] = int(payload.get("timeLimitMinutes") or 1)
    update["updatedAt"] = _now_iso()
    update["updatedBy"] = (current_user or {}).get("id")
    update["updatedByName"] = (current_user or {}).get("full_name")
    result = db.test_drafts.update_one(
        {"_id": ObjectId(draft_id), "status": "active"},
        {"$set": update},
    )
    if result.matched_count == 0:
        return None
    return get_test_draft(draft_id)


def lock_test_draft(draft_id, current_user=None, force=False):
    db = get_mongo_db()
    user_id = (current_user or {}).get("id")
    user_name = (current_user or {}).get("full_name")
    now = datetime.utcnow()
    until = now + timedelta(seconds=LOCK_TTL_SECONDS)
    existing = db.test_drafts.find_one({"_id": ObjectId(draft_id), "status": "active"})
    if not existing:
        return {"success": False, "error": "draft_not_found"}
    locked_until = _parse_dt(existing.get("lockedUntil"))
    locked_by = existing.get("lockedBy")
    if locked_by and str(locked_by) != str(user_id) and locked_until and locked_until > now and not force:
        return {
            "success": False,
            "error": "locked",
            "lockedBy": locked_by,
            "lockedByName": existing.get("lockedByName"),
            "lockedUntil": existing.get("lockedUntil"),
        }
    db.test_drafts.update_one(
        {"_id": ObjectId(draft_id)},
        {"$set": {
            "lockedBy": user_id,
            "lockedByName": user_name,
            "lockedUntil": until.isoformat() + "Z",
        }},
    )
    return {"success": True, "draft": get_test_draft(draft_id)}


def unlock_test_draft(draft_id, current_user=None):
    db = get_mongo_db()
    user_id = (current_user or {}).get("id")
    query = {"_id": ObjectId(draft_id)}
    existing = db.test_drafts.find_one(query)
    if not existing:
        return {"success": False, "error": "draft_not_found"}
    if existing.get("lockedBy") and str(existing.get("lockedBy")) != str(user_id):
        return {"success": False, "error": "locked_by_other"}
    db.test_drafts.update_one(
        query,
        {"$set": {"lockedBy": None, "lockedByName": None, "lockedUntil": None}},
    )
    return {"success": True}


def _validate_canvas(draft):
    errors = []
    canvas = draft.get("canvas") or {}
    questions = canvas.get("questions") or []
    if not draft.get("title"):
        errors.append({"targetId": "metadata", "message": "Укажите название теста"})
    if not draft.get("direction"):
        errors.append({"targetId": "metadata", "message": "Выберите направление"})
    if not draft.get("startDate") or not draft.get("endDate"):
        errors.append({"targetId": "metadata", "message": "Укажите даты начала и окончания"})
    if not questions:
        errors.append({"targetId": "canvas", "message": "Добавьте хотя бы один вопрос"})
    for index, question in enumerate(questions):
        qid = question.get("id") or f"question_{index + 1}"
        q_type = question.get("type")
        answers = question.get("answers") or []
        if not (question.get("text") or "").strip():
            errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: заполните текст"})
        if int(question.get("points") or 0) < 1:
            errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: баллы должны быть больше 0"})
        if q_type in ("single", "multiple"):
            regular = [a for a in answers if a.get("kind") == "answer"]
            correct = [a for a in regular if a.get("isCorrect")]
            if len(regular) < 2:
                errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: добавьте минимум два ответа"})
            if not correct:
                errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: отметьте правильный ответ"})
            if q_type == "single" and len(correct) > 1:
                errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: для одиночного выбора нужен один правильный ответ"})
            for answer in regular:
                if not (answer.get("text") or "").strip():
                    errors.append({"targetId": answer.get("id") or qid, "message": f"Вопрос {index + 1}: заполните текст ответа"})
        elif q_type == "text":
            text_answers = [a for a in answers if a.get("kind") == "textAnswer"]
            if not text_answers:
                errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: добавьте правильный текстовый ответ"})
            for answer in text_answers:
                if not (answer.get("text") or "").strip():
                    errors.append({"targetId": answer.get("id") or qid, "message": f"Вопрос {index + 1}: заполните текстовый ответ"})
        else:
            errors.append({"targetId": qid, "message": f"Вопрос {index + 1}: неизвестный тип вопроса"})
    return errors


def _draft_to_test_payload(draft):
    canvas = draft.get("canvas") or {}
    layout = canvas.get("layout") or {}
    questions = list(canvas.get("questions") or [])
    questions.sort(key=lambda q: (
        (layout.get(q.get("id")) or {}).get("y", 0),
        (layout.get(q.get("id")) or {}).get("x", 0),
    ))
    result_questions = []
    for index, question in enumerate(questions):
        q_type = question.get("type") or "single"
        result = {
            "questionId": index + 1,
            "type": q_type,
            "text": question.get("text") or "",
            "points": int(question.get("points") or 1),
            "answers": [],
            "correctAnswers": [],
        }
        if q_type == "text":
            result["correctAnswers"] = [
                a.get("text") or ""
                for a in (question.get("answers") or [])
                if a.get("kind") == "textAnswer"
            ]
        else:
            answer_id_ord = ord("a")
            for answer in question.get("answers") or []:
                if answer.get("kind") != "answer":
                    continue
                result["answers"].append({
                    "id": chr(answer_id_ord),
                    "text": answer.get("text") or "",
                    "isCorrect": bool(answer.get("isCorrect")),
                })
                answer_id_ord += 1
        result_questions.append(result)
    return {
        "title": draft.get("title") or "",
        "direction": draft.get("direction") or "",
        "startDate": draft.get("startDate") or "",
        "endDate": draft.get("endDate") or "",
        "timeLimitMinutes": int(draft.get("timeLimitMinutes") or 30),
        "published": bool(draft.get("published", True)),
        "visible": bool(draft.get("visible", False)),
        "questions": result_questions,
    }


def publish_test_draft(draft_id, current_user=None):
    db = get_mongo_db()
    draft = get_test_draft(draft_id)
    if not draft or draft.get("status") != "active":
        return {"success": False, "error": "draft_not_found"}, 404
    errors = _validate_canvas(draft)
    if errors:
        return {"success": False, "error": "validation_failed", "errors": errors}, 400
    payload = _draft_to_test_payload(draft)
    test_id = create_test(payload)
    db.test_drafts.update_one(
        {"_id": ObjectId(draft_id)},
        {"$set": {
            "status": "archived",
            "publishedTestId": test_id,
            "updatedAt": _now_iso(),
            "updatedBy": (current_user or {}).get("id"),
            "updatedByName": (current_user or {}).get("full_name"),
            "lockedBy": None,
            "lockedByName": None,
            "lockedUntil": None,
        }},
    )
    return {"success": True, "testId": test_id, "draft": get_test_draft(draft_id)}, 200
