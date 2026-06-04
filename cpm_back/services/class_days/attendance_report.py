"""
Журнал посещаемости за период (админ): дни занятий, студенты, записи посещаемости.
"""
import re
from datetime import datetime

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.serv.school_schema import is_schools_schema_ready

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_PERIOD_DAYS = 366


def _parse_date(date_str):
    if not date_str or not isinstance(date_str, str) or not DATE_RE.match(date_str.strip()):
        return None, "Неверный формат даты (YYYY-MM-DD)"
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date(), None
    except ValueError:
        return None, "Недопустимая дата"


def get_attendance_report(date_from, date_to):
    """
    Отчёт журнала посещаемости за период [date_from, date_to] включительно.
    """
    if not date_from or not date_to:
        return {"status": False, "error": "date_from и date_to обязательны (YYYY-MM-DD)"}

    d_from, err = _parse_date(date_from)
    if err:
        return {"status": False, "error": err}

    d_to, err = _parse_date(date_to)
    if err:
        return {"status": False, "error": err}

    if d_from > d_to:
        return {"status": False, "error": "date_from не может быть позже date_to"}

    period_days = (d_to - d_from).days + 1
    if period_days > MAX_PERIOD_DAYS:
        return {
            "status": False,
            "error": f"Период не может превышать {MAX_PERIOD_DAYS} дней",
        }

    date_from_iso = d_from.isoformat()
    date_to_iso = d_to.isoformat()

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, date, comment
            FROM class_days
            WHERE date BETWEEN %s AND %s
            ORDER BY date ASC
            """,
            (date_from_iso, date_to_iso),
        )
        class_days = [
            {
                "id": row["id"],
                "date": row["date"].isoformat() if row.get("date") else None,
                "comment": row.get("comment"),
            }
            for row in cursor.fetchall()
        ]

        if is_schools_schema_ready(cursor):
            cursor.execute(
                """
                SELECT
                    s.id AS student_id,
                    s.full_name,
                    s.class,
                    s.group_id,
                    g.name AS group_name,
                    s.school_id,
                    sch.short_name AS school_short_name
                FROM students s
                LEFT JOIN `groups` g ON g.id = s.group_id
                LEFT JOIN schools sch ON sch.id = s.school_id
                ORDER BY s.full_name ASC
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    s.id AS student_id,
                    s.full_name,
                    s.class,
                    s.group_id,
                    g.name AS group_name
                FROM students s
                LEFT JOIN `groups` g ON g.id = s.group_id
                ORDER BY s.full_name ASC
                """
            )

        students = []
        for row in cursor.fetchall():
            item = {
                "student_id": row["student_id"],
                "full_name": row["full_name"],
                "class": row.get("class"),
                "group_id": row.get("group_id"),
                "group_name": row.get("group_name"),
            }
            if "school_id" in row:
                item["school_id"] = row.get("school_id")
                item["school_short_name"] = row.get("school_short_name")
            else:
                item["school_id"] = None
                item["school_short_name"] = None
            students.append(item)

        cursor.execute(
            """
            SELECT
                a.id,
                a.student_id,
                a.class_day_id,
                a.attendance_type_id,
                t.code AS type_code,
                t.name_ru AS type_name,
                a.zap_id,
                zd.id AS zap_date_id
            FROM class_day_attendance a
            JOIN class_days cd ON cd.id = a.class_day_id
            JOIN attendance_types t ON t.id = a.attendance_type_id
            LEFT JOIN zap_dates zd ON zd.zap_id = a.zap_id AND zd.date = cd.date
            WHERE cd.date BETWEEN %s AND %s
            ORDER BY cd.date ASC, a.student_id ASC, a.id ASC
            """,
            (date_from_iso, date_to_iso),
        )
        entries = [
            {
                "id": row["id"],
                "student_id": row["student_id"],
                "class_day_id": row["class_day_id"],
                "attendance_type_id": row["attendance_type_id"],
                "type_code": row["type_code"],
                "type_name": row["type_name"],
                "zap_id": row.get("zap_id"),
                "zap_date_id": row.get("zap_date_id"),
            }
            for row in cursor.fetchall()
        ]

        return {
            "status": True,
            "period": {"date_from": date_from_iso, "date_to": date_to_iso},
            "class_days": class_days,
            "students": students,
            "entries": entries,
        }

    except Exception as err:
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
