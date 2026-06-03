"""
Админка групп: пакетная загрузка без N+1, overview с пагинацией, поиск.
"""
from collections import defaultdict

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

NO_PROCTOR = {"status": False, "res": "No proctor in this group"}

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 100


def _parse_page_limit(page, limit, default_limit=DEFAULT_PAGE_LIMIT):
    try:
        page = int(page) if page is not None else 1
    except (TypeError, ValueError):
        page = 1
    try:
        limit = int(limit) if limit is not None else default_limit
    except (TypeError, ValueError):
        limit = default_limit

    page = max(1, page)
    limit = min(MAX_PAGE_LIMIT, max(1, limit))
    return page, limit, (page - 1) * limit


def _parse_search_limit(limit):
    try:
        limit = int(limit) if limit is not None else DEFAULT_SEARCH_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_SEARCH_LIMIT
    return min(MAX_SEARCH_LIMIT, max(1, limit))


def _normalize_search(search):
    if search is None:
        return None
    search = str(search).strip()
    return search or None


def _group_search_clause(search):
    if not search:
        return "", []
    pattern = f"%{search}%"
    return " WHERE (g.name LIKE %s OR CAST(g.id AS CHAR) LIKE %s)", [pattern, pattern]


def _student_row(row):
    payload = {
        "id": row["id"],
        "full_name": row["full_name"],
    }
    if "class" in row:
        payload["class"] = row["class"]
    if "school_id" in row:
        payload["school_id"] = row.get("school_id")
    if "school_name" in row:
        payload["school_name"] = row.get("school_name")
    return payload


def _proctor_payload(row):
    if not row:
        return NO_PROCTOR.copy()
    return {
        "status": True,
        "res": {
            "proctor_id": row["id"],
            "full_name": row["full_name"],
        },
    }


def _fetch_students_by_group_ids(cursor, group_ids):
    if not group_ids:
        return defaultdict(list)

    from .school_schema import is_schools_schema_ready

    placeholders = ", ".join(["%s"] * len(group_ids))
    if is_schools_schema_ready(cursor):
        cursor.execute(
            f"""
            SELECT
                s.id,
                s.full_name,
                s.class,
                s.group_id,
                s.school_id,
                sch.name AS school_name
            FROM students s
            LEFT JOIN schools sch ON sch.id = s.school_id
            WHERE s.group_id IN ({placeholders})
            ORDER BY s.full_name ASC
            """,
            tuple(group_ids),
        )
    else:
        cursor.execute(
            f"""
            SELECT id, full_name, class, group_id
            FROM students
            WHERE group_id IN ({placeholders})
            ORDER BY full_name ASC
            """,
            tuple(group_ids),
        )

    students_by_group = defaultdict(list)
    for row in cursor.fetchall():
        students_by_group[row["group_id"]].append(_student_row(row))
    return students_by_group


def _fetch_proctors_by_group_ids(cursor, group_ids):
    if not group_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(group_ids))
    cursor.execute(
        f"""
        SELECT id, full_name, group_id
        FROM proctors
        WHERE group_id IN ({placeholders})
        """,
        tuple(group_ids),
    )

    proctors_by_group = {}
    for row in cursor.fetchall():
        proctors_by_group[row["group_id"]] = _proctor_payload(row)
    return proctors_by_group


