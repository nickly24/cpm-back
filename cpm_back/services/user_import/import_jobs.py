"""Журнал и фоновый запуск импорта пользователей."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

_import_thread_lock = threading.Lock()
_active_import_job_id: Optional[int] = None


def _table_exists(cursor) -> bool:
    cursor.execute("SHOW TABLES LIKE 'user_import_jobs'")
    return cursor.fetchone() is not None


def recover_stale_user_import_jobs() -> None:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if not _table_exists(cursor):
            return
        cursor.execute(
            """
            UPDATE user_import_jobs
            SET status = 'failed',
                message = 'Прервано перезапуском сервера',
                completed_at = NOW()
            WHERE status IN ('queued', 'running', 'rolling_back')
            """
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
    finally:
        if conn:
            close_db_connection(conn)


def _progress_percent(row: Dict[str, Any]) -> int:
    total = int(row.get("total_rows") or 0)
    processed = int(row.get("processed_count") or 0)
    if total <= 0:
        return 100 if row.get("status") == "completed" else 0
    return min(100, int(round(processed * 100 / total)))


def _serialize_job(row: Dict[str, Any]) -> Dict[str, Any]:
    for field in ("errors", "entities_created", "summary"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field] = json.loads(value)
            except json.JSONDecodeError:
                row[field] = None

    return {
        "id": row["id"],
        "session_id": row.get("session_id"),
        "import_type": row.get("import_type") or "users",
        "status": row["status"],
        "created_by": row.get("created_by"),
        "created_by_name": row.get("created_by_name"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "completed_at": row["completed_at"].isoformat() if row.get("completed_at") else None,
        "total_rows": int(row.get("total_rows") or 0),
        "processed_count": int(row.get("processed_count") or 0),
        "successful": int(row.get("successful") or 0),
        "skipped": int(row.get("skipped") or 0),
        "failed": int(row.get("failed") or 0),
        "message": row.get("message"),
        "errors": row.get("errors"),
        "entities_created": row.get("entities_created"),
        "summary": row.get("summary"),
        "progress_percent": _progress_percent(row),
        "has_report": row.get("status") == "completed",
    }


def has_active_import_job() -> bool:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return False
        cursor.execute(
            """
            SELECT id FROM user_import_jobs
            WHERE status IN ('queued', 'running', 'rolling_back')
            LIMIT 1
            """
        )
        return cursor.fetchone() is not None
    finally:
        if conn:
            close_db_connection(conn)


def create_import_job(
    session_id: int,
    preview: Dict[str, Any],
    *,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
) -> Dict[str, Any]:
    if has_active_import_job():
        raise ValueError("Уже выполняется импорт. Дождитесь завершения.")

    summary = preview.get("summary") or {}
    total_rows = int(summary.get("total_rows") or 0)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            raise RuntimeError(
                "Таблица user_import_jobs не найдена. Примените миграцию 009_user_import.sql"
            )

        cursor.execute(
            """
            INSERT INTO user_import_jobs
                (session_id, import_type, status, created_by, created_by_name,
                 total_rows, summary, message)
            VALUES (%s, 'users', 'queued', %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                created_by,
                created_by_name,
                total_rows,
                json.dumps(summary, ensure_ascii=False),
                "Ожидает запуска",
            ),
        )
        job_id = cursor.lastrowid
        conn.commit()
        cursor.execute("SELECT * FROM user_import_jobs WHERE id = %s", (job_id,))
        return _serialize_job(cursor.fetchone())
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            close_db_connection(conn)


def get_import_job(job_id: int) -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return None
        cursor.execute("SELECT * FROM user_import_jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
        return _serialize_job(row) if row else None
    finally:
        if conn:
            close_db_connection(conn)


def list_import_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return []
        cursor.execute(
            """
            SELECT * FROM user_import_jobs
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [_serialize_job(row) for row in cursor.fetchall()]
    finally:
        if conn:
            close_db_connection(conn)


def get_active_import_job_id() -> Optional[int]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return None
        cursor.execute(
            """
            SELECT id FROM user_import_jobs
            WHERE status IN ('queued', 'running', 'rolling_back')
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        return row["id"] if row else None
    finally:
        if conn:
            close_db_connection(conn)


def _update_job(job_id: int, **fields) -> None:
    if not fields:
        return

    columns = []
    values = []
    for key, value in fields.items():
        columns.append(f"{key} = %s")
        if key in ("errors", "entities_created", "summary") and value is not None and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        values.append(value)
    values.append(job_id)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE user_import_jobs SET {', '.join(columns)} WHERE id = %s",
            tuple(values),
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            close_db_connection(conn)


def _mark_running(job_id: int) -> bool:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE user_import_jobs
            SET status = 'running', started_at = NOW(), message = %s
            WHERE id = %s AND status = 'queued'
            """,
            ("Импорт выполняется", job_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            close_db_connection(conn)


def save_job_results(job_id: int, rows_data: List[Dict[str, Any]]) -> None:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO user_import_job_results (job_id, rows_data)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE rows_data = VALUES(rows_data)
            """,
            (job_id, json.dumps(rows_data, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            close_db_connection(conn)


def get_job_report(job_id: int) -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SHOW TABLES LIKE 'user_import_job_results'")
        if not cursor.fetchone():
            return None

        cursor.execute(
            """
            SELECT j.id, j.status, j.summary, j.successful, j.skipped, j.failed, r.rows_data
            FROM user_import_jobs j
            LEFT JOIN user_import_job_results r ON r.job_id = j.id
            WHERE j.id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        rows_data = row.get("rows_data")
        if isinstance(rows_data, str):
            rows_data = json.loads(rows_data)

        summary = row.get("summary")
        if isinstance(summary, str):
            summary = json.loads(summary)

        return {
            "job_id": row["id"],
            "status": row["status"],
            "summary": summary,
            "successful": int(row.get("successful") or 0),
            "skipped": int(row.get("skipped") or 0),
            "failed": int(row.get("failed") or 0),
            "rows": rows_data or [],
        }
    finally:
        if conn:
            close_db_connection(conn)


def _make_progress_callback(job_id: int):
    def callback(snapshot: Dict[str, Any]) -> None:
        _update_job(job_id, **snapshot)

    return callback


def _run_import_job(job_id: int) -> None:
    global _active_import_job_id

    try:
        job = get_import_job(job_id)
        if not job or not _mark_running(job_id):
            return

        from .import_runner import run_users_import

        run_users_import(job_id, progress_callback=_make_progress_callback(job_id))
    finally:
        with _import_thread_lock:
            if _active_import_job_id == job_id:
                _active_import_job_id = None


def enqueue_import_job(job_id: int) -> None:
    global _active_import_job_id

    def _target():
        _run_import_job(job_id)

    with _import_thread_lock:
        if _active_import_job_id is not None:
            return
        _active_import_job_id = job_id

    thread = threading.Thread(target=_target, name=f"user-import-{job_id}", daemon=True)
    thread.start()
