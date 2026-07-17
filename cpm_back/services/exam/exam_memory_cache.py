"""
In-memory кэш exam-модуля (без Redis): один процесс gunicorn = свой кэш.

- Каталог опубликованных тестов (для /tests/student/available и каталога)
- Полный документ теста (для start / attempt)
- Санитизированные вопросы по testId (без ключей ответов)
"""
import copy
import random
import threading
import time

from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.test_sanitize import sanitize_question

TEST_DOC_TTL_SECONDS = 600
PUBLISHED_CATALOG_TTL_SECONDS = 20
DIRECTIONS_TTL_SECONDS = 300

PUBLISHED_TEST_PROJECTION = {
    "_id": 1,
    "title": 1,
    "direction": 1,
    "startDate": 1,
    "endDate": 1,
    "timeLimitMinutes": 1,
    "visible": 1,
    "published": 1,
}

_test_docs: dict[str, dict] = {}
_sanitized_question_maps: dict[str, dict] = {}
_published_catalog: dict | None = None
_directions: dict | None = None
_published_catalog_lock = threading.Lock()


def _is_fresh(stored_at: float, ttl_seconds: int) -> bool:
    return (time.time() - stored_at) < ttl_seconds


def _serialize_published_test(doc) -> dict:
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title"),
        "startDate": doc.get("startDate"),
        "endDate": doc.get("endDate"),
        "timeLimitMinutes": doc.get("timeLimitMinutes"),
        "visible": doc.get("visible", False),
        "published": doc.get("published", True),
        "direction": doc.get("direction"),
    }


def _load_published_tests_from_mongo() -> list[dict]:
    db = get_mongo_db()
    cursor = db.tests.find({}, PUBLISHED_TEST_PROJECTION).sort("startDate", -1)
    items = []
    for doc in cursor:
        if doc.get("published") is False:
            continue
        items.append(_serialize_published_test(doc))
    return items


def get_published_tests_light_cached() -> list[dict]:
    """Список опубликованных тестов без вопросов (общий для всех студентов)."""
    global _published_catalog
    if (
        _published_catalog is not None
        and time.time() < _published_catalog["expiresAt"]
    ):
        return copy.deepcopy(_published_catalog["items"])
    with _published_catalog_lock:
        if _published_catalog is not None and time.time() < _published_catalog["expiresAt"]:
            return copy.deepcopy(_published_catalog["items"])
        items = _load_published_tests_from_mongo()
        _published_catalog = {
            "items": items,
            "at": time.time(),
            "expiresAt": time.time() + random.uniform(15, 30),
        }
        return copy.deepcopy(items)


def get_test_document_cached(test_id):
    """Полный документ теста из Mongo tests."""
    key = str(test_id)
    entry = _test_docs.get(key)
    if entry and _is_fresh(entry["at"], TEST_DOC_TTL_SECONDS):
        return entry["doc"]

    try:
        doc = get_mongo_db().tests.find_one({"_id": ObjectId(key)})
    except Exception:
        return None

    if doc:
        _test_docs[key] = {"doc": doc, "at": time.time()}
    return doc


def get_sanitized_questions_map_cached(test_id) -> dict:
    """questionId -> вопрос без ключей (для attempt API)."""
    key = str(test_id)
    entry = _sanitized_question_maps.get(key)
    if entry and _is_fresh(entry["at"], TEST_DOC_TTL_SECONDS):
        return copy.deepcopy(entry["map"])

    doc = get_test_document_cached(key)
    if not doc:
        return {}

    qmap = {}
    for question in doc.get("questions") or []:
        qid = question.get("questionId")
        if qid:
            qmap[qid] = sanitize_question(question)

    _sanitized_question_maps[key] = {"map": qmap, "at": time.time()}
    return copy.deepcopy(qmap)


def get_directions_cached() -> list[dict]:
    """Справочник направлений MySQL."""
    global _directions
    if _directions is not None and _is_fresh(_directions["at"], DIRECTIONS_TTL_SECONDS):
        return copy.deepcopy(_directions["items"])

    from cpm_back.services.exam.get_directions import get_directions_from_db

    items = get_directions_from_db()
    _directions = {"items": items, "at": time.time()}
    return copy.deepcopy(items)


def invalidate_test_cache(test_id) -> None:
    key = str(test_id)
    _test_docs.pop(key, None)
    _sanitized_question_maps.pop(key, None)


def invalidate_published_tests_cache() -> None:
    global _published_catalog
    _published_catalog = None


def invalidate_directions_cache() -> None:
    global _directions
    _directions = None


def invalidate_all_exam_memory_cache() -> None:
    _test_docs.clear()
    _sanitized_question_maps.clear()
    invalidate_published_tests_cache()
    invalidate_directions_cache()
