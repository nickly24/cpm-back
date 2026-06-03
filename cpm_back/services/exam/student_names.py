"""Имена студентов из MySQL для обогащения Mongo-сущностей тестов."""
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.exam.admin_list_utils import (
    MAX_SEARCH_STUDENT_IDS,
    MIN_SEARCH_LENGTH,
)


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


def search_student_ids_by_name(query, max_results=None):
    """
    Поиск id студентов по подстроке full_name (MySQL).
    Возвращает [] если запрос короче MIN_SEARCH_LENGTH.
    """
    q = (query or "").strip()
    if len(q) < MIN_SEARCH_LENGTH:
        return None

    limit = max_results or MAX_SEARCH_STUDENT_IDS
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id FROM students WHERE full_name LIKE %s ORDER BY full_name ASC LIMIT %s",
            (f"%{q}%", limit),
        )
        rows = cursor.fetchall()
        return [int(r["id"]) for r in rows]
    except Exception as e:
        print(f"search_student_ids_by_name: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)


def expand_student_ids_for_mongo(student_ids):
    """int/str в Mongo studentId могут отличаться — расширяем для $in."""
    if student_ids is None:
        return None
    if not student_ids:
        return []
    expanded = set()
    for raw in student_ids:
        sid = _normalize_student_id(raw)
        if sid is not None:
            expanded.add(sid)
            expanded.add(str(sid))
        elif raw is not None:
            expanded.add(raw)
            try:
                expanded.add(int(raw))
            except (TypeError, ValueError):
                pass
    return list(expanded)


def build_student_id_mongo_filter(student_ids):
    expanded = expand_student_ids_for_mongo(student_ids)
    if expanded is None:
        return {}
    if not expanded:
        return {"studentId": {"$in": []}}
    return {"studentId": {"$in": expanded}}
