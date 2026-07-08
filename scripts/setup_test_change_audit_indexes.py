#!/usr/bin/env python3
"""
Создаёт индексы MongoDB для аудита изменений тестов.

Коллекция: test_question_change_log

Запуск из cpm-back-main:
  python3 scripts/setup_test_change_audit_indexes.py
"""
import sys
import importlib.util
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_config():
    config_path = ROOT / "cpm_back" / "config.py"
    spec = importlib.util.spec_from_file_location("cpm_back_config", config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.config


def main():
    config = _load_config()
    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB_NAME]
    print(f"DB: {config.MONGODB_DB_NAME}")

    coll = db.test_question_change_log
    before = list(coll.list_indexes())
    print(f"test_question_change_log documents: {coll.count_documents({})}")
    print(f"test_question_change_log indexes before: {[i['name'] for i in before]}")

    coll.create_index(
        [("testId", ASCENDING), ("questionId", ASCENDING), ("changedAt", DESCENDING)],
        name="test_question_changedAt",
    )
    coll.create_index(
        [("testId", ASCENDING), ("changedAt", DESCENDING)],
        name="test_changedAt",
    )
    coll.create_index(
        [("changeKey", ASCENDING), ("revision", DESCENDING)],
        name="changeKey_revision",
    )
    coll.create_index(
        [("eventType", ASCENDING), ("changedAt", DESCENDING)],
        name="event_changedAt",
    )

    after = list(coll.list_indexes())
    print(f"test_question_change_log indexes after: {[i['name'] for i in after]}")
    print("Collections:", db.list_collection_names())
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
