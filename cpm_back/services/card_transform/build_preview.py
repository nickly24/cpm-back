"""Сборка preview для трансформации карточек в драфт."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

IMPORT_TYPE = "cards_to_draft"


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _parse_id_list(card_ids: Any) -> Optional[List[int]]:
    if not isinstance(card_ids, list) or not card_ids:
        return None
    result: List[int] = []
    for item in card_ids:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return None
    return result


def _load_theme(cursor, theme_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT ct.id, ct.name, ct.direction_id, d.name AS direction_name
        FROM card_themes ct
        JOIN directions d ON d.id = ct.direction_id
        WHERE ct.id = %s
        """,
        (theme_id,),
    )
    return cursor.fetchone()


def _load_cards(cursor, theme_id: int, card_ids: List[int]) -> List[Dict[str, Any]]:
    if not card_ids:
        return []
    placeholders = ",".join(["%s"] * len(card_ids))
    cursor.execute(
        f"""
        SELECT id, question, answer, theme_id, sort_order
        FROM cards
        WHERE theme_id = %s AND id IN ({placeholders})
        ORDER BY sort_order, id
        """,
        (theme_id, *card_ids),
    )
    rows = cursor.fetchall()
    order_map = {card_id: index for index, card_id in enumerate(card_ids)}
    return sorted(rows, key=lambda row: order_map.get(row["id"], row["id"]))


def build_preview(cursor, theme_id: int, card_ids: List[int]) -> Dict[str, Any]:
    theme = _load_theme(cursor, theme_id)
    if not theme:
        raise ValueError("Раздел не найден")

    cards = _load_cards(cursor, theme_id, card_ids)
    if len(cards) != len(set(card_ids)):
        raise ValueError("Некоторые карточки не найдены в выбранном разделе")

    errors: List[str] = []
    for card in cards:
        if not _normalize_text(card.get("question")):
            errors.append(f"Карточка #{card['id']}: пустой вопрос")
        if not _normalize_text(card.get("answer")):
            errors.append(f"Карточка #{card['id']}: пустой ответ")
    if errors:
        raise ValueError("; ".join(errors))

    theme_name = theme.get("name") or "Раздел"
    direction_name = theme.get("direction_name") or ""

    return {
        "theme_id": int(theme_id),
        "theme_name": theme_name,
        "direction_id": int(theme["direction_id"]),
        "direction_name": direction_name,
        "card_ids": [int(card["id"]) for card in cards],
        "cards": [
            {
                "id": int(card["id"]),
                "question": card.get("question") or "",
                "answer": card.get("answer") or "",
                "sort_order": int(card.get("sort_order") or 0),
            }
            for card in cards
        ],
        "draft_defaults": {
            "title": f"{theme_name} — тест",
            "direction": direction_name,
        },
        "summary": {
            "total_rows": len(cards),
            "cards_selected": len(cards),
            "row_errors": 0,
        },
    }


def validate_preview_for_commit(preview: Dict[str, Any]) -> Optional[str]:
    summary = preview.get("summary") or {}
    if int(summary.get("row_errors") or 0) > 0:
        return "Исправьте ошибки перед трансформацией"
    cards = preview.get("cards") or []
    if not cards:
        return "Выберите хотя бы одну карточку"
    if not preview.get("draft_defaults", {}).get("direction"):
        return "Не удалось определить направление раздела"
    return None


def parse_request_payload(payload: Dict[str, Any]) -> tuple[int, List[int]]:
    try:
        theme_id = int(payload.get("theme_id"))
    except (TypeError, ValueError):
        raise ValueError("theme_id обязателен")

    card_ids = _parse_id_list(payload.get("card_ids"))
    if not card_ids:
        raise ValueError("Выберите хотя бы одну карточку")

    return theme_id, card_ids
