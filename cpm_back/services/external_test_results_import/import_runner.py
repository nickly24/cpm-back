"""Фоновая запись результатов внешнего теста."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.external_test_results_import.preview import (
    build_preview_from_rows,
    validate_preview_for_commit,
)
from cpm_back.services.user_import.import_jobs import (
    _update_job,
    get_import_job,
    save_job_results,
)

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _load_preview_for_job(cursor, job: Dict[str, Any]) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT preview_payload FROM user_import_sessions WHERE id = %s
        """,
        (job["session_id"],),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("Сессия импорта не найдена")

    payload = row.get("preview_payload")
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    return payload


def _result_row(row: Dict[str, Any], status: str, message: str) -> Dict[str, Any]:
    return {
        "row": row.get("row"),
        "source_number": row.get("source_number"),
        "full_name": row.get("full_name"),
        "student_id": row.get("student_id"),
        "student_full_name": row.get("student_full_name"),
        "test_id": row.get("test_id"),
        "test_name": row.get("test_name"),
        "percent": row.get("percent"),
        "correct_count": row.get("correct_count"),
        "completed_at": row.get("completed_at"),
        "time_spent": row.get("time_spent"),
        "login": row.get("login"),
        "status": status,
        "message": message,
    }


def run_external_test_results_import(job_id: int, progress_callback: ProgressCallback = None) -> None:
    conn = None
    result_rows: List[Dict[str, Any]] = []
    successful = 0
    processed = 0

    try:
        job = get_import_job(job_id)
        if not job:
            return

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        stored_preview = _load_preview_for_job(cursor, job)
        raw_rows = stored_preview.get("source_rows") or stored_preview.get("rows") or []
        preview = build_preview_from_rows(cursor, raw_rows, stored_preview.get("test_id"))
        validation_error = validate_preview_for_commit(preview)
        if validation_error:
            error_rows = [
                _result_row(
                    {
                        **row,
                        "test_id": preview.get("test_id"),
                        "test_name": (preview.get("test") or {}).get("name"),
                    },
                    "error",
                    "; ".join(row.get("errors") or [validation_error]),
                )
                for row in preview.get("rows") or []
            ]
            save_job_results(job_id, error_rows)
            _update_job(
                job_id,
                status="failed",
                completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                processed_count=0,
                successful=0,
                skipped=0,
                failed=1,
                message=validation_error,
                errors=error_rows,
                summary=preview.get("summary"),
            )
            return

        test_id = int(preview["test_id"])
        test_name = (preview.get("test") or {}).get("name") or f"external_{test_id}"
        rows = preview.get("rows") or []
        total_rows = len(rows)

        def emit_progress(message: str) -> None:
            if progress_callback:
                progress_callback(
                    {
                        "processed_count": processed,
                        "successful": successful,
                        "skipped": 0,
                        "failed": 0,
                        "message": message,
                        "summary": preview.get("summary"),
                    }
                )

        for row in rows:
            processed += 1
            student_id = int(row["student_id"])
            cursor.execute(
                """
                SELECT id FROM test_sessions
                WHERE student_id = %s AND test_id = %s
                """,
                (student_id, test_id),
            )
            if cursor.fetchone():
                raise RuntimeError(
                    f"Строка {row.get('row')}: у студента уже есть результат по выбранному тесту"
                )

            cursor.execute(
                """
                INSERT INTO test_sessions (student_id, test_id, rate)
                VALUES (%s, %s, %s)
                """,
                (student_id, test_id, row.get("percent")),
            )
            successful += 1
            result_rows.append(
                _result_row(
                    {**row, "test_id": test_id, "test_name": test_name},
                    "imported",
                    "Результат загружен",
                )
            )
            emit_progress(f"Загрузка результатов: {processed}/{total_rows}")

        conn.commit()
        save_job_results(job_id, result_rows)
        _update_job(
            job_id,
            status="completed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            processed_count=processed,
            successful=successful,
            skipped=0,
            failed=0,
            message=f"Загружено результатов: {successful}",
            summary=preview.get("summary"),
        )
    except Exception as exc:
        if conn:
            conn.rollback()
        _update_job(
            job_id,
            status="failed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            processed_count=processed,
            successful=0,
            skipped=0,
            failed=1,
            message=f"Импорт отменён: {exc}",
        )
    finally:
        if conn:
            close_db_connection(conn)
