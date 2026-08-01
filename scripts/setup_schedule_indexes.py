#!/usr/bin/env python3
"""
Создаёт индексы MongoDB для календарного расписания (коллекция schedule).

Использует cpm_back.config (MONGODB из config.py или env).

Запуск из cpm-back-main:
  python3 scripts/setup_schedule_indexes.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymongo import ASCENDING, MongoClient

from cpm_back.config import config


def main():
    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB_NAME]
    schedule = db.schedule

    print(f"DB: {config.MONGODB_DB_NAME}")
    print(f"schedule documents: {schedule.count_documents({})}")
    print(f"indexes before: {[i['name'] for i in schedule.list_indexes()]}")

    schedule.create_index([("date", ASCENDING)], name="schedule_date")
    schedule.create_index(
        [("date", ASCENDING), ("is_public", ASCENDING), ("school_id", ASCENDING)],
        name="schedule_date_visibility",
    )

    print(f"indexes after: {[i['name'] for i in schedule.list_indexes()]}")
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
