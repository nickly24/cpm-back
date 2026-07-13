"""Сборка preview импорта карточек."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

IMPORT_TYPE = "cards"

WARNING_DUPLICATE_IN_FILE = "duplicate_in_file"
WARNING_DUPLICATE_IN_SECTION = "duplicate_in_section"


def normalize_card_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _card_key(question: str, answer: str) -> Tuple[str, str]:
    return normalize_card_text(question), normalize_card_text(answer)


def _validate_theme(cursor, direction_id: int, theme_id: int) -> Optional[str]:
    cursor.execute("SELECT id, name FROM directions WHERE id = %s", (direction_id,))
    direction = cursor.fetchone()
    if not direction:
        return "Направление не найдено"

    cursor.execute(
        """
        SELECT id, name, direction_id
        FROM card_themes
        WHERE id = %s
        """,
        (theme_id,),
    )
    theme = cursor.fetchone()
    if not theme:
        return "Раздел не найден"
    if int(theme["direction_id"]) != int(direction_id):
        return "Раздел не относится к выбранному направлению"
    return None


def _load_existing_card_keys(cursor, theme_id: int) -> Set[Tuple[str, str]]:
    cursor.execute(
        """
        SELECT question, answer
        FROM cards
        WHERE theme_id = %s
        """,
        (theme_id,),
    )
    return {
        _card_key(row.get("question"), row.get("answer"))
        for row in cursor.fetchall()
    }


def _build_summary(cards: List[Dict[str, Any]]) -> Dict[str, int]:
    cards_create = sum(1 for card in cards if card.get("action") == "create")
    cards_warning = sum(1 for card in cards if card.get("action") == "warning")
    cards_skip = sum(1 for card in cards if card.get("action") == "skip")
    row_errors = sum(1 for card in cards if card.get("action") == "error")
    cards_to_import = cards_create + cards_warning
    return {
        "total_rows": len(cards),
        "cards_create": cards_create,
        "cards_warning": cards_warning,
        "cards_skip": cards_skip,
        "cards_to_import": cards_to_import,
        "row_errors": row_errors,
    }


def _resolve_warnings(
    question: str,
    answer: str,
    seen_in_file: Set[Tuple[str, str]],
    existing_keys: Set[Tuple[str, str]],
) -> List[str]:
    key = _card_key(question, answer)
    warnings: List[str] = []
    if key in seen_in_file:
        warnings.append(WARNING_DUPLICATE_IN_FILE)
    if key in existing_keys:
        warnings.append(WARNING_DUPLICATE_IN_SECTION)
    seen_in_file.add(key)
    return warnings


def _resolve_action(
    question: str,
    answer: str,
    *,
    forced_action: Optional[str] = None,
    warnings: List[str],
) -> str:
    if forced_action == "skip":
        return "skip"
    if not normalize_card_text(question) or not normalize_card_text(answer):
        return "error"
    if warnings:
        return "warning"
    return "create"


def _warning_messages(warnings: List[str]) -> Optional[str]:
    messages = []
    if WARNING_DUPLICATE_IN_FILE in warnings:
        messages.append("Дубликат в файле")
    if WARNING_DUPLICATE_IN_SECTION in warnings:
        messages.append("Уже есть в разделе")
    return "; ".join(messages) if messages else None


def build_preview_from_rows(
    cursor,
    raw_rows: List[Dict[str, Any]],
    *,
    direction_id: int,
    theme_id: int,
    preserved_cards: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    theme_error = _validate_theme(cursor, direction_id, theme_id)
    if theme_error:
        raise ValueError(theme_error)

    cursor.execute("SELECT id, name FROM directions WHERE id = %s", (direction_id,))
    direction = cursor.fetchone()
    cursor.execute("SELECT id, name FROM card_themes WHERE id = %s", (theme_id,))
    theme = cursor.fetchone()
    existing_keys = _load_existing_card_keys(cursor, theme_id)

    preserved_by_row = {
        int(item.get("row")): item
        for item in (preserved_cards or [])
        if item.get("row") is not None
    }

    cards: List[Dict[str, Any]] = []
    seen_in_file: Set[Tuple[str, str]] = set()

    for raw in raw_rows:
        row_no = int(raw.get("row"))
        preserved = preserved_by_row.get(row_no) or {}
        question = str(
            preserved.get("question")
            if preserved.get("question") is not None
            else raw.get("question") or ""
        )
        answer = str(
            preserved.get("answer")
            if preserved.get("answer") is not None
            else raw.get("answer") or ""
        )
        forced_action = preserved.get("action")

        warnings = _resolve_warnings(question, answer, seen_in_file, existing_keys)
        action = _resolve_action(
            question,
            answer,
            forced_action=forced_action,
            warnings=warnings,
        )

        errors: List[str] = []
        message = _warning_messages(warnings)
        if action == "error":
            if not normalize_card_text(question):
                errors.append("Пустой вопрос")
            if not normalize_card_text(answer):
                errors.append("Пустой ответ")
            message = "; ".join(errors) if errors else "Ошибка строки"

        cards.append(
            {
                "row": row_no,
                "question": question,
                "answer": answer,
                "action": action,
                "warnings": warnings,
                "errors": errors,
                "message": message,
            }
        )

    summary = _build_summary(cards)
    return {
        "direction_id": int(direction_id),
        "direction_name": direction.get("name"),
        "theme_id": int(theme_id),
        "theme_name": theme.get("name"),
        "cards": cards,
        "summary": summary,
        "source_rows": raw_rows,
    }


def rebuild_preview_from_cards(cursor, preview: Dict[str, Any]) -> Dict[str, Any]:
    raw_rows = preview.get("source_rows") or []
    if not raw_rows:
        raw_rows = [
            {
                "row": card.get("row"),
                "question": card.get("question"),
                "answer": card.get("answer"),
            }
            for card in preview.get("cards") or []
        ]
    return build_preview_from_rows(
        cursor,
        raw_rows,
        direction_id=int(preview["direction_id"]),
        theme_id=int(preview["theme_id"]),
        preserved_cards=preview.get("cards") or [],
    )


def validate_preview_for_commit(preview: Dict[str, Any]) -> Optional[str]:
    summary = preview.get("summary") or {}
    if int(summary.get("row_errors") or 0) > 0:
        return "Исправьте ошибки в строках перед загрузкой"
    cards = preview.get("cards") or []
    if not cards:
        return "Нет строк для загрузки"
    if int(summary.get("cards_to_import") or 0) <= 0:
        return "Нет карточек для импорта (все строки исключены)"
    return None
