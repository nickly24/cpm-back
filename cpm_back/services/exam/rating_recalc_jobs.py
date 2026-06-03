"""
Журнал и фоновый запуск пересчёта рейтингов.
"""
import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from cpm_back.db.mongo import get_mongo_db
from cpm_back.db.mysql_pool import close_db_connection, get_db_connection

_recalc_thread_lock = threading.Lock()
_active_recalc_job_id: Optional[int] = None


def _table_exists(cursor) -> bool:
    cursor.execute("SHOW TABLES LIKE 'rating_recalc_jobs'")
    return cursor.fetchone() is not None


def recover_stale_rating_jobs() -> None:
    """Помечает зависшие задачи после перезапуска сервера."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if not _table_exists(cursor):
            return
        cursor.execute(
            """
            UPDATE rating_recalc_jobs
            SET status = 'failed',
                message = 'Прервано перезапуском сервера',
                completed_at = NOW()
            WHERE status IN ('queued', 'running')
            """
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
    finally:
        if conn:
            close_db_connection(conn)


def _serialize_job(row: Dict[str, Any]) -> Dict[str, Any]:
    errors = row.get("errors")
    if isinstance(errors, str):
        try:
            errors = json.loads(errors)
        except json.JSONDecodeError:
            errors = None
    date_from = row.get("date_from")
    date_to = row.get("date_to")
    return {
        "id": row["id"],
        "status": row["status"],
        "date_from": date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from),
        "date_to": date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to),
        "created_by": row.get("created_by"),
        "created_by_name": row.get("created_by_name"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "completed_at": row["completed_at"].isoformat() if row.get("completed_at") else None,
        "total_students": int(row.get("total_students") or 0),
        "processed_count": int(row.get("processed_count") or 0),
        "successful": int(row.get("successful") or 0),
        "failed": int(row.get("failed") or 0),
        "skipped": int(row.get("skipped") or 0),
        "message": row.get("message"),
        "errors": errors,
        "progress_percent": _progress_percent(row),
    }


def _progress_percent(row: Dict[str, Any]) -> int:
    total = int(row.get("total_students") or 0)
    processed = int(row.get("processed_count") or 0)
    if total <= 0:
        if row.get("status") == "completed":
            return 100
        return 0
    return min(100, int(round(processed * 100 / total)))


def has_active_recalc_job() -> bool:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return False
        cursor.execute(
            """
            SELECT id FROM rating_recalc_jobs
            WHERE status IN ('queued', 'running')
            LIMIT 1
            """
        )
        return cursor.fetchone() is not None
    finally:
        if conn:
            close_db_connection(conn)


def create_recalc_job(
    date_from: str,
    date_to: str,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
) -> Dict[str, Any]:
    if has_active_recalc_job():
        raise ValueError("Уже выполняется пересчёт рейтинга. Дождитесь завершения или проверьте журнал.")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            raise RuntimeError(
                "Таблица rating_recalc_jobs не найдена. Примените миграцию 006_rating_recalc_jobs.sql"
            )
        cursor.execute(
            """
            INSERT INTO rating_recalc_jobs
                (status, date_from, date_to, created_by, created_by_name, message)
            VALUES ('queued', %s, %s, %s, %s, %s)
            """,
            (date_from, date_to, created_by, created_by_name, "Ожидает запуска"),
        )
        job_id = cursor.lastrowid
        conn.commit()
        cursor.execute("SELECT * FROM rating_recalc_jobs WHERE id = %s", (job_id,))
        job = cursor.fetchone()
        return _serialize_job(job)
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            close_db_connection(conn)


def get_recalc_job(job_id: int) -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return None
        cursor.execute("SELECT * FROM rating_recalc_jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
        return _serialize_job(row) if row else None
    finally:
        if conn:
            close_db_connection(conn)


def list_recalc_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if not _table_exists(cursor):
            return []
        cursor.execute(
            """
            SELECT * FROM rating_recalc_jobs
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [_serialize_job(row) for row in cursor.fetchall()]
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
        if key == "errors" and value is not None and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        values.append(value)
    values.append(job_id)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE rating_recalc_jobs SET {', '.join(columns)} WHERE id = %s",
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
            UPDATE rating_recalc_jobs
            SET status = 'running', started_at = NOW(), message = %s
            WHERE id = %s AND status = 'queued'
            """,
            ("Пересчёт выполняется", job_id),
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


def _make_progress_callback(job_id: int):
    def callback(snapshot: Dict[str, Any]) -> None:
        _update_job(
            job_id,
            total_students=snapshot.get("total_students", 0),
            processed_count=snapshot.get("processed_count", 0),
            successful=snapshot.get("successful", 0),
            failed=snapshot.get("failed", 0),
            skipped=snapshot.get("skipped", 0),
            message=snapshot.get("message") or "Пересчёт выполняется",
        )

    return callback


def _run_recalc_job(job_id: int) -> None:
    global _active_recalc_job_id
    mysql_conn = None
    try:
        job = get_recalc_job(job_id)
        if not job:
            return
        if not _mark_running(job_id):
            return

        from cpm_back.services.exam.save_ratings import save_all_ratings

        mysql_conn = get_db_connection()
        mongo_db = get_mongo_db()
        results = save_all_ratings(
            mysql_conn,
            mongo_db,
            job["date_from"],
            job["date_to"],
            progress_callback=_make_progress_callback(job_id),
        )

        if results.get("clear_error"):
            _update_job(
                job_id,
                status="failed",
                completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                message=" | ".join(results.get("errors") or ["Ошибка очистки данных"]),
                errors=results.get("errors"),
                total_students=0,
                processed_count=0,
            )
            return

        message_parts = [
            f"Обработано студентов: {results['successful']}/{results['total_students']}",
        ]
        if results.get("skipped", 0) > 0:
            message_parts.append(f"Пропущено: {results['skipped']}")
        if results.get("failed", 0) > 0:
            message_parts.append(f"Ошибок: {results['failed']}")

        _update_job(
            job_id,
            status="completed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            total_students=results.get("total_students", 0),
            processed_count=results.get("total_students", 0),
            successful=results.get("successful", 0),
            failed=results.get("failed", 0),
            skipped=results.get("skipped", 0),
            message=" | ".join(message_parts),
            errors=results.get("errors") or None,
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            message=str(exc),
        )
    finally:
        if mysql_conn:
            close_db_connection(mysql_conn)
        with _recalc_thread_lock:
            if _active_recalc_job_id == job_id:
                _active_recalc_job_id = None


def enqueue_recalc_job(job_id: int) -> None:
    global _active_recalc_job_id

    def _target():
        _run_recalc_job(job_id)

    with _recalc_thread_lock:
        if _active_recalc_job_id is not None:
            return
        _active_recalc_job_id = job_id

    thread = threading.Thread(target=_target, name=f"rating-recalc-{job_id}", daemon=True)
    thread.start()
