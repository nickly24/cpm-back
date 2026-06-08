#!/usr/bin/env python3
"""
Миграция 010: расширить auth_users.password и пересоздать обрезанные хеши.

Запуск из cpm-back:
  python3 scripts/apply_migration_010_auth_password.py
"""
from __future__ import annotations

import random
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash
from cpm_back.config import config


def _is_truncated_hash(password: str | None) -> bool:
    if not password:
        return False
    return len(password) == 50 and password.startswith(("scrypt:", "pbkdf2:", "argon2:"))


def _generate_password() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


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
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SHOW COLUMNS FROM auth_users LIKE 'password'")
        column = cursor.fetchone()
        col_type = (column or {}).get("Type", "")
        print(f"Текущий тип password: {col_type}")

        if "255" not in col_type:
            print("Применяем ALTER TABLE auth_users.password -> VARCHAR(255)…")
            cursor.execute(
                "ALTER TABLE auth_users MODIFY COLUMN password VARCHAR(255) NULL"
            )
            conn.commit()
            print("Колонка расширена.")
        else:
            print("Колонка уже VARCHAR(255) — пропуск ALTER.")

        cursor.execute(
            "SELECT username, password, role, ref_id FROM auth_users ORDER BY role, username"
        )
        rows = cursor.fetchall()
        fixed = []

        for row in rows:
            password = row.get("password") or ""
            if not _is_truncated_hash(password):
                continue

            new_password = _generate_password()
            new_hash = generate_password_hash(new_password)
            cursor.execute(
                "UPDATE auth_users SET password = %s WHERE username = %s",
                (new_hash, row["username"]),
            )
            fixed.append(
                {
                    "username": row["username"],
                    "role": row["role"],
                    "ref_id": row["ref_id"],
                    "password": new_password,
                    "hash_len": len(new_hash),
                }
            )

        if fixed:
            conn.commit()
            print(f"\nПересоздано паролей для {len(fixed)} учёток с обрезанным хешем:")
            for item in fixed:
                print(
                    f"  [{item['role']}] {item['username']} (ref {item['ref_id']}) "
                    f"-> {item['password']} (hash len {item['hash_len']})"
                )
        else:
            print("\nОбрезанных хешей не найдено — сброс паролей не требуется.")

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
