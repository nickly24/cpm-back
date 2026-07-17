#!/usr/bin/env python3
"""
Создаёт индексы MongoDB для модуля test_attempts (прод/любое окружение).

Использует cpm_back.config (MYSQL/MONGODB из config.py или env).

Запуск из cpm-back:
  python3 scripts/setup_test_attempts_indexes.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient, ASCENDING

from cpm_back.config import config


def main():
    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB_NAME]
    print(f"DB: {config.MONGODB_DB_NAME}")

    # --- test_attempts (новая коллекция) ---
    attempts = db.test_attempts
    before = list(attempts.list_indexes())
    print(f"test_attempts documents: {attempts.count_documents({})}")
    print(f"test_attempts indexes before: {[i['name'] for i in before]}")

    attempts.create_index(
        [("studentId", ASCENDING), ("testId", ASCENDING), ("status", ASCENDING)],
        name="student_test_status",
    )
    attempts.create_index(
        [("studentId", ASCENDING), ("status", ASCENDING), ("isPractice", ASCENDING), ("testId", ASCENDING)],
        name="student_attempt_catalog",
    )
    attempts.create_index(
        [("testId", ASCENDING), ("status", ASCENDING)],
        name="test_attempt_status",
    )
    try:
        attempts.create_index(
            [("studentId", ASCENDING), ("testId", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": "in_progress"},
            name="unique_in_progress_attempt",
        )
    except Exception as e:
        print(f"unique_in_progress_attempt: {e}")

    after = list(attempts.list_indexes())
    print(f"test_attempts indexes after: {[i['name'] for i in after]}")

    # --- test_sessions (финальные сдачи) ---
    sessions = db.test_sessions
    print(f"test_sessions documents: {sessions.count_documents({})}")
    print(f"test_sessions indexes before: {[i['name'] for i in sessions.list_indexes()]}")

    try:
        sessions.create_index(
            [("studentId", ASCENDING), ("testId", ASCENDING)],
            unique=True,
            name="unique_student_test",
        )
    except Exception as e:
        print(f"unique_student_test: {e}")
    try:
        sessions.create_index(
            [("attemptId", ASCENDING)],
            unique=True,
            partialFilterExpression={"attemptId": {"$exists": True}},
            name="unique_attempt_session",
        )
    except Exception as e:
        print(f"unique_attempt_session: {e}")

    db.tests.create_index(
        [("published", ASCENDING), ("startDate", ASCENDING), ("endDate", ASCENDING)],
        name="published_test_window",
    )
    db.test_versions.create_index(
        [("testId", ASCENDING), ("definitionHash", ASCENDING)],
        unique=True,
        name="unique_test_definition_version",
    )

    print(f"test_sessions indexes after: {[i['name'] for i in sessions.list_indexes()]}")

    # Справка по коллекциям
    print("Collections:", db.list_collection_names())
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
