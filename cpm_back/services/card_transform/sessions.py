"""Сессии трансформации карточек в драфт."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

from .build_preview import (
    IMPORT_TYPE,
    build_preview,
    parse_request_payload,
    validate_preview_for_commit,
)

SESSION_TTL_HOURS = 72


def _table_exists(cursor) -> bool:
    cursor.execute("SHOW TABLES LIKE 'user_import_sessions'")
    return cursor.fetchone() is not None


def _schema_error() -> Dict[str, Any]:
    return {
        "status": False,
        "error": "Таблицы импорта не найдены. Примените миграцию 009_user_import.sql",
        "code": "user_import_schema_missing",
    }


def _serialize_session(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("preview_payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "session_id": row["id"],
        "import_type": row.get("import_type") or IMPORT_TYPE,
        "source_filename": row.get("source_filename"),
        "preview": payload,
        "created_by": row.get("created_by"),
        "created_by_name": row.get("created_by_name"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
    }


def create_session(
    payload: Dict[str, Any],
    *,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        theme_id, card_ids = parse_request_payload(payload)
    except ValueError as exc:
        return {"status": False, "error": str(exc)}

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return _schema_error()

        preview = build_preview(cursor, theme_id, card_ids)
        expires_at = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)
        source_label = f"theme_{theme_id}_{len(card_ids)}_cards"
        cursor.execute(
            """
            INSERT INTO user_import_sessions
                (import_type, source_filename, preview_payload, created_by, created_by_name, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                IMPORT_TYPE,
                source_label,
                json.dumps(preview, ensure_ascii=False),
                created_by,
                created_by_name,
                expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        session_id = cursor.lastrowid
        conn.commit()
        cursor.execute("SELECT * FROM user_import_sessions WHERE id = %s", (session_id,))
        result = _serialize_session(cursor.fetchone())
        result["status"] = True
        return result
    except ValueError as exc:
        return {"status": False, "error": str(exc)}
    except Exception as exc:
        if conn:
            conn.rollback()
        return {"status": False, "error": str(exc)}
    finally:
        if conn:
            close_db_connection(conn)


def get_session(session_id: int) -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return None
        cursor.execute(
            """
            SELECT * FROM user_import_sessions
            WHERE id = %s AND import_type = %s AND expires_at > NOW()
            """,
            (session_id, IMPORT_TYPE),
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = _serialize_session(row)
        data["status"] = True
        return data
    finally:
        if conn:
            close_db_connection(conn)


def get_session_preview_for_commit(session_id: int) -> Dict[str, Any]:
    session = get_session(session_id)
    if not session:
        return {"status": False, "error": "Сессия не найдена или истекла"}
    preview = session.get("preview") or {}
    validation_error = validate_preview_for_commit(preview)
    if validation_error:
        return {"status": False, "error": validation_error}
    return {"status": True, "session": session, "preview": preview}
