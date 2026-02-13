from datetime import datetime
from bson import ObjectId
from cpm_back.db.mongo import get_mongo_db


def create_test(test_data):
    db = get_mongo_db()
    tests_collection = db.tests
    test_data["createdAt"] = datetime.utcnow().isoformat() + "Z"
    if "visible" not in test_data:
        test_data["visible"] = False
    result = tests_collection.insert_one(test_data)
    return str(result.inserted_id)


def update_test(test_id, test_data):
    db = get_mongo_db()
    tests_collection = db.tests
    test_data["updatedAt"] = datetime.utcnow().isoformat() + "Z"
    result = tests_collection.update_one({"_id": ObjectId(test_id)}, {"$set": test_data})
    return result.modified_count > 0


def delete_test(test_id):
    db = get_mongo_db()
    tests_collection = db.tests
    test_sessions_collection = db.test_sessions
    sessions_result = test_sessions_collection.delete_many({"testId": test_id})
    sessions_deleted = sessions_result.deleted_count
    test_result = tests_collection.delete_one({"_id": ObjectId(test_id)})
    test_deleted = test_result.deleted_count
    return {"test_deleted": test_deleted > 0, "sessions_deleted": sessions_deleted, "total_deleted": test_deleted + sessions_deleted}


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
            {"$set": {"visible": new_visible, "updatedAt": datetime.utcnow().isoformat() + "Z"}}
        )
        if result.modified_count > 0:
            return {"success": True, "visible": new_visible, "message": f"Видимость теста {'включена' if new_visible else 'выключена'}"}
        return {"success": False, "error": "Failed to update test visibility"}
    except Exception as e:
        return {"success": False, "error": str(e)}
