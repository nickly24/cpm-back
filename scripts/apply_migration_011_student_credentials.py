#!/usr/bin/env python3
"""
Миграция 011: таблица открытых логинов/паролей учеников для админки.

Запуск из cpm-back:
  python3 scripts/apply_migration_011_student_credentials.py
"""
from __future__ import annotations

import sys
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mysql.connector

config = runpy.run_path(str(ROOT / "cpm_back" / "config.py"))["config"]


def main() -> int:
    print(
        f"Подключение: {config.MYSQL_HOST}:{config.MYSQL_PORT}/{config.MYSQL_DATABASE} "
        f"({config.MYSQL_USER})"
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
        migration_sql = (ROOT / "migrations" / "011_student_credentials.sql").read_text()
        for statement in [part.strip() for part in migration_sql.split(";") if part.strip()]:
            cursor.execute(statement)
        conn.commit()
        print("Таблица student_credentials готова.")
        return 0
    except Exception as err:
        conn.rollback()
        print(f"Ошибка: {err}")
        return 1
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
