from bson import ObjectId
from cpm_back.db.mongo import get_mongo_db
from cpm_back.services.exam.test_time import MOSCOW_TZ, now_moscow_iso, to_datetime


def _normalize_test_dates(test_data):
    for key in ("startDate", "endDate"):
        if key in test_data and test_data.get(key):
            parsed = to_datetime(test_data[key])
            if parsed:
                test_data[key] = parsed.astimezone(MOSCOW_TZ).isoformat()


def create_test(test_data):
    from cpm_back.services.exam.exam_memory_cache import invalidate_published_tests_cache

    db = get_mongo_db()
    tests_collection = db.tests
    _normalize_test_dates(test_data)
    test_data["createdAt"] = now_moscow_iso()
    if "visible" not in test_data:
        test_data["visible"] = False
    if "published" not in test_data:
        test_data["published"] = True
    result = tests_collection.insert_one(test_data)
    from cpm_back.services.exam.test_versions import create_test_version
    create_test_version(str(result.inserted_id), test_data)
    invalidate_published_tests_cache()
    return str(result.inserted_id)


def update_test(test_id, test_data):
    from cpm_back.services.exam.exam_memory_cache import (
        invalidate_published_tests_cache,
        invalidate_test_cache,
    )

    db = get_mongo_db()
    tests_collection = db.tests
    _normalize_test_dates(test_data)
    test_data["updatedAt"] = now_moscow_iso()
    result = tests_collection.update_one({"_id": ObjectId(test_id)}, {"$set": test_data})
    if result.modified_count > 0:
        from cpm_back.services.exam.test_versions import create_test_version
        current = tests_collection.find_one({"_id": ObjectId(test_id)})
        if current and any(key in test_data for key in ("title", "direction", "questions")):
            create_test_version(test_id, current)
        invalidate_test_cache(test_id)
        invalidate_published_tests_cache()
    return result.modified_count > 0


def delete_test(test_id):
    from cpm_back.services.exam.exam_memory_cache import (
        invalidate_published_tests_cache,
        invalidate_test_cache,
    )

    db = get_mongo_db()
    tests_collection = db.tests
    test_sessions_collection = db.test_sessions
    attempts_result = db.test_attempts.delete_many({"testId": str(test_id)})
    attempts_deleted = attempts_result.deleted_count
    sessions_result = test_sessions_collection.delete_many({"testId": test_id})
    sessions_deleted = sessions_result.deleted_count
    test_result = tests_collection.delete_one({"_id": ObjectId(test_id)})
    test_deleted = test_result.deleted_count
    if test_deleted > 0:
        invalidate_test_cache(test_id)
        invalidate_published_tests_cache()
    return {
        "test_deleted": test_deleted > 0,
        "sessions_deleted": sessions_deleted,
        "attempts_deleted": attempts_deleted,
        "total_deleted": test_deleted + sessions_deleted + attempts_deleted,
    }


def get_test_by_id(test_id):
    try:
        db = get_mongo_db()
        tests_collection = db.tests
        test = tests_collection.find_one({"_id": ObjectId(test_id)})
        if test:
            test["_id"] = str(test["_id"])
            return test
        return None
    except Exception:
        return None


def toggle_test_visibility(test_id):
    db = get_mongo_db()
    tests_collection = db.tests
    try:
        test = tests_collection.find_one({"_id": ObjectId(test_id)})
        if not test:
            return {"success": False, "error": "Test not found"}
        current_visible = test.get("visible", False)
        new_visible = not current_visible
        result = tests_collection.update_one(
            {"_id": ObjectId(test_id)},
            {"$set": {"visible": new_visible, "updatedAt": now_moscow_iso()}}
        )
        if result.modified_count > 0:
            from cpm_back.services.exam.exam_memory_cache import invalidate_published_tests_cache

            invalidate_published_tests_cache()
            return {"success": True, "visible": new_visible, "message": f"Видимость теста {'включена' if new_visible else 'выключена'}"}
        return {"success": False, "error": "Failed to update test visibility"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def toggle_test_published(test_id):
    db = get_mongo_db()
    tests_collection = db.tests
    try:
        test = tests_collection.find_one({"_id": ObjectId(test_id)})
        if not test:
            return {"success": False, "error": "Test not found"}
        current_published = test.get("published", True)
        new_published = not current_published
        result = tests_collection.update_one(
            {"_id": ObjectId(test_id)},
            {"$set": {"published": new_published, "updatedAt": now_moscow_iso()}},
        )
        if result.modified_count > 0:
            from cpm_back.services.exam.exam_memory_cache import (
                invalidate_published_tests_cache,
                invalidate_test_cache,
            )

            invalidate_test_cache(test_id)
            invalidate_published_tests_cache()
            return {
                "success": True,
                "published": new_published,
                "message": f"Тест {'показан' if new_published else 'скрыт'} для студентов",
            }
        return {"success": False, "error": "Failed to update test published state"}
    except Exception as e:
        return {"success": False, "error": str(e)}
