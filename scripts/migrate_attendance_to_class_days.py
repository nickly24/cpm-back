#!/usr/bin/env python3
"""
Миграция посещаемости из старой таблицы attendance в новую модель:
class_days + class_day_attendance.

Старая таблица attendance:
  - date, student_id, attendance_rate (1 = очное, 2 = уважительная), zap_id (опционально)

Алгоритм:
  1. Читаем все записи из attendance.
  2. Группируем по date.
  3. Для каждой даты создаём день занятий (class_days), если его ещё нет.
  4. Для каждой старой записи вставляем строку в class_day_attendance с маппингом:
     attendance_rate 1 -> attendance_type_id 1 (очное присутствие)
     attendance_rate 2 -> attendance_type_id 2 (отсутствие по уважительной причине)
     иное/NULL -> 1

Запуск (из директории cpm-back, чтобы подхватить cpm_back и config):
  cd /path/to/cpm-back
  python scripts/migrate_attendance_to_class_days.py

Учётные данные БД берутся из cpm_back.config (переменные окружения MYSQL_HOST, MYSQL_USER, ...).
"""
import sys
import os

# Чтобы импортировать cpm_back при запуске из cpm-back/scripts/
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACK_ROOT = os.path.dirname(_SCRIPT_DIR)
if _BACK_ROOT not in sys.path:
    sys.path.insert(0, _BACK_ROOT)

import mysql.connector
from cpm_back.config import config


def get_old_attendance(conn):
    """Возвращает список (date, student_id, attendance_rate, zap_id) из таблицы attendance."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, student_id, attendance_rate, zap_id
        FROM attendance
        ORDER BY date, student_id
    """)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def ensure_class_days_for_dates(conn, dates, created_count):
    """
    По списку уникальных дат создаёт недостающие записи в class_days.
    Возвращает словарь date -> class_day_id.
    """
    if not dates:
        return {}
    cursor = conn.cursor()
    date_to_id = {}
    for date_obj in sorted(set(dates)):
        cursor.execute("SELECT id FROM class_days WHERE date = %s", (date_obj,))
        row = cursor.fetchone()
        if row:
            date_to_id[date_obj] = row[0]
        else:
            cursor.execute("INSERT INTO class_days (date) VALUES (%s)", (date_obj,))
            date_to_id[date_obj] = cursor.lastrowid
            created_count[0] += 1
    conn.commit()
    cursor.close()
    return date_to_id


def get_valid_zap_ids(conn):
    """Возвращает множество id, существующих в таблице zaps (для проверки FK)."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM zaps")
    ids = {row[0] for row in cursor.fetchall()}
    cursor.close()
    return ids


def map_attendance_rate_to_type_id(attendance_rate):
    """Старая attendance_rate: 1 = очное, 2 = уважительная. Остальное -> 1."""
    if attendance_rate == 2:
        return 2  # absent_valid
    return 1  # in_person (включая rate is None или иное значение)


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
        rows = get_old_attendance(conn)
        print(f"Прочитано записей из attendance: {len(rows)}")

        if not rows:
            print("Нет данных для миграции.")
            return

        dates = [r[0] for r in rows]
        created_days = [0]
        date_to_class_day_id = ensure_class_days_for_dates(conn, dates, created_days)
        print(f"Уникальных дат: {len(date_to_class_day_id)}, создано новых дней занятий: {created_days[0]}")

        valid_zap_ids = get_valid_zap_ids(conn)
        print(f"В таблице zaps найдено id: {len(valid_zap_ids)}")

        BATCH_SIZE = 10
        processed = 0
        errors = []
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO class_day_attendance
                (class_day_id, student_id, attendance_type_id, zap_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                attendance_type_id = VALUES(attendance_type_id),
                zap_id = VALUES(zap_id)
        """
        batch = []
        for date_val, student_id, attendance_rate, zap_id in rows:
            class_day_id = date_to_class_day_id.get(date_val)
            if not class_day_id:
                errors.append((date_val, student_id, "нет class_day_id для даты"))
                continue
            type_id = map_attendance_rate_to_type_id(attendance_rate)
            batch.append((class_day_id, student_id, type_id, zap_id))
            if len(batch) >= BATCH_SIZE:
                try:
                    cursor.executemany(insert_sql, batch)
                    conn.commit()
                    processed += len(batch)
                    if processed % 500 == 0:
                        print(f"  ... обработано {processed} записей")
                except Exception as e:
                    conn.rollback()
                    for t in batch:
                        errors.append((None, t[1], str(e)))
                batch = []

        if batch:
            try:
                cursor.executemany(insert_sql, batch)
                conn.commit()
                processed += len(batch)
            except Exception as e:
                conn.rollback()
                for t in batch:
                    errors.append((None, t[1], str(e)))

        cursor.close()

        print(f"Создано дней занятий (class_days): {created_days[0]}")
        print(f"Обработано записей посещаемости (пакетами по {BATCH_SIZE}): {processed}")
        if errors:
            print(f"Ошибки ({len(errors)}):")
            for date_val, student_id, msg in errors[:10]:
                print(f"  {date_val} student_id={student_id}: {msg}")
            if len(errors) > 10:
                print(f"  ... и ещё {len(errors) - 10}")
    finally:
        conn.close()

    print("Готово.")


if __name__ == "__main__":
    main()
