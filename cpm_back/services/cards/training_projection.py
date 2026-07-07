"""
Проекция тестовых вопросов в карточки для тренировок.
"""
import hashlib
import json


def normalize_text(value):
    return (value or "").strip()


def content_fingerprint(question, answer):
    payload = json.dumps(
        {"q": normalize_text(question), "a": normalize_text(answer)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def manual_card_ref(card_id):
    return f"card:{card_id}"


def test_card_ref(test_id, question_id):
    return f"test:{test_id}:{question_id}"


def parse_card_ref(card_ref):
    if not card_ref:
        return None
    if card_ref.startswith("card:"):
        try:
            return {"kind": "manual", "card_id": int(card_ref[5:])}
        except ValueError:
            return None
    if card_ref.startswith("test:"):
        parts = card_ref.split(":")
        if len(parts) != 3:
            return None
        try:
            return {
                "kind": "test",
                "test_id": parts[1],
                "question_id": int(parts[2]),
            }
        except ValueError:
            return None
    return None


def extract_answer_from_question(question):
    q_type = question.get("type")
    if q_type == "single":
        for answer in question.get("answers") or []:
            if answer.get("isCorrect"):
                return normalize_text(answer.get("text"))
        return ""
    if q_type == "multiple":
        parts = [
            normalize_text(answer.get("text"))
            for answer in (question.get("answers") or [])
            if answer.get("isCorrect")
        ]
        return "; ".join(part for part in parts if part)
    if q_type == "text":
        parts = [
            normalize_text(value) for value in (question.get("correctAnswers") or [])
        ]
        return " / ".join(part for part in parts if part)
    return ""


def project_manual_card(card_row):
    question = card_row.get("question", "")
    answer = card_row.get("answer", "")
    card_id = card_row["id"]
    return {
        "card_ref": manual_card_ref(card_id),
        "card_id": card_id,
        "question": question,
        "answer": answer,
        "sort_order": int(card_row.get("sort_order") or 0),
        "content_fingerprint": content_fingerprint(question, answer),
    }


def project_test_question(test_id, question):
    question_text = question.get("text", "")
    answer_text = extract_answer_from_question(question)
    question_id = question.get("questionId")
    return {
        "card_ref": test_card_ref(test_id, question_id),
        "question_id": question_id,
        "question": question_text,
        "answer": answer_text,
        "sort_order": int(question_id or 0),
        "content_fingerprint": content_fingerprint(question_text, answer_text),
    }


def test_allows_training(test_doc):
    """Раздел из теста доступен только при visible=true (ответы не скрыты)."""
    if not test_doc:
        return False
    if test_doc.get("published") is False:
        return False
    return bool(test_doc.get("visible"))


def get_test_document_for_training(test_id):
    from cpm_back.services.exam.test_definition_cache import get_test_document_cached

    test = get_test_document_cached(test_id)
    if not test:
        return None, "test_not_found"
    if not test_allows_training(test):
        return None, "answers_hidden"
    return test, None


def get_test_training_cards(test_id):
    test, err = get_test_document_for_training(test_id)
    if err:
        return None, err

    cards = []
    questions = sorted(
        test.get("questions") or [],
        key=lambda item: item.get("questionId") or 0,
    )
    for question in questions:
        projected = project_test_question(test_id, question)
        if projected["question"] and projected["answer"]:
            cards.append(projected)
    return cards, None


def get_visible_training_tests():
    """Опубликованные тесты с visible=true для дерева тренировок."""
    from cpm_back.services.exam.exam_memory_cache import get_published_tests_light_cached

    items = get_published_tests_light_cached()
    return [item for item in items if item.get("visible")]
