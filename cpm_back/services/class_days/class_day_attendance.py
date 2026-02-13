"""
Посещаемость по дням занятий (таблица class_day_attendance).
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def set_attendance(class_day_id, student_id, attendance_type_id, zap_id=None):
    """
    Добавить или обновить запись посещаемости для студента в день занятий.
    Один студент — одна запись на день (unique class_day_id + student_id).
    attendance_type_id: 1–8 (справочник attendance_types).
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT id FROM attendance_types WHERE id = %s",
            (attendance_type_id,),
        )
        if not cursor.fetchone():
            return {"status": False, "error": "Недопустимый тип посещения"}

        cursor.execute(
            "SELECT id FROM class_days WHERE id = %s",
            (class_day_id,),
        )
        if not cursor.fetchone():
            return {"status": False, "error": "День занятий не найден"}

        cursor.execute(
            "SELECT id FROM students WHERE id = %s",
            (student_id,),
        )
        if not cursor.fetchone():
            return {"status": False, "error": "Студент не найден"}

        cursor.execute(
            """
            INSERT INTO class_day_attendance (class_day_id, student_id, attendance_type_id, zap_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE attendance_type_id = VALUES(attendance_type_id), zap_id = VALUES(zap_id)
            """,
            (class_day_id, student_id, attendance_type_id, zap_id),
        )
        connection.commit()
        return {"status": True}
    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def get_attendance_by_class_day(class_day_id):
    """
    Список посещаемостей по дню занятий: студент + тип посещения.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT a.id, a.student_id, a.attendance_type_id, a.zap_id, a.created_at,
                   s.full_name,
                   t.code AS type_code, t.name_ru AS type_name
            FROM class_day_attendance a
            JOIN students s ON s.id = a.student_id
            JOIN attendance_types t ON t.id = a.attendance_type_id
            WHERE a.class_day_id = %s
            ORDER BY s.full_name
            """,
            (class_day_id,),
        )
        rows = cursor.fetchall()
        items = [
            {
                "id": r["id"],
                "student_id": r["student_id"],
                "full_name": r["full_name"],
                "attendance_type_id": r["attendance_type_id"],
                "type_code": r["type_code"],
                "type_name": r["type_name"],
                "zap_id": r["zap_id"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
        return {"status": True, "attendance": items}
    except Exception as err:
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def get_student_class_day_attendance(student_id, date_from=None, date_to=None):
    """
    Посещаемость студента по дням занятий за период (для личного кабинета / супервизора).
    date_from, date_to: YYYY-MM-DD.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        if date_from and date_to:
            cursor.execute(
                """
                SELECT cd.id AS class_day_id, cd.date, cd.comment,
                       a.attendance_type_id, a.zap_id,
                       t.code AS type_code, t.name_ru AS type_name
                FROM class_day_attendance a
                JOIN class_days cd ON cd.id = a.class_day_id
                JOIN attendance_types t ON t.id = a.attendance_type_id
                WHERE a.student_id = %s AND cd.date BETWEEN %s AND %s
                ORDER BY cd.date DESC
                """,
                (student_id, date_from, date_to),
            )
        elif date_from:
            cursor.execute(
                """
                SELECT cd.id AS class_day_id, cd.date, cd.comment,
                       a.attendance_type_id, a.zap_id,
                       t.code AS type_code, t.name_ru AS type_name
                FROM class_day_attendance a
                JOIN class_days cd ON cd.id = a.class_day_id
                JOIN attendance_types t ON t.id = a.attendance_type_id
                WHERE a.student_id = %s AND cd.date >= %s
                ORDER BY cd.date DESC
                """,
                (student_id, date_from),
            )
        elif date_to:
            cursor.execute(
                """
                SELECT cd.id AS class_day_id, cd.date, cd.comment,
                       a.attendance_type_id, a.zap_id,
                       t.code AS type_code, t.name_ru AS type_name
                FROM class_day_attendance a
                JOIN class_days cd ON cd.id = a.class_day_id
                JOIN attendance_types t ON t.id = a.attendance_type_id
                WHERE a.student_id = %s AND cd.date <= %s
                ORDER BY cd.date DESC
                """,
                (student_id, date_to),
            )
        else:
            cursor.execute(
                """
                SELECT cd.id AS class_day_id, cd.date, cd.comment,
                       a.attendance_type_id, a.zap_id,
                       t.code AS type_code, t.name_ru AS type_name
                FROM class_day_attendance a
                JOIN class_days cd ON cd.id = a.class_day_id
                JOIN attendance_types t ON t.id = a.attendance_type_id
                WHERE a.student_id = %s
                ORDER BY cd.date DESC
                LIMIT 200
                """,
                (student_id,),
            )
        rows = cursor.fetchall()
        items = [
            {
                "class_day_id": r["class_day_id"],
                "date": r["date"].isoformat(),
                "comment": r.get("comment"),
                "attendance_type_id": r["attendance_type_id"],
                "type_code": r["type_code"],
                "type_name": r["type_name"],
                "zap_id": r["zap_id"],
            }
            for r in rows
        ]
        return {"status": True, "attendance": items}
    except Exception as err:
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)
