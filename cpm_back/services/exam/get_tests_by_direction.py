from bson import ObjectId
from cpm_back.db.mongo import get_mongo_db


def get_tests_by_direction(direction_name):
    """Все тесты по направлению без пагинации (обратная совместимость)."""
    items, _ = get_tests_by_direction_paginated(direction_name, page=1, limit=5000)
    return items


def get_tests_by_direction_paginated(direction_name, page=1, limit=20):
    db = get_mongo_db()
    tests_collection = db.tests
    filter_query = {"direction": direction_name}
    projection = {"_id": 1, "title": 1, "startDate": 1, "endDate": 1, "timeLimitMinutes": 1, "visible": 1}
    total = tests_collection.count_documents(filter_query)
    skip = (page - 1) * limit
    cursor = tests_collection.find(filter_query, projection).sort("startDate", -1).skip(skip).limit(limit)
    result = []
    for test in cursor:
        result.append({
            "id": str(test["_id"]),
            "title": test["title"],
            "startDate": test["startDate"],
            "endDate": test["endDate"],
            "timeLimitMinutes": test["timeLimitMinutes"],
            "visible": test.get("visible", False),
        })
    return result, total


def get_test_by_id(test_id):
    db = get_mongo_db()
    tests_collection = db.tests
    test = tests_collection.find_one({"_id": ObjectId(test_id)})
    if test:
        test["_id"] = str(test["_id"])
        return test
    return None