def merge_groups_students_proctors():
    """
    Полный снимок всех групп (legacy-формат для cpm-app).
    3 SQL-запроса и одно подключение вместо 2N+1.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id, name FROM `groups` ORDER BY name ASC")
        groups = cursor.fetchall()
        group_ids = [row["id"] for row in groups]

        students_by_group = _fetch_students_by_group_ids(cursor, group_ids)
        proctors_by_group = _fetch_proctors_by_group_ids(cursor, group_ids)

        answer = []
        for group in groups:
            group_id = group["id"]
            answer.append(
                {
                    "item": {
                        "group_id": group_id,
                        "group_name": group["name"],
                    },
                    "students": students_by_group.get(group_id, []),
                    "proctor": proctors_by_group.get(group_id, NO_PROCTOR.copy()),
                }
            )
        return answer

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return []

    finally:
        if connection:
            close_db_connection(connection)


def get_groups_overview(search=None, page=1, limit=DEFAULT_PAGE_LIMIT):
    """
    Лёгкий список групп: проктор + счётчик учеников, без полного списка детей.
    """
    connection = None
    search = _normalize_search(search)
    page, limit, offset = _parse_page_limit(page, limit)

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_sql, params = _group_search_clause(search)

        cursor.execute(
            f"SELECT COUNT(*) AS total FROM `groups` g{where_sql}",
            params,
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT g.id, g.name
            FROM `groups` g
            {where_sql}
            ORDER BY g.name ASC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        groups = cursor.fetchall()
        group_ids = [row["id"] for row in groups]

        proctors_by_group = _fetch_proctors_by_group_ids(cursor, group_ids)

        student_counts = defaultdict(int)
        if group_ids:
            placeholders = ", ".join(["%s"] * len(group_ids))
            cursor.execute(
                f"""
                SELECT group_id, COUNT(*) AS student_count
                FROM students
                WHERE group_id IN ({placeholders})
                GROUP BY group_id
                """,
                tuple(group_ids),
            )
            for row in cursor.fetchall():
                student_counts[row["group_id"]] = row["student_count"]

        items = []
        for group in groups:
            group_id = group["id"]
            items.append(
                {
                    "item": {
                        "group_id": group_id,
                        "group_name": group["name"],
                    },
                    "student_count": student_counts.get(group_id, 0),
                    "proctor": proctors_by_group.get(group_id, NO_PROCTOR.copy()),
                }
            )

        return {
            "status": True,
            "page": page,
            "limit": limit,
            "total": total,
            "pages": max(1, (total + limit - 1) // limit) if total else 1,
            "res": items,
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {
            "status": False,
            "page": page,
            "limit": limit,
            "total": 0,
            "pages": 1,
            "res": [],
            "error": str(err),
        }

    finally:
        if connection:
            close_db_connection(connection)


def get_group_members(group_id):
    """Состав одной группы — для ленивой подгрузки карточки."""
    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        return {"status": False, "error": "group_id должен быть числом"}

    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id, name FROM `groups` WHERE id = %s", (group_id,))
        group = cursor.fetchone()
        if not group:
            return {"status": False, "error": f"Группа с ID {group_id} не найдена"}

        students_by_group = _fetch_students_by_group_ids(cursor, [group_id])
        proctors_by_group = _fetch_proctors_by_group_ids(cursor, [group_id])

        return {
            "status": True,
            "item": {
                "group_id": group["id"],
                "group_name": group["name"],
            },
            "students": students_by_group.get(group_id, []),
            "proctor": proctors_by_group.get(group_id, NO_PROCTOR.copy()),
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)


def search_groups_and_members(query, limit=DEFAULT_SEARCH_LIMIT):
    """
    Поиск по названию группы / ID группы и по ФИО ученика.
    """
    connection = None
    query = _normalize_search(query)
    if not query:
        return {"status": False, "error": "Параметр q обязателен"}

    limit = _parse_search_limit(limit)
    pattern = f"%{query}%"

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT g.id AS group_id, g.name AS group_name
            FROM `groups` g
            WHERE g.name LIKE %s OR CAST(g.id AS CHAR) LIKE %s
            ORDER BY g.name ASC
            LIMIT %s
            """,
            (pattern, pattern, limit),
        )
        groups = cursor.fetchall()

        from .school_schema import is_schools_schema_ready

        if is_schools_schema_ready(cursor):
            cursor.execute(
                """
                SELECT
                    s.id,
                    s.full_name,
                    s.class,
                    s.group_id,
                    s.school_id,
                    g.name AS group_name,
                    sch.name AS school_name
                FROM students s
                LEFT JOIN `groups` g ON g.id = s.group_id
                LEFT JOIN schools sch ON sch.id = s.school_id
                WHERE s.full_name LIKE %s
                ORDER BY s.full_name ASC
                LIMIT %s
                """,
                (pattern, limit),
            )
        else:
            cursor.execute(
                """
                SELECT
                    s.id,
                    s.full_name,
                    s.class,
                    s.group_id,
                    g.name AS group_name
                FROM students s
                LEFT JOIN `groups` g ON g.id = s.group_id
                WHERE s.full_name LIKE %s
                ORDER BY s.full_name ASC
                LIMIT %s
                """,
                (pattern, limit),
            )
        students = [
            {
                "id": row["id"],
                "full_name": row["full_name"],
                "class": row["class"],
                "group_id": row.get("group_id"),
                "group_name": row.get("group_name"),
                "school_id": row.get("school_id"),
                "school_name": row.get("school_name"),
            }
            for row in cursor.fetchall()
        ]

        return {
            "status": True,
            "query": query,
            "groups": groups,
            "students": students,
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "groups": [], "students": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
