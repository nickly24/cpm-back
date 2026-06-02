"""Правила показа правильных ответов после сдачи (поле tests.visible)."""
from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db


def get_test_visible(test_id):
    db = get_mongo_db()
    try:
        test = db.tests.find_one({"_id": ObjectId(test_id)}, {"visible": 1})
    except Exception:
        return False
    if not test:
        return False
    return bool(test.get("visible", False))


def can_show_correct_answers(role, test_id):
    if role == "admin":
        return True
    if role != "student":
        return False
    return get_test_visible(test_id)
