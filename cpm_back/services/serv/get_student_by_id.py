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
            SELECT
                s.id,
                s.full_name,
                s.group_id,
                s.school_id,
                s.class,
                s.tg_name,
                sch.name AS school_name,
                sch.short_name AS school_short_name
            FROM students s
            LEFT JOIN schools sch ON sch.id = s.school_id
            WHERE s.id = %s
        """, (student_id,))
        
        student = cursor.fetchone()
        
        if not student:
            return {"status": False, "error": "Студент не найден"}
        
        result = {
            "id": student["id"],
            "name": student["full_name"],
            "class": student["class"],
            "group_id": student["group_id"],
            "school_id": student.get("school_id"),
            "school_name": student.get("school_name"),
            "school_short_name": student.get("school_short_name"),
            "tg_name": student["tg_name"]
        }
        
        return {"status": True, "data": result}

    except Exception as err:
        print(f"Ошибка базы данных: {err}")
        return {"status": False, "error": str(err)}

    finally:
        if connection:
            close_db_connection(connection)
