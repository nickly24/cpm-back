"""Конвертация manual-карточек в canvas драфта."""
from __future__ import annotations

from typing import Any, Dict, List


def card_to_canvas_question(card: Dict[str, Any]) -> Dict[str, Any]:
    card_id = card.get("id")
    qid = f"q_card_{card_id}"
    return {
        "id": qid,
        "type": "text",
        "text": card.get("question") or "",
        "points": 1,
        "answers": [
            {
                "id": f"{qid}_text_1",
                "kind": "textAnswer",
                "text": card.get("answer") or "",
                "isCorrect": True,
            }
        ],
    }


def build_canvas_from_cards(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    questions = [card_to_canvas_question(card) for card in cards]
    layout: Dict[str, Dict[str, int]] = {}
    for index, question in enumerate(questions):
        row = index // 3
        col = index % 3
        layout[question["id"]] = {"x": 120 + col * 360, "y": 120 + row * 260}
    return {"questions": questions, "layout": layout}


def build_draft_payload(preview: Dict[str, Any]) -> Dict[str, Any]:
    defaults = preview.get("draft_defaults") or {}
    cards = preview.get("cards") or []
    return {
        "title": defaults.get("title") or "Новый драфт теста",
        "direction": defaults.get("direction") or "",
        "startDate": "",
        "endDate": "",
        "timeLimitMinutes": 30,
        "published": True,
        "visible": False,
        "canvas": build_canvas_from_cards(cards),
        "source": {
            "kind": "manual_cards",
            "themeId": preview.get("theme_id"),
            "themeName": preview.get("theme_name"),
            "cardIds": preview.get("card_ids") or [],
        },
    }
