from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


def get_directions_from_db():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM directions")
        return cursor.fetchall()
    except Exception as e:
        print(f"Ошибка при получении направлений: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)


def get_directions():
    from cpm_back.services.exam.exam_memory_cache import get_directions_cached

    return get_directions_cached()