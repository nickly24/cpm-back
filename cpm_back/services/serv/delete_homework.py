from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def delete_homework(homework_id):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Удаляем все записи в homework_sessions для этого домашнего задания
        delete_sessions_query = "DELETE FROM homework_sessions WHERE homework_id = %s"
        cursor.execute(delete_sessions_query, (homework_id,))
        connection.commit()

        # Удаляем само домашнее задание
        delete_homework_query = "DELETE FROM homework WHERE id = %s"
        cursor.execute(delete_homework_query, (homework_id,))
        connection.commit()

        print(f"Домашнее задание с id {homework_id} успешно удалено вместе со всеми сессиями.")
        return {"status": True}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        if connection:
            connection.rollback()
        return {"status": False}

    finally:
        if connection:
            close_db_connection(connection)