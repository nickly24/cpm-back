from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.exam.admin_list_utils import (
    build_pagination,
    normalize_search_query,
    parse_page_limit,
)


def _map_session_row(s):
    return {
        "id": s["id"],
        "points": s["val"],
        "grade": s["points"],
        "examinator": s["examinator"],
        "student_id": s.get("student_id"),
        "exam_id": s["exam_id"],
        "exam_name": s["exam_name"],
        "exam_date": s["exam_date"],
        "student_name": s.get("student_name"),
    }


def _session_sort_sql(sort):
    if sort == "grade":
        return "es.points DESC, s.full_name ASC"
    if sort == "points":
        return "es.val DESC, s.full_name ASC"
    return "s.full_name ASC, es.id ASC"


def _student_session_sort_sql(sort):
    if sort == "grade":
        return "es.points DESC, e.date DESC"
    if sort == "points":
        return "es.val DESC, e.date DESC"
    return "e.date DESC, es.id DESC"


def _exam_sort_sql(sort):
    if sort == "name":
        return "e.name ASC, e.date DESC"
    return "e.date DESC, e.name ASC"


def get_all_exams():
    """
    Получает все экзамены из базы данных
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = "SELECT id, name, date FROM exams ORDER BY date DESC"
        cursor.execute(query)
        exams = cursor.fetchall()

        return {
            "status": True,
            "exams": exams,
        }

    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_all_exams_paginated(page_raw=1, limit_raw=20, search=None, sort="date"):
    connection = None
    try:
        page, limit, skip = parse_page_limit(page_raw, limit_raw)
        search = normalize_search_query(search)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_parts = []
        params = []
        if search:
            where_parts.append("e.name LIKE %s")
            params.append(f"%{search}%")
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        cursor.execute(
            f"SELECT COUNT(*) as total FROM exams e {where_sql}",
            params,
        )
        total = cursor.fetchone()["total"]

        order_sql = _exam_sort_sql(sort if sort in ("date", "name") else "date")
        cursor.execute(
            f"""
            SELECT
                e.id,
                e.name,
                e.date,
                (
                    SELECT COUNT(*)
                    FROM exam_sessions es
                    WHERE es.exam_id = e.id
                ) AS sessions_count
            FROM exams e
            {where_sql}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
            """,
            params + [limit, skip],
        )
        exams = cursor.fetchall()

        return {
            "status": True,
            "exams": exams,
            "pagination": build_pagination(total, page, limit),
        }
    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_session(student_id, exam_id):
    """
    Получает сессию экзамена для студента
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id, name, date FROM exams WHERE id = %s", (exam_id,))
        exam = cursor.fetchone()

        if not exam:
            return {
                "status": False,
                "error": "Экзамен не найден",
            }

        cursor.execute(
            """
            SELECT id, val, points, examinator
            FROM exam_sessions
            WHERE exam_id = %s AND student_id = %s
            """,
            (exam_id, student_id),
        )

        session = cursor.fetchone()

        if not session:
            return {
                "status": False,
                "error": "Сессия экзамена не найдена",
            }

        return {
            "status": True,
            "exam": exam,
            "grade": session["points"],
            "score": session["val"],
            "examinator": session["examinator"],
        }

    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_sessions_by_student(student_id):
    """
    Получает все сессии экзаменов для студента
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                es.id,
                es.val,
                es.points,
                es.examinator,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            WHERE es.student_id = %s
            ORDER BY e.date DESC
        """

        cursor.execute(query, (student_id,))
        raw_sessions = cursor.fetchall()
        sessions = [_map_session_row(s) for s in raw_sessions]

        return {
            "status": True,
            "sessions": sessions,
        }

    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_sessions_by_student_paginated(
    student_id,
    page_raw=1,
    limit_raw=20,
    grade=None,
    sort="exam_date",
):
    connection = None
    try:
        page, limit, skip = parse_page_limit(page_raw, limit_raw)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_parts = ["es.student_id = %s"]
        params = [student_id]

        if grade is not None and str(grade) not in ("", "all"):
            try:
                where_parts.append("es.points = %s")
                params.append(int(grade))
            except (TypeError, ValueError):
                pass

        where_sql = " AND ".join(where_parts)
        order_sql = _student_session_sort_sql(
            sort if sort in ("exam_date", "grade", "points") else "exam_date"
        )

        cursor.execute(
            f"""
            SELECT
                COUNT(*) as count,
                AVG(es.points) as avg_grade,
                COALESCE(SUM(es.val), 0) as total_points
            FROM exam_sessions es
            WHERE {where_sql}
            """,
            params,
        )
        summary_row = cursor.fetchone()
        summary = {
            "count": int(summary_row["count"] or 0),
            "averageGrade": float(summary_row["avg_grade"] or 0),
            "totalPoints": int(summary_row["total_points"] or 0),
        }

        cursor.execute(
            f"""
            SELECT COUNT(*) as total
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            WHERE {where_sql}
            """,
            params,
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT
                es.id,
                es.val,
                es.points,
                es.examinator,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
            """,
            params + [limit, skip],
        )
        sessions = [_map_session_row(row) for row in cursor.fetchall()]

        return {
            "status": True,
            "sessions": sessions,
            "summary": summary,
            "pagination": build_pagination(total, page, limit),
        }
    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_all_exam_sessions():
    """
    Получает все сессии экзаменов (для администраторов)
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                es.id,
                es.val,
                es.points,
                es.examinator,
                es.student_id,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date,
                s.full_name as student_name
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            INNER JOIN students s ON es.student_id = s.id
            ORDER BY e.date DESC, s.full_name ASC
        """

        cursor.execute(query)
        sessions = [_map_session_row(s) for s in cursor.fetchall()]

        return {
            "status": True,
            "sessions": sessions,
        }

    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_sessions_by_exam(exam_id):
    """
    Получает все сессии для конкретного экзамена (для администраторов)
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                es.id,
                es.val,
                es.points,
                es.examinator,
                es.student_id,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date,
                s.full_name as student_name
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            INNER JOIN students s ON es.student_id = s.id
            WHERE e.id = %s
            ORDER BY s.full_name ASC
        """

        cursor.execute(query, (exam_id,))
        sessions = [_map_session_row(s) for s in cursor.fetchall()]

        return {
            "status": True,
            "sessions": sessions,
        }

    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)


def get_exam_sessions_by_exam_paginated(
    exam_id,
    page_raw=1,
    limit_raw=20,
    search=None,
    sort="student_name",
):
    connection = None
    try:
        page, limit, skip = parse_page_limit(page_raw, limit_raw)
        search = normalize_search_query(search)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_parts = ["e.id = %s"]
        params = [exam_id]

        if search:
            if search.isdigit():
                where_parts.append(
                    "(s.id = %s OR s.full_name LIKE %s OR es.examinator LIKE %s)"
                )
                params.extend([int(search), f"%{search}%", f"%{search}%"])
            else:
                where_parts.append("(s.full_name LIKE %s OR es.examinator LIKE %s)")
                params.extend([f"%{search}%", f"%{search}%"])

        where_sql = " AND ".join(where_parts)
        order_sql = _session_sort_sql(
            sort if sort in ("student_name", "grade", "points") else "student_name"
        )

        cursor.execute(
            f"""
            SELECT COUNT(*) as total
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            INNER JOIN students s ON es.student_id = s.id
            WHERE {where_sql}
            """,
            params,
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT
                es.id,
                es.val,
                es.points,
                es.examinator,
                es.student_id,
                e.id as exam_id,
                e.name as exam_name,
                e.date as exam_date,
                s.full_name as student_name
            FROM exam_sessions es
            INNER JOIN exams e ON es.exam_id = e.id
            INNER JOIN students s ON es.student_id = s.id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
            """,
            params + [limit, skip],
        )
        sessions = [_map_session_row(row) for row in cursor.fetchall()]

        return {
            "status": True,
            "sessions": sessions,
            "pagination": build_pagination(total, page, limit),
        }
    except Exception as e:
        return {
            "status": False,
            "error": str(e),
        }
    finally:
        if connection:
            close_db_connection(connection)
