#!/usr/bin/env python3
"""
Удаляет legacy-документы расписания без поля date (недельный шаблон day_of_week).

Новая модель: одно занятие = одна календарная дата (YYYY-MM-DD).
Документы со старым day_of_week несовместимы и должны быть удалены перед
использованием календарного API.

По умолчанию — dry-run (только показывает, что будет удалено).
Для реального удаления:
  python3 scripts/wipe_legacy_schedule.py --apply

Запуск из cpm-back-main:
  python3 scripts/wipe_legacy_schedule.py
  python3 scripts/wipe_legacy_schedule.py --apply
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient

from cpm_back.config import config


def main():
    parser = argparse.ArgumentParser(description="Wipe legacy schedule documents without date")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete documents (default is dry-run)",
    )
    args = parser.parse_args()

    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB_NAME]
    schedule = db.schedule

    # Legacy: нет валидного date (недельный шаблон на day_of_week).
    # Документы с заполненным date не трогаем.
    legacy_query = {
        "$or": [
            {"date": {"$exists": False}},
            {"date": None},
            {"date": ""},
        ]
    }

    total = schedule.count_documents({})
    legacy_count = schedule.count_documents(legacy_query)

    print(f"DB: {config.MONGODB_DB_NAME}")
    print(f"schedule total documents: {total}")
    print(f"legacy documents to remove: {legacy_count}")

    if legacy_count == 0:
        print("Nothing to wipe.")
        client.close()
        return

    sample = list(schedule.find(legacy_query).limit(5))
    for doc in sample:
        print(
            "  sample:",
            {
                "_id": str(doc.get("_id")),
                "day_of_week": doc.get("day_of_week"),
                "date": doc.get("date"),
                "lesson_name": doc.get("lesson_name"),
            },
        )

    if not args.apply:
        print("Dry-run only. Re-run with --apply to delete.")
        client.close()
        return

    result = schedule.delete_many(legacy_query)
    print(f"Deleted: {result.deleted_count}")
    print(f"schedule remaining: {schedule.count_documents({})}")
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
