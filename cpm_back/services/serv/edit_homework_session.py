from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from datetime import datetime, date


def edit_homework_session(session_id, result=None, date_pass=None, status=None, student_id=None, homework_id=None):
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if not session_id and student_id is not None and homework_id is not None:
            cursor.execute(
                """
                SELECT id FROM homework_sessions
                WHERE student_id = %s AND homework_id = %s
                LIMIT 1
                """,
                (student_id, homework_id),
            )
            row = cursor.fetchone()
            if row:
                session_id = row["id"]

        if not session_id:
            return {"status": False, "error": "session_not_found"}

        # Проверяем, что сессия существует
        cursor.execute("SELECT * FROM homework_sessions WHERE id = %s", (session_id,))
        session = cursor.fetchone()
        if not session:
            return {"status": False, "error": "session_not_found"}

        if status is not None:
            try:
                status_val = int(status)
            except (TypeError, ValueError):
                return {"status": False, "error": "invalid_status"}
            if status_val not in (0, 1):
                return {"status": False, "error": "invalid_status"}
            if status_val == 0:
                cursor.execute(
                    "UPDATE homework_sessions SET status = 0, result = 0, date_pass = NULL WHERE id = %s",
                    (session_id,),
                )
                connection.commit()
                return {"status": True, "result": 0, "date_pass": None}

        # Готовим набор обновляемых полей
        set_clauses = []
        values = []

        if result is not None:
            try:
                result_val = int(result)
                if result_val < 0:
                    result_val = 0
                if result_val > 100:
                    result_val = 100
                set_clauses.append("result = %s")
                values.append(result_val)
            except (TypeError, ValueError):
                return {"status": False, "error": "invalid_result"}

        if date_pass is not None:
            # Поддержка str в ISO формате или datetime/date
            if isinstance(date_pass, str):
                date_val = None
                # Пробуем разные форматы
                formats = [
                    "%Y-%m-%d",      # 2025-10-30 (стандартный ISO)
                    "%Y-%m-%dT%H:%M:%S",  # 2025-10-30T00:00:00
                    "%Y-%m-%d %H:%M:%S",  # 2025-10-30 00:00:00
                    "%d.%m.%Y",      # 30.10.2025
                    "%d/%m/%Y",      # 30/10/2025
                ]
                
                # Сначала пробуем ISO формат
                try:
                    date_val = date.fromisoformat(date_pass.split('T')[0])
                except (ValueError, AttributeError):
                    # Пробуем другие форматы
                    for fmt in formats:
                        try:
                            date_val = datetime.strptime(date_pass, fmt).date()
                            break
                        except ValueError:
                            continue
                
                if date_val is None:
                    return {"status": False, "error": f"invalid_date_pass: не удалось распарсить дату '{date_pass}'"}
            elif isinstance(date_pass, datetime):
                date_val = date_pass.date()
            elif isinstance(date_pass, date):
                date_val = date_pass
            else:
                return {"status": False, "error": "invalid_date_pass_type"}

            # Получаем deadline для пересчета баллов
            cursor.execute("""
                SELECT h.deadline 
                FROM homework_sessions hs 
                JOIN homework h ON hs.homework_id = h.id 
                WHERE hs.id = %s
            """, (session_id,))
            homework_data = cursor.fetchone()
            
            if homework_data:
                deadline = homework_data["deadline"]

                # Пересчёт по дате только если балл не задан вручную в этом запросе
                if result is None:
                    auto_result = 100
                    if date_val > deadline:
                        delta_days = (date_val - deadline).days
                        auto_result -= delta_days * 5
                        if auto_result < 0:
                            auto_result = 0
                    set_clauses.append("result = %s")
                    values.append(auto_result)

            set_clauses.append("date_pass = %s")
            values.append(date_val)

        if status is not None:
            try:
                status_val = int(status)
                if status_val not in (0, 1):
                    return {"status": False, "error": "invalid_status"}
                set_clauses.append("status = %s")
                values.append(status_val)
            except (TypeError, ValueError):
                return {"status": False, "error": "invalid_status"}

        if not set_clauses:
            return {"status": False, "error": "nothing_to_update"}

        update_sql = f"UPDATE homework_sessions SET {', '.join(set_clauses)} WHERE id = %s"
        values.append(session_id)
        cursor.execute(update_sql, tuple(values))
        connection.commit()

        # Получаем обновленные данные для возврата
        cursor.execute("SELECT result, date_pass FROM homework_sessions WHERE id = %s", (session_id,))
        updated_session = cursor.fetchone()
        
        return {
            "status": True, 
            "result": updated_session["result"] if updated_session else None,
            "date_pass": updated_session["date_pass"] if updated_session else None
        }

    except Exception as err:
        if connection:
            connection.rollback()
        return {"status": False, "error": str(err)}
    finally:
        if connection:
            close_db_connection(connection)


