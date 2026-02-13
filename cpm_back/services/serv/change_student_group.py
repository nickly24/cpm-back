from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def assign_student_to_group(student_id, group_id):
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

        # Проверим существует ли студент
        cursor.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        if not student:
            print("Студент не найден")
            return {"status": False, "error": "Student not found"}

        # Обновляем группу у студента
        update_query = "UPDATE students SET group_id = %s WHERE id = %s"
        cursor.execute(update_query, (group_id, student_id))
        connection.commit()

        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
