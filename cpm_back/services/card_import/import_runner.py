"""Фоновый импорт manual-карточек."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.cards.training_projection import manual_card_ref
from cpm_back.services.cards.training_progress import delete_progress_for_card_refs

from .build_preview import rebuild_preview_from_cards, validate_preview_for_commit
from cpm_back.services.user_import.import_jobs import (
    _update_job,
    get_import_job,
    save_job_results,
)

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _empty_entities() -> Dict[str, Any]:
    return {"card_ids_created": [], "theme_id_created": None}


def _resolve_or_create_theme(cursor, preview: Dict[str, Any]) -> Dict[str, Any]:
    """Возвращает theme_id / theme_name и флаг, создан ли раздел этим job."""
    direction_id = int(preview["direction_id"])
    if preview.get("create_new_theme"):
        theme_name = str(preview.get("new_theme_name") or "").strip()
        if not theme_name:
            raise RuntimeError("Не указано название нового раздела")

        cursor.execute(
            """
            SELECT id, name FROM card_themes
            WHERE direction_id = %s AND name = %s
            """,
            (direction_id, theme_name),
        )
        existing = cursor.fetchone()
        if existing:
            return {
                "theme_id": int(existing["id"]),
                "theme_name": existing.get("name") or theme_name,
                "theme_created": False,
            }

        cursor.execute(
            "INSERT INTO card_themes (name, direction_id) VALUES (%s, %s)",
            (theme_name, direction_id),
        )
        return {
            "theme_id": int(cursor.lastrowid),
            "theme_name": theme_name,
            "theme_created": True,
        }

    theme_id = preview.get("theme_id")
    if theme_id is None:
        raise RuntimeError("Не выбран раздел для импорта")
    return {
        "theme_id": int(theme_id),
        "theme_name": preview.get("theme_name"),
        "theme_created": False,
    }


def _rollback_theme(cursor, theme_id: Optional[int]) -> None:
    if not theme_id:
        return
    cursor.execute("SELECT COUNT(*) AS cnt FROM cards WHERE theme_id = %s", (theme_id,))
    count = int((cursor.fetchone() or {}).get("cnt") or 0)
    if count == 0:
        cursor.execute("DELETE FROM card_themes WHERE id = %s", (theme_id,))


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


def _rollback_cards(cursor, card_ids: List[int]) -> None:
    if not card_ids:
        return
    refs = [manual_card_ref(card_id) for card_id in card_ids]
    delete_progress_for_card_refs(cursor, refs)
    placeholders = ",".join(["%s"] * len(card_ids))
    cursor.execute(
        f"DELETE FROM cards WHERE id IN ({placeholders})",
        tuple(card_ids),
    )


def _result_row(card: Dict[str, Any], status: str, message: str, card_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "row": card.get("row"),
        "question": card.get("question"),
        "answer": card.get("answer"),
        "theme_id": card.get("theme_id"),
        "theme_name": card.get("theme_name"),
        "direction_name": card.get("direction_name"),
        "card_id": card_id,
        "status": status,
        "message": message,
    }


def run_cards_import(job_id: int, progress_callback: ProgressCallback = None) -> None:
    conn = None
    entities = _empty_entities()
    result_rows: List[Dict[str, Any]] = []
    successful = 0
    skipped = 0
    processed = 0

    try:
        job = get_import_job(job_id)
        if not job:
            return

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        stored_preview = _load_preview_for_job(cursor, job)
        preview = rebuild_preview_from_cards(cursor, stored_preview)
        validation_error = validate_preview_for_commit(preview)
        if validation_error:
            save_job_results(job_id, [])
            _update_job(
                job_id,
                status="failed",
                completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                processed_count=0,
                successful=0,
                skipped=0,
                failed=1,
                message=validation_error,
                summary=preview.get("summary"),
            )
            return

        resolved = _resolve_or_create_theme(cursor, preview)
        theme_id = int(resolved["theme_id"])
        theme_name = resolved.get("theme_name") or preview.get("theme_name")
        if resolved.get("theme_created"):
            entities["theme_id_created"] = theme_id
            conn.commit()

        direction_name = preview.get("direction_name")
        cards = preview.get("cards") or []
        total_rows = len(cards)

        cursor.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS max_order FROM cards WHERE theme_id = %s",
            (theme_id,),
        )
        sort_order = int((cursor.fetchone() or {}).get("max_order") or 0)

        def emit_progress(message: str) -> None:
            if progress_callback:
                progress_callback(
                    {
                        "processed_count": processed,
                        "successful": successful,
                        "skipped": skipped,
                        "failed": 0,
                        "message": message,
                        "entities_created": entities,
                        "summary": preview.get("summary"),
                    }
                )

        for card in cards:
            processed += 1
            row_context = {
                **card,
                "theme_id": theme_id,
                "theme_name": theme_name,
                "direction_name": direction_name,
            }

            if card.get("action") == "skip":
                skipped += 1
                result_rows.append(
                    _result_row(row_context, "skipped", "Исключено из импорта")
                )
                emit_progress(f"Импорт карточек: {processed}/{total_rows}")
                continue

            if card.get("action") == "error":
                raise RuntimeError(
                    f"Строка {card.get('row')}: "
                    f"{'; '.join(card.get('errors') or ['ошибка валидации'])}"
                )

            sort_order += 1
            cursor.execute(
                """
                INSERT INTO cards (question, answer, theme_id, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    str(card.get("question") or "").strip(),
                    str(card.get("answer") or "").strip(),
                    theme_id,
                    sort_order,
                ),
            )
            card_id = cursor.lastrowid
            entities["card_ids_created"].append(card_id)
            conn.commit()

            successful += 1
            message = "Карточка создана"
            if card.get("action") == "warning":
                message = card.get("message") or "Создана с предупреждением"
            result_rows.append(
                _result_row(row_context, "created", message, card_id=card_id)
            )
            emit_progress(f"Импорт карточек: {processed}/{total_rows}")

        save_job_results(job_id, result_rows)
        summary = dict(preview.get("summary") or {})
        summary["theme_id"] = theme_id
        summary["theme_name"] = theme_name
        summary["theme_created"] = bool(entities.get("theme_id_created"))
        _update_job(
            job_id,
            status="completed",
            completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            processed_count=processed,
            successful=successful,
            skipped=skipped,
            failed=0,
            message=(
                f"Создано карточек: {successful}, пропущено: {skipped}"
                + (f", раздел: {theme_name}" if theme_name else "")
            ),
            entities_created=entities,
            summary=summary,
        )
    except Exception as exc:
        if conn:
            try:
                cursor = conn.cursor(dictionary=True)
                _update_job(job_id, status="rolling_back", message=f"Откат: {exc}")
                _rollback_cards(cursor, entities.get("card_ids_created") or [])
                _rollback_theme(cursor, entities.get("theme_id_created"))
                conn.commit()
            except Exception as rollback_exc:
                conn.rollback()
                _update_job(
                    job_id,
                    status="failed",
                    completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    message=f"Ошибка: {exc}. Откат не завершён: {rollback_exc}",
                    entities_created=entities,
                    processed_count=processed,
                    successful=0,
                    skipped=skipped,
                    failed=1,
                )
            else:
                _update_job(
                    job_id,
                    status="failed",
                    completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    message=f"Импорт отменён: {exc}",
                    entities_created=entities,
                    processed_count=processed,
                    successful=0,
                    skipped=skipped,
                    failed=1,
                )
        else:
            _update_job(
                job_id,
                status="failed",
                completed_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                message=str(exc),
            )
    finally:
        if conn:
            close_db_connection(conn)
