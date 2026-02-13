#!/usr/bin/env python3
"""
Обработка только тех записей из attendance, у которых zap_id указывает на несуществующий отгул.
Переносит их в class_day_attendance с zap_id=NULL (без привязки к отгулу).

Запуск из cpm-back:
  python scripts/migrate_attendance_orphan_zap_only.py
"""
import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACK_ROOT = os.path.dirname(_SCRIPT_DIR)
if _BACK_ROOT not in sys.path:
    sys.path.insert(0, _BACK_ROOT)

import mysql.connector
from cpm_back.config import config


def main():
    print("Подключение к БД...")
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=False,
    )

    try:
        cursor = conn.cursor()
        # Записи attendance, у которых zap_id не существует в zaps
        cursor.execute("""
            SELECT a.date, a.student_id, a.attendance_rate, a.zap_id
            FROM attendance a
            LEFT JOIN zaps z ON z.id = a.zap_id
            WHERE a.zap_id IS NOT NULL AND z.id IS NULL
            ORDER BY a.date, a.student_id
        """)
        rows = cursor.fetchall()
        print(f"Найдено записей с «битым» zap_id: {len(rows)}")

        if not rows:
            print("Нечего обрабатывать.")
            cursor.close()
            return

        # type: 1 = очное, 2 = уважительная
        def type_id(rate):
            return 2 if rate == 2 else 1

        insert_sql = """
            INSERT INTO class_day_attendance
                (class_day_id, student_id, attendance_type_id, zap_id)
            VALUES (%s, %s, %s, NULL)
            ON DUPLICATE KEY UPDATE
                attendance_type_id = VALUES(attendance_type_id),
                zap_id = NULL
        """
        ok = 0
        err = []
        for (date_val, student_id, attendance_rate, zap_id) in rows:
            cursor.execute("SELECT id FROM class_days WHERE date = %s", (date_val,))
            row = cursor.fetchone()
            if not row:
                err.append((date_val, student_id, "нет дня занятий на эту дату"))
                continue
            class_day_id = row[0]
            try:
                cursor.execute(insert_sql, (class_day_id, student_id, type_id(attendance_rate)))
                ok += 1
            except Exception as e:
                err.append((date_val, student_id, str(e)))
        conn.commit()
        cursor.close()

        print(f"Обработано успешно: {ok}")
        if err:
            print(f"Ошибки ({len(err)}):")
            for date_val, student_id, msg in err:
                print(f"  {date_val} student_id={student_id}: {msg}")
    finally:
        conn.close()
    print("Готово.")


if __name__ == "__main__":
    main()
