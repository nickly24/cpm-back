from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_proctor_by_group(group_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT id, full_name FROM proctors WHERE group_id = %s", (group_id,))
        proctor = cursor.fetchone()

        if not proctor:
            return {"status": False, "res": 'No proctor in this group'}

        return {
            "status": True,
            "res": {
                "proctor_id": proctor["id"],
                "full_name": proctor["full_name"]
            }
        }

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": None}

    finally:
        if connection:
            close_db_connection(connection)
