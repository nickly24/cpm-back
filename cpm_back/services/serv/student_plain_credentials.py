from __future__ import annotations

from typing import Optional


def ensure_student_credentials_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_credentials (
            student_id INT NOT NULL PRIMARY KEY,
            login VARCHAR(255) NOT NULL,
            password VARCHAR(255) NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_student_credentials_student
                FOREIGN KEY (student_id) REFERENCES students(id)
                ON DELETE CASCADE
        )
        """
    )


def upsert_student_credentials(
    cursor,
    student_id: int,
    login: str,
    password: Optional[str] = None,
) -> None:
    ensure_student_credentials_table(cursor)
    cursor.execute(
        """
        INSERT INTO student_credentials (student_id, login, password)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            login = VALUES(login),
            password = COALESCE(VALUES(password), password)
        """,
        (student_id, login, password),
    )


def update_student_credentials(
    cursor,
    student_id: int,
    login: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    if login is None and password is None:
        return

    ensure_student_credentials_table(cursor)
    cursor.execute(
        """
        SELECT
            COALESCE(sc.login, a.username) AS login,
            sc.password AS password
        FROM students s
        LEFT JOIN auth_users a ON a.ref_id = s.id AND a.role = 'student'
        LEFT JOIN student_credentials sc ON sc.student_id = s.id
        WHERE s.id = %s
        """,
        (student_id,),
    )
    row = cursor.fetchone() or {}
    next_login = login if login is not None else row.get("login")
    next_password = password if password is not None else row.get("password")
    if next_login is None:
        return

    upsert_student_credentials(cursor, student_id, next_login, next_password)


def serialize_student_credentials(row: dict) -> dict:
    login = row.get("credential_login") or row.get("auth_login")
    password = row.get("credential_password")
    return {
        "login": login,
        "password": password,
        "password_hidden": bool(row.get("auth_login")) and not bool(password),
    }
