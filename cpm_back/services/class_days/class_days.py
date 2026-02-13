"""
Дни занятий (таблица class_days): создание, список за период, удаление.
"""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def create_class_day(date_str, comment=None):
    """
    Создаёт день занятий (лист посещаемости) на указанную дату.
    date_str: YYYY-MM-DD
    Возвращает status, id созданной записи или error.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO class_days (date, comment) VALUES (%s, %s)",
            (date_str, comment or None),
        )
        connection.commit()
        day_id = cursor.lastrowid
        return {"status": True, "id": day_id}
    except Exception as err:
        if connection:
            connection.rollback()
        err_msg = str(err)
        if "Duplicate entry" in err_msg or "uq_class_days_date" in err_msg:
            return {"status": False, "error": "День занятий на эту дату уже существует"}
        return {"status": False, "error": err_msg}
    finally:
        if connection:
            close_db_connection(connection)


def list_class_days(date_from=None, date_to=None):
    """
    Список дней занятий за период.
    date_from, date_to: YYYY-MM-DD (включительно).
    Если не заданы — возвращаются все дни (или ограничить лимитом по желанию).
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        if date_from and date_to:
            cursor.execute(
                """
                SELECT id, date, comment, created_at
                FROM class_days
                WHERE date BETWEEN %s AND %s
                ORDER BY date DESC
                """,
                (date_from, date_to),
            )
        elif date_from:
            cursor.execute(
                """
                SELECT id, date, comment, created_at
                FROM class_days
                WHERE date >= %s
                ORDER BY date DESC
                """,
                (date_from,),
            )
        elif date_to:
            cursor.execute(
                """
                SELECT id, date, comment, created_at
                FROM class_days
                WHERE date <= %s
                ORDER BY date DESC
                """,
                (date_to,),
            )
        else:
            cursor.execute(
                """
                SELECT id, date, comment, created_at
                FROM class_days
                ORDER BY date DESC
                LIMIT 500
                """
            )
        rows = cursor.fetchall()
        days = [
            {
                "id": r["id"],
                "date": r["date"].isoformat() if r["date"] else None,
                "comment": r["comment"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
        return {"status": True, "class_days": days}
    except Exception as err:
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def get_class_day(class_day_id):
    """
    Один день занятий по id (без списка посещаемостей — это отдельный эндпоинт).
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, date, comment, created_at FROM class_days WHERE id = %s",
            (class_day_id,),
        )
        r = cursor.fetchone()
        if not r:
            return {"status": False, "error": "День занятий не найден"}
        return {
            "status": True,
            "class_day": {
                "id": r["id"],
                "date": r["date"].isoformat(),
                "comment": r["comment"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            },
        }
    except Exception as err:
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def delete_class_day(class_day_id):
    """
    Удаляет день занятий. Каскадно удаляются записи в class_day_attendance.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM class_days WHERE id = %s", (class_day_id,))
        connection.commit()
        if cursor.rowcount == 0:
            return {"status": False, "error": "День занятий не найден"}
        return {"status": True}
    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)
