from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

def get_directions():
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = "SELECT * FROM directions"
        cursor.execute(query)
        directions = cursor.fetchall()
        return directions
    except Exception as e:
        print(f"Ошибка при получении направлений: {e}")
        return []
    finally:
        if connection:
            close_db_connection(connection)