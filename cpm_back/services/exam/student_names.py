"""Имена студентов из MySQL для обогащения Mongo-сущностей тестов."""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def _normalize_student_id(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_student_names_by_ids(student_ids):
    """
    Возвращает dict { student_id (int): full_name }.
    Неизвестные id в ответ не попадают.
    """
    unique = []
    seen = set()
    for raw in student_ids or []:
        sid = _normalize_student_id(raw)
        if sid is not None and sid not in seen:
            seen.add(sid)
            unique.append(sid)

    if not unique:
        return {}

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        placeholders = ",".join(["%s"] * len(unique))
        cursor.execute(
            f"SELECT id, full_name FROM students WHERE id IN ({placeholders})",
            tuple(unique),
        )
        rows = cursor.fetchall()
        return {int(r["id"]): r.get("full_name") or "—" for r in rows}
    except Exception as e:
        print(f"get_student_names_by_ids: {e}")
        return {}
    finally:
        if connection:
            close_db_connection(connection)


def resolve_student_name(student_id, names_map):
    sid = _normalize_student_id(student_id)
    if sid is not None and sid in names_map:
        return names_map[sid]
    if student_id is not None:
        return names_map.get(student_id) or names_map.get(str(student_id))
    return None
