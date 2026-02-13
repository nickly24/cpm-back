from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def reset_group_for_user(user_type, user_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        if user_type == "student":
            query = "UPDATE students SET group_id = NULL WHERE id = %s"
        elif user_type == "proctor":
            query = "UPDATE proctors SET group_id = NULL WHERE id = %s"
        else:
            print("Неверный тип пользователя")
            return {"status": False}

        cursor.execute(query, (user_id,))
        connection.commit()

        if cursor.rowcount == 0:
            return {"status": False}

        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False}

    finally:
        if connection:
            close_db_connection(connection)
