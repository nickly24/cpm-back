from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def process_zap(zap_id, status, answer, dates=None):
    """
    Обрабатывает запрос на отгул (одобрить или отклонить)
    Если одобрен и указаны даты, прикрепляет запрос к этим датам в посещаемости
    
    Args:
        zap_id: ID запроса
        status: Новый статус ('apr' или 'dec')
        answer: Ответ админа
        dates: Список дат в формате ['YYYY-MM-DD', ...] для привязки к посещаемости
    
    Returns:
        dict: Результат обработки
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        # Получаем информацию о запросе
        cursor.execute("""
            SELECT id, student_id, status FROM zaps WHERE id = %s
        """, (zap_id,))
        
        zap = cursor.fetchone()
        if not zap:
            return {"status": False, "error": "Запрос не найден"}

        student_id = zap[1]
        old_status = zap[2]

        # Обновляем статус и ответ
        cursor.execute("""
            UPDATE zaps 
            SET status = %s, answer = %s
            WHERE id = %s
        """, (status, answer, zap_id))

        # Если одобрен и указаны даты, привязываем к посещаемости
        if status == 'apr' and dates:
            for date_str in dates:
                # Проверяем, существует ли запись в attendance
                cursor.execute("""
                    SELECT id FROM attendance 
                    WHERE student_id = %s AND date = %s
                """, (student_id, date_str))
                
                existing = cursor.fetchone()
                
                if existing:
                    # Обновляем существующую запись
                    cursor.execute("""
                        UPDATE attendance 
                        SET attendance_rate = 2, zap_id = %s
                        WHERE id = %s
                    """, (zap_id, existing[0]))
                else:
                    # Создаем новую запись
                    cursor.execute("""
                        INSERT INTO attendance (date, student_id, attendance_rate, zap_id)
                        VALUES (%s, %s, 2, %s)
                    """, (date_str, student_id, zap_id))

        connection.commit()

        return {
            "status": True,
            "message": "Запрос успешно обработан"
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)

