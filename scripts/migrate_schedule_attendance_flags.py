#!/usr/bin/env python3
"""
Миграция MongoDB: поля is_in_person и is_for_all в коллекции schedule.

- is_in_person: true = очно, false = дистанционно (default true)
- is_for_all: true = все студенты, false = частично (default true)

Идемпотентно: выставляет дефолты только если поля отсутствуют.

Запуск из cpm-back-main:
  python3 scripts/migrate_schedule_attendance_flags.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient


def _load_config():
    """Load config.py without importing cpm_back package (avoids py3.10+ syntax in deps)."""
    config_path = ROOT / "cpm_back" / "config.py"
    spec = importlib.util.spec_from_file_location("cpm_back_config_standalone", config_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.config


def main():
    config = _load_config()
    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB_NAME]
    schedule = db.schedule

    total = schedule.count_documents({})
    missing_in_person = schedule.count_documents({"is_in_person": {"$exists": False}})
    missing_for_all = schedule.count_documents({"is_for_all": {"$exists": False}})

    print("DB: {}".format(config.MONGODB_DB_NAME))
    print("schedule documents: {}".format(total))
    print("missing is_in_person: {}".format(missing_in_person))
    print("missing is_for_all: {}".format(missing_for_all))

    r1 = schedule.update_many(
        {"is_in_person": {"$exists": False}},
        {"$set": {"is_in_person": True}},
    )
    r2 = schedule.update_many(
        {"is_for_all": {"$exists": False}},
        {"$set": {"is_for_all": True}},
    )

    print(
        "set is_in_person=true: matched={}, modified={}".format(
            r1.matched_count, r1.modified_count
        )
    )
    print(
        "set is_for_all=true: matched={}, modified={}".format(
            r2.matched_count, r2.modified_count
        )
    )
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
