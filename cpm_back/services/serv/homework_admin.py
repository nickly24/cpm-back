import datetime

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection


def _serialize_deadline(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _row_to_homework(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "deadline": _serialize_deadline(row.get("deadline")),
        "published": bool(row.get("published", 1)),
    }


def get_homework_by_id(homework_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, type, deadline, published FROM homework WHERE id = %s",
            (homework_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"status": False, "error": "Домашнее задание не найдено"}
        return {"status": True, "res": _row_to_homework(row)}
    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def update_homework(homework_id, fields):
    """Частичное обновление: name, type, deadline, published."""
    if not fields:
        return {"status": False, "error": "Нет полей для обновления"}

    allowed = {}
    if "name" in fields and fields["name"] is not None:
        name = str(fields["name"]).strip()
        if not name:
            return {"status": False, "error": "Название не может быть пустым"}
        allowed["name"] = name

    if "type" in fields and fields["type"] is not None:
        hw_type = str(fields["type"]).strip()
        if hw_type not in ("ДЗНВ", "ОВ"):
            return {"status": False, "error": "Тип должен быть ДЗНВ или ОВ"}
        allowed["type"] = hw_type

    if "deadline" in fields and fields["deadline"] is not None:
        try:
            allowed["deadline"] = datetime.datetime.strptime(
                str(fields["deadline"])[:10], "%Y-%m-%d"
            ).date()
        except ValueError:
            return {"status": False, "error": "Неверный формат даты (YYYY-MM-DD)"}

    if "published" in fields and fields["published"] is not None:
        allowed["published"] = 1 if fields["published"] else 0

    if not allowed:
        return {"status": False, "error": "Нет допустимых полей для обновления"}

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM homework WHERE id = %s", (homework_id,))
        if not cursor.fetchone():
            return {"status": False, "error": "Домашнее задание не найдено"}

        set_clause = ", ".join(f"{col} = %s" for col in allowed)
        params = list(allowed.values()) + [homework_id]
        cursor.execute(
            f"UPDATE homework SET {set_clause} WHERE id = %s",
            params,
        )
        connection.commit()
        return {"status": True, "homeworkId": homework_id}
    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def toggle_homework_published(homework_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, published FROM homework WHERE id = %s",
            (homework_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"status": False, "error": "Домашнее задание не найдено"}

        new_published = 0 if row.get("published", 1) else 1
        cursor.execute(
            "UPDATE homework SET published = %s WHERE id = %s",
            (new_published, homework_id),
        )
        connection.commit()
        return {
            "status": True,
            "published": bool(new_published),
            "message": (
                "Домашнее задание показано студентам"
                if new_published
                else "Домашнее задание скрыто от студентов"
            ),
        }
    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


def get_homework_overview(homework_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT id, name, type, deadline, published FROM homework WHERE id = %s",
            (homework_id,),
        )
        hw_row = cursor.fetchone()
        if not hw_row:
            return {"status": False, "error": "Домашнее задание не найдено"}

        stats_query = """
            SELECT
                COUNT(*) AS total_students,
                SUM(CASE WHEN hs.status = 1 THEN 1 ELSE 0 END) AS submitted,
                SUM(
                    CASE
                        WHEN hs.status = 0 AND h.deadline >= CURDATE() THEN 1
                        ELSE 0
                    END
                ) AS in_progress,
                SUM(
                    CASE
                        WHEN hs.status = 0 AND h.deadline < CURDATE() THEN 1
                        ELSE 0
                    END
                ) AS overdue,
                AVG(CASE WHEN hs.status = 1 THEN hs.result END) AS average_score
            FROM homework_sessions hs
            INNER JOIN homework h ON h.id = hs.homework_id
            WHERE hs.homework_id = %s
        """
        cursor.execute(stats_query, (homework_id,))
        stats = cursor.fetchone() or {}

        avg = stats.get("average_score")
        if avg is not None:
            avg = round(float(avg), 2)

        return {
            "status": True,
            "homework": _row_to_homework(hw_row),
            "analytics": {
                "totalStudents": int(stats.get("total_students") or 0),
                "submitted": int(stats.get("submitted") or 0),
                "inProgress": int(stats.get("in_progress") or 0),
                "overdue": int(stats.get("overdue") or 0),
                "averageScore": avg,
            },
        }
    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)
