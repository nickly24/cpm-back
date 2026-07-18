from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.serv.student_plain_credentials import ensure_student_credentials_table

def delete_user(role, user_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        user_tables = {
            'student': 'students',
            'proctor': 'proctors',
            'admin': 'admins',
            'examinator': 'examinators',
            'supervisor': 'supervisors'
        }

        if role not in user_tables:
            print("Ошибка: Неверная роль.")
            return {"status": False, "error": "Неверная роль"}

        # Удаляем из сущности
        table_name = user_tables[role]
        if role == "student":
            from cpm_back.services.homework_files.cascade import queue_and_delete_submission_data
            queue_and_delete_submission_data(cursor, student_id=user_id)
            ensure_student_credentials_table(cursor)
            cursor.execute("DELETE FROM student_credentials WHERE student_id = %s", (user_id,))

        delete_entity_query = f"DELETE FROM {table_name} WHERE id = %s"
        cursor.execute(delete_entity_query, (user_id,))
        # Удаляем из auth_users
        delete_auth_query = "DELETE FROM auth_users WHERE role = %s AND ref_id = %s"
        cursor.execute(delete_auth_query, (role, user_id))
        connection.commit()

        print(f"Пользователь с ролью '{role}' и id '{user_id}' успешно удалён.")
        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
