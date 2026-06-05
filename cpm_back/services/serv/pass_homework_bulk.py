from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .pass_homework import pass_homework


def pass_homework_bulk(proctor_id, homework_id, date_pass, result=None):
    """
    Отметить сдачу всем ученикам группы проктора, у кого ещё нет сдачи по этому ДЗ.
    result: если передан — одинаковый балл всем; иначе авто по дате и дедлайну.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT group_id FROM proctors WHERE id = %s", (proctor_id,))
        proctor = cursor.fetchone()
        if not proctor or proctor.get("group_id") is None:
            return {"status": False, "error": "proctor_group_not_found", "passed": 0, "total": 0}

        group_id = proctor["group_id"]
        cursor.execute(
            """
            SELECT s.id AS student_id, hs.id AS session_id, hs.status
            FROM students s
            LEFT JOIN homework_sessions hs
              ON hs.student_id = s.id AND hs.homework_id = %s
            WHERE s.group_id = %s
            ORDER BY s.full_name ASC
            """,
            (homework_id, group_id),
        )
        rows = cursor.fetchall()
        cursor.close()
        close_db_connection(connection)
        connection = None

        pending = [row for row in rows if row.get("status") != 1]
        passed = 0
        errors = []

        for row in pending:
            answer = pass_homework(
                row.get("session_id"),
                date_pass,
                student_id=row["student_id"],
                homework_id=homework_id,
                result=result,
            )
            if answer.get("status"):
                passed += 1
            else:
                errors.append({"student_id": row["student_id"], "error": "pass_failed"})

        return {
            "status": True,
            "passed": passed,
            "total": len(pending),
            "skipped": len(rows) - len(pending),
            "errors": errors or None,
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err), "passed": 0, "total": 0}

    finally:
        if connection:
            close_db_connection(connection)
