from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

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

        # Lock the parent before checking references: a concurrent FK-backed
        # assignment must not slip between this check and the actual deletion.
        table_name = user_tables[role]
        cursor.execute(f"SELECT id FROM `{table_name}` WHERE id=%s FOR UPDATE", (user_id,))
        if not cursor.fetchone():
            return {"status": False, "error": "Пользователь не найден", "http_status": 404}

        # The additive rollout deliberately precedes the legacy FK migration.
        # Protect existing outside-LMS results during that interval as well.
        if role == 'student':
            cursor.execute('SELECT 1 FROM exam_sessions WHERE student_id=%s LIMIT 1', (user_id,))
            if cursor.fetchone():
                return {"status": False, "error": "У ученика есть результаты экзаменов. Сначала удалите их.",
                        "code": "user_referenced_by_exam", "http_status": 409}

        cursor.execute("""
            SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_SCHEMA=DATABASE()
              AND REFERENCED_TABLE_NAME=%s AND REFERENCED_COLUMN_NAME='id'
              AND (TABLE_NAME LIKE 'classic_exam%%' OR TABLE_NAME='exam_sessions')
        """, (table_name,))
        references = cursor.fetchall()
        for child_table, child_column in references:
            # Identifiers are metadata, not request parameters; quote defensively.
            child_table = child_table.replace('`', '``')
            child_column = child_column.replace('`', '``')
            cursor.execute(f"SELECT 1 FROM `{child_table}` WHERE `{child_column}`=%s LIMIT 1", (user_id,))
            if cursor.fetchone():
                return {"status": False, "error": "Пользователь связан с экзаменом. Сначала удалите соответствующие экзаменационные данные.",
                        "code": "user_referenced_by_exam", "http_status": 409}

        if role == 'admin':
            audit_references = (
                ('exam_admin_commands', 'actor_role', 'actor_id'),
                ('classic_exam_assignments', 'created_by_role', 'created_by'),
                ('classic_exam_appeals', 'changed_by_role', 'changed_by_admin_id'),
                ('classic_exam_question_import_sessions', 'created_by_role', 'created_by'),
                ('classic_exam_assignment_import_sessions', 'created_by_role', 'created_by'),
                ('outside_exam_result_import_sessions', 'created_by_role', 'created_by'),
            )
            for audit_table, role_column, id_column in audit_references:
                cursor.execute('SHOW TABLES LIKE %s', (audit_table,))
                if not cursor.fetchone():
                    continue
                cursor.execute(f"SELECT 1 FROM `{audit_table}` WHERE `{role_column}`='admin' AND `{id_column}`=%s LIMIT 1", (user_id,))
                if cursor.fetchone():
                    return {"status": False, "error": "Администратор связан с историей экзаменов.",
                            "code": "user_referenced_by_exam", "http_status": 409}

        # No CREATE TABLE here: DDL implicitly commits and would break atomicity.
        if role == "student":
            cursor.execute("SHOW TABLES LIKE 'student_credentials'")
            if cursor.fetchone():
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
        if getattr(err, 'errno', None) == 1451:
            return {"status": False, "error": "Пользователь связан с данными системы и не может быть удалён.",
                    "code": "user_referenced_by_exam", "http_status": 409}
        return {"status": False, "error": "Не удалось удалить пользователя", "http_status": 500}

    finally:
        if connection:
            close_db_connection(connection)
