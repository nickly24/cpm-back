"""Фоновая трансформация карточек в test draft."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db
from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.card_transform.to_draft_canvas import build_draft_payload
from cpm_back.services.exam.test_drafts import create_test_draft
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
        raise RuntimeError("Сессия трансформации не найдена")

    payload = row.get("preview_payload")
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    return payload


def _delete_draft_force(draft_id: str) -> None:
    try:
        get_mongo_db().test_drafts.delete_one({"_id": ObjectId(draft_id), "status": "active"})
    except Exception:
        pass


def _result_row(
    card: Dict[str, Any],
    preview: Dict[str, Any],
    *,
    status: str,
    message: str,
    draft_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "row": card.get("sort_order") or card.get("id"),
        "card_id": card.get("id"),
        "question": card.get("question"),
        "answer": card.get("answer"),
        "theme_id": preview.get("theme_id"),
        "theme_name": preview.get("theme_name"),
        "direction_name": preview.get("direction_name"),
        "draft_id": draft_id,
        "status": status,
        "message": message,
    }


def run_cards_to_draft_transform(job_id: int, progress_callback: ProgressCallback = None) -> None:
    conn = None
    draft_id: Optional[str] = None
    result_rows: List[Dict[str, Any]] = []
    processed = 0
    preview: Dict[str, Any] = {}

    try:
        job = get_import_job(job_id)
        if not job:
            return

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        preview = _load_preview_for_job(cursor, job)
        cards = preview.get("cards") or []
        total_rows = len(cards)

        def emit_progress(message: str) -> None:
            if progress_callback:
                progress_callback(
                    {
                        "processed_count": processed,
                        "successful": 0,
                        "skipped": 0,
                        "failed": 0,
                        "message": message,
                        "summary": preview.get("summary"),
                    }
                )

        emit_progress("Создание драфта…")
        draft_payload = build_draft_payload(preview)
        draft = create_test_draft(draft_payload)
        if not draft or not draft.get("id"):
            raise RuntimeError("Не удалось создать драфт")

        draft_id = str(draft["id"])
        processed = total_rows

        for card in cards:
            result_rows.append(
                _result_row(
                    card,
                    preview,
                    status="transformed",
                    message="Добавлено в драфт",
                    draft_id=draft_id,
                )
            )

        save_job_results(job_id, result_rows)
        summary = dict(preview.get("summary") or {})
        summary["draft_id"] = draft_id
        summary["draft_title"] = draft_payload.get("title")
        _update_job(
            job_id,
            status="completed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            processed_count=processed,
            successful=total_rows,
            skipped=0,
            failed=0,
            message=f"Драфт создан: {draft_payload.get('title')}",
            entities_created={"draft_id": draft_id},
            summary=summary,
        )
    except Exception as exc:
        if draft_id:
            _delete_draft_force(draft_id)
        if result_rows:
            save_job_results(job_id, result_rows)
        _update_job(
            job_id,
            status="failed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            processed_count=processed,
            successful=0,
            skipped=0,
            failed=1,
            message=f"Трансформация отменена: {exc}",
            entities_created={"draft_id": draft_id} if draft_id else None,
            summary=preview.get("summary"),
        )
    finally:
        if conn:
            close_db_connection(conn)
