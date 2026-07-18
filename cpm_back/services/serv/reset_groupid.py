from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def reset_group_for_user(user_type, user_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        if user_type == "student":
            cursor.execute("UPDATE homework_submissions SET state='submitted',reviewer_role=NULL,reviewer_id=NULL WHERE student_id=%s AND state='in_review' AND reviewer_role='proctor'", (user_id,))
            query = "UPDATE students SET group_id = NULL WHERE id = %s"
        elif user_type == "proctor":
            cursor.execute("SELECT group_id FROM proctors WHERE id=%s", (user_id,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                cursor.execute("UPDATE homework_submissions sub JOIN students s ON s.id=sub.student_id SET sub.state='submitted',sub.reviewer_role=NULL,sub.reviewer_id=NULL WHERE s.group_id=%s AND sub.state='in_review' AND sub.reviewer_role='proctor'", (row[0],))
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
