#!/usr/bin/env python3
"""
Проверка и очистка legacy-данных тренировок перед миграцией 013.

Запуск из cpm-back-main:
  python3 scripts/wipe_training_legacy_data.py
  python3 scripts/wipe_training_legacy_data.py --apply
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parents[1]

_config_path = ROOT / "cpm_back" / "config.py"
_spec = importlib.util.spec_from_file_location("cpm_config", _config_path)
_config_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_config_mod)
config = _config_mod.config

TABLES = (
    "student_progress",
    "student_card_progress",
    "student_section_study_settings",
    "cards",
    "card_themes",
    "training_sections",
)


def table_exists(cursor, table):
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table,),
    )
    return cursor.fetchone()[0] > 0


def count_rows(cursor, table):
    if not table_exists(cursor, table):
        return None
    cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
    return cursor.fetchone()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Выполнить DELETE (без флага — только отчёт COUNT)",
    )
    args = parser.parse_args()

    print(
        f"Подключение: {config.MYSQL_HOST}:{config.MYSQL_PORT}/"
        f"{config.MYSQL_DATABASE} ({config.MYSQL_USER})"
    )
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=False,
    )
    cursor = conn.cursor()

    try:
        print("\n--- Состояние таблиц ---")
        for table in TABLES:
            count = count_rows(cursor, table)
            if count is None:
                print(f"  {table}: (нет таблицы)")
            else:
                print(f"  {table}: {count} строк")

        if not args.apply:
            print("\nDry-run. Для очистки: python3 scripts/wipe_training_legacy_data.py --apply")
            return

        print("\n--- Очистка ---")
        for table in TABLES:
            if not table_exists(cursor, table):
                print(f"  skip {table}")
                continue
            cursor.execute(f"DELETE FROM `{table}`")
            print(f"  deleted {table}: {cursor.rowcount} строк")

        conn.commit()
        print("\nОчистка завершена.")
    except Exception as exc:
        conn.rollback()
        print(f"Ошибка: {exc}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
