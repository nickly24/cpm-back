#!/usr/bin/env python3
"""
Миграция MongoDB: поля is_in_person и is_for_all в коллекции schedule.

- is_in_person: true = очно, false = дистанционно (default true)
- is_for_all: true = все студенты, false = частично (default true)

Идемпотентно: выставляет дефолты только если поля отсутствуют.

Запуск из cpm-back-main:
  python3 scripts/migrate_schedule_attendance_flags.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient

from cpm_back.config import config


def main():
    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB_NAME]
    schedule = db.schedule

    total = schedule.count_documents({})
    missing_in_person = schedule.count_documents({"is_in_person": {"$exists": False}})
    missing_for_all = schedule.count_documents({"is_for_all": {"$exists": False}})

    print(f"DB: {config.MONGODB_DB_NAME}")
    print(f"schedule documents: {total}")
    print(f"missing is_in_person: {missing_in_person}")
    print(f"missing is_for_all: {missing_for_all}")

    r1 = schedule.update_many(
        {"is_in_person": {"$exists": False}},
        {"$set": {"is_in_person": True}},
    )
    r2 = schedule.update_many(
        {"is_for_all": {"$exists": False}},
        {"$set": {"is_for_all": True}},
    )

    print(f"set is_in_person=true: matched={r1.matched_count}, modified={r1.modified_count}")
    print(f"set is_for_all=true: matched={r2.matched_count}, modified={r2.modified_count}")
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
