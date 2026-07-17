"""Immutable definitions used by attempts, scoring and review."""
import copy
import hashlib
import json

from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.test_time import now_moscow_iso

_version_cache = {}
_sanitized_version_cache = {}


def _definition(test):
    questions = copy.deepcopy(test.get("questions") or [])
    for question in questions:
        for option in question.get("answers") or []:
            if option.get("id") is not None:
                option["id"] = str(option["id"])
    return {
        "title": test.get("title") or "",
        "direction": test.get("direction") or "",
        "questions": questions,
    }


def _hash(definition):
    raw = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_test_version(test):
    """Return current version, lazily backfilling legacy tests."""
    if not test:
        return None
    db = get_mongo_db()
    current = test.get("currentVersionId")
    if current:
        try:
            found = db.test_versions.find_one({"_id": ObjectId(str(current))})
            if found and found.get("definitionHash") == _hash(_definition(test)):
                return found
        except Exception:
            pass

    definition = _definition(test)
    definition_hash = _hash(definition)
    test_id = str(test.get("_id") or test.get("id"))
    existing = db.test_versions.find_one({"testId": test_id, "definitionHash": definition_hash})
    if existing:
        version_id = existing["_id"]
    else:
        doc = {
            "testId": test_id,
            **definition,
            "definitionHash": definition_hash,
            "createdAtMoscow": now_moscow_iso(),
        }
        version_id = db.test_versions.insert_one(doc).inserted_id
        doc["_id"] = version_id
        existing = doc
    try:
        db.tests.update_one({"_id": ObjectId(test_id)}, {"$set": {"currentVersionId": str(version_id)}})
    except Exception:
        pass
    return existing


def create_test_version(test_id, test):
    db = get_mongo_db()
    definition = _definition(test)
    doc = {
        "testId": str(test_id),
        **definition,
        "definitionHash": _hash(definition),
        "createdAtMoscow": now_moscow_iso(),
    }
    existing = db.test_versions.find_one({
        "testId": str(test_id),
        "definitionHash": doc["definitionHash"],
    })
    version_id = existing["_id"] if existing else db.test_versions.insert_one(doc).inserted_id
    db.tests.update_one(
        {"_id": ObjectId(str(test_id))},
        {"$set": {"currentVersionId": str(version_id)}},
    )
    return str(version_id)


def get_test_version(version_id):
    key = str(version_id or "")
    if key in _version_cache:
        return copy.deepcopy(_version_cache[key])
    try:
        found = get_mongo_db().test_versions.find_one({"_id": ObjectId(key)})
        if found:
            _version_cache[key] = copy.deepcopy(found)
        return found
    except Exception:
        return None


def get_sanitized_test_version_map(version_id):
    key = str(version_id or "")
    if key in _sanitized_version_cache:
        return copy.deepcopy(_sanitized_version_cache[key])
    version = get_test_version(key)
    if not version:
        return {}
    from cpm_back.services.exam.test_sanitize import sanitize_question
    result = {
        question.get("questionId"): sanitize_question(question)
        for question in (version.get("questions") or [])
        if question.get("questionId") is not None
    }
    _sanitized_version_cache[key] = copy.deepcopy(result)
    return result
