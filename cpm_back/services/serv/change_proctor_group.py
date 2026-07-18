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
        cursor.execute("SELECT id,group_id FROM proctors WHERE id = %s", (proctor_id,))
        proctor = cursor.fetchone()
        if not proctor:
            return {"status": False, "error": "Proctor not found"}

        # Обновляем группу у проктора
        update_query = "UPDATE proctors SET group_id = %s WHERE id = %s"
        cursor.execute(update_query, (group_id, proctor_id))
        old_group_id = proctor[1]
        if old_group_id is not None:
            cursor.execute(
                "UPDATE homework_submissions sub JOIN students s ON s.id=sub.student_id "
                "SET sub.state='submitted',sub.reviewer_role=NULL,sub.reviewer_id=NULL "
                "WHERE s.group_id=%s AND sub.state='in_review' AND sub.reviewer_role='proctor'",
                (old_group_id,),
            )
        connection.commit()

        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
