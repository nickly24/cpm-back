from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def get_student_homework_dashboard(student_id, page=1, limit=20, homework_type=None):
    """
    Домашки студента с пагинацией и опциональным фильтром по типу (ОВ, ДЗНВ).
    Один запрос с LEFT JOIN — без N+1.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_parts = []
        count_params = []
        if homework_type:
            where_parts.append("h.type = %s")
            count_params.append(homework_type)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Общее количество домашних заданий (с учётом фильтра по типу)
        count_query = f"SELECT COUNT(*) as total FROM homework h {where_sql}"
        cursor.execute(count_query, count_params)
        total = cursor.fetchone()["total"]

        offset = (page - 1) * limit
        query_params = [student_id]
        if homework_type:
            query_params.append(homework_type)
        query_params.extend([limit, offset])

        # Один запрос: домашки + статус/балл студента
        query = f"""
            SELECT
                h.id as homework_id,
                h.name as homework_name,
                h.type as homework_type,
                h.deadline,
                hs.status,
                hs.result
            FROM homework h
            LEFT JOIN homework_sessions hs ON h.id = hs.homework_id AND hs.student_id = %s
            {where_sql}
            ORDER BY h.deadline DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, query_params)
        rows = cursor.fetchall()
        result_list = []

        for row in rows:
            if row["status"] == 1:
                homework_status = "ДЗ сдано"
                score = row["result"]
            elif row["status"] == 0:
                homework_status = "ДЗ не сделано"
                score = None
            else:
                homework_status = "ДЗ не сделано"
                score = None

            result_list.append({
                "homework_id": row["homework_id"],
                "homework_name": row["homework_name"],
                "homework_type": row["homework_type"],
                "deadline": row["deadline"],
                "status": homework_status,
                "result": score,
            })

        total_pages = (total + limit - 1) // limit if total else 1

        return {
            "status": True,
            "res": result_list,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total,
                "items_per_page": limit,
            },
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "pagination": None}

    finally:
        if connection:
            close_db_connection(connection)


def get_student_homework_dashboard_legacy(student_id):
    """
    Без пагинации — все домашки (обратная совместимость).
    """
    out = get_student_homework_dashboard(student_id, page=1, limit=500)
    if out.get("pagination"):
        out["total_items"] = out["pagination"]["total_items"]
        del out["pagination"]
    return out
