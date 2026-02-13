from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def assign_proctor_to_group(proctor_id, group_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        # Проверим существует ли группа
        cursor.execute('''SELECT id FROM `groups` WHERE id = %s''', (group_id,))
        group = cursor.fetchone()
        if not group:
            print("Группа не найдена")
            return {"status": False, "error": "Group not found"}

        # Проверим существует ли проктор
        cursor.execute("SELECT id FROM proctors WHERE id = %s", (proctor_id,))
        proctor = cursor.fetchone()
        if not proctor:
            return {"status": False, "error": "Proctor not found"}

        # Обновляем группу у проктора
        update_query = "UPDATE proctors SET group_id = %s WHERE id = %s"
        cursor.execute(update_query, (group_id, proctor_id))
        connection.commit()

        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
