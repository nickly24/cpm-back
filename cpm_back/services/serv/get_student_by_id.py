from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_student_by_id(student_id):
    """
    Получает информацию о студенте по ID
    
    Args:
        student_id (str): ID студента
    
    Returns:
        dict: Информация о студенте
    """
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, full_name, group_id, class, tg_name 
            FROM students 
            WHERE id = %s
        """, (student_id,))
        
        student = cursor.fetchone()
        
        if not student:
            return {"status": False, "error": "Студент не найден"}
        
        result = {
            "id": student["id"],
            "name": student["full_name"],
            "class": student["class"],
            "group_id": student["group_id"],
            "tg_name": student["tg_name"]
        }
        
        return {"status": True, "data": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
