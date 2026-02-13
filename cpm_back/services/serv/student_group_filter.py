from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_student_ids_and_names_by_group(group_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = "SELECT id, full_name FROM students WHERE group_id = %s"
        cursor.execute(query, (group_id,))
        results = cursor.fetchall()

        if not results:
            return {"status": False, "res": []}

        data = [{"id": row['id'], "full_name": row['full_name']} for row in results]
        return {"status": True, "res": data}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": []}

    finally:
        if connection:
            close_db_connection(connection)
