from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_users_by_role(role):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if role == "student":
            cursor.execute("SELECT id, full_name, group_id, class, tg_name FROM students ORDER BY full_name")
            result = [{
                "id": row["id"], 
                "full_name": row["full_name"],
                "group_id": row["group_id"],
                "class": row["class"],
                "tg_name": row["tg_name"]
            } for row in cursor.fetchall()]
        elif role == "proctor":
            cursor.execute("SELECT id, full_name, group_id FROM proctors ORDER BY full_name")
            result = [{
                "id": row["id"], 
                "full_name": row["full_name"],
                "group_id": row["group_id"]
            } for row in cursor.fetchall()]
        elif role == "examinator":
            cursor.execute("SELECT id, full_name FROM examinators ORDER BY full_name")
            result = [{"id": row["id"], "full_name": row["full_name"]} for row in cursor.fetchall()]
        elif role == "supervisor":
            cursor.execute("SELECT id, full_name FROM supervisors ORDER BY full_name")
            result = [{"id": row["id"], "full_name": row["full_name"]} for row in cursor.fetchall()]
        else:
            return {"status": False, "error": "Invalid role provided."}

        return {"status": True, "res": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "res": [], "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
