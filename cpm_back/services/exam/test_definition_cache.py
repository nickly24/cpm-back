"""In-memory cache определения теста (Mongo tests) для пика start."""
import time

from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db

_CACHE = {}
_TTL_SECONDS = 600


def get_test_document_cached(test_id):
    key = str(test_id)
    entry = _CACHE.get(key)
    now = time.time()
    if entry and (now - entry["at"]) < _TTL_SECONDS:
        return entry["doc"]

    try:
        doc = get_mongo_db().tests.find_one({"_id": ObjectId(test_id)})
    except Exception:
        return None

    if doc:
        _CACHE[key] = {"doc": doc, "at": now}
    return doc


def invalidate_test_cache(test_id):
    _CACHE.pop(str(test_id), None)
