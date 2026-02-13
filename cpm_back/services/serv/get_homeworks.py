from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def get_homeworks():
    """Все домашние задания без пагинации (обратная совместимость)."""
    return get_homeworks_paginated(page=1, limit=500, homework_type=None)


def get_homeworks_paginated(page=1, limit=20, homework_type=None):
    """
    Домашние задания с пагинацией и опциональным фильтром по типу (ОВ, ДЗНВ и т.д.).
    По сути «по направлениям» — тип домашки как направление.
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        where_sql = "WHERE h.type = %s" if homework_type else ""
        count_params = [homework_type] if homework_type else []

        count_query = f"SELECT COUNT(*) as total FROM homework h {where_sql}"
        cursor.execute(count_query, count_params)
        total = cursor.fetchone()["total"]

        offset = (page - 1) * limit
        query_params = count_params + [limit, offset]

        query = f"""
            SELECT id, name, type, deadline
            FROM homework h
            {where_sql}
            ORDER BY h.deadline DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, query_params)
        results = cursor.fetchall()

        if not results:
            return {
                "status": True,
                "res": [],
                "pagination": {
                    "current_page": page,
                    "total_pages": 0,
                    "total_items": 0,
                    "items_per_page": limit,
                },
            }

        homework_list = [
            {"id": row["id"], "name": row["name"], "type": row["type"], "deadline": row["deadline"]}
            for row in results
        ]
        total_pages = (total + limit - 1) // limit if total else 1

        return {
            "status": True,
            "res": homework_list,
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
