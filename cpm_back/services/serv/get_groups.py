from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_all_groups():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute('''SELECT id, name FROM `groups` ORDER BY name ASC''')
        groups = cursor.fetchall()

        if not groups:
            return {"status": False, "res": []}

        groups_list = [{"group_id": g["id"], "group_name": g["name"]} for g in groups]

        return {"status": True, "res": groups_list}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": []}

    finally:
        if connection:
            close_db_connection(connection)

