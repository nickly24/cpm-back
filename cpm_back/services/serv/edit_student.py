from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def edit_student(student_id, full_name=None, class_number=None, group_id=None, tg_name=None):
    """
    Редактирует данные студента
    
    Args:
        student_id (int): ID студента
        full_name (str, optional): Новое полное имя студента
        class_number (int, optional): Новый класс студента (9, 10 или 11)
        group_id (int, optional): Новый ID группы студента
        tg_name (str, optional): Новый Telegram никнейм студента
    
    Returns:
        dict: Результат операции с обновленными данными студента
    """
    connection = None
    try:
        # Проверяем, что хотя бы одно поле для обновления передано
        if all(param is None for param in [full_name, class_number, group_id, tg_name]):
            return {
                "status": False,
                "error": "Необходимо указать хотя бы одно поле для обновления"
            }
        
        # Проверяем корректность класса, если он передан
        if class_number is not None and class_number not in [9, 10, 11]:
            return {
                "status": False,
                "error": "Класс должен быть 9, 10 или 11"
            }
        
        # Получаем подключение из пула
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Проверяем существование студента
        cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        
        if not student:
            return {
                "status": False,
                "error": f"Студент с ID {student_id} не найден"
            }
        
        # Формируем список полей для обновления
        update_fields = []
        update_values = []
        
        if full_name is not None:
            update_fields.append("full_name = %s")
            update_values.append(full_name)
        
        if class_number is not None:
            update_fields.append("class = %s")
            update_values.append(class_number)
        
        if group_id is not None:
            update_fields.append("group_id = %s")
            update_values.append(group_id)
        
        if tg_name is not None:
            update_fields.append("tg_name = %s")
            update_values.append(tg_name)
        
        # Добавляем student_id в конец списка значений
        update_values.append(student_id)
        
        # Выполняем обновление
        update_query = f"""
        UPDATE students 
        SET {', '.join(update_fields)}
        WHERE id = %s
        """
        cursor.execute(update_query, tuple(update_values))
        
        # Подтверждаем транзакцию
        connection.commit()
        
        # Получаем обновленные данные студента
        cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
        updated_student = cursor.fetchone()
        
        return {
            "status": True,
            "message": "Данные студента успешно обновлены",
            "student_data": {
                "student_id": updated_student['id'],
                "full_name": updated_student['full_name'],
                "class": updated_student['class'],
                "group_id": updated_student['group_id'],
                "tg_name": updated_student.get('tg_name')
            }
        }
        
    except Exception as e:
        if connection:
            connection.rollback()
        return {
            "status": False,
            "error": f"Ошибка базы данных: {str(e)}"
        }
        
    finally:
        if connection:
            close_db_connection(connection)

