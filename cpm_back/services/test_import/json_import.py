from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from cpm_back.services.exam.create_test import create_test
from cpm_back.services.exam.get_directions import get_directions

SUPPORTED_TYPES = {"single", "multiple", "text"}
DATE_FORMAT = "%Y-%m-%dT%H:%M"


def _error(path: str, message: str) -> Dict[str, str]:
    return {"path": path, "message": message}


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_date(value: Any, path: str, errors: List[Dict[str, str]]) -> str:
    text = _as_str(value)
    if not text:
        errors.append(_error(path, "Поле обязательно"))
        return ""
    try:
        datetime.strptime(text, DATE_FORMAT)
    except ValueError:
        errors.append(_error(path, "Используйте формат YYYY-MM-DDTHH:mm"))
    return text


def _parse_bool(value: Any, path: str, default: bool, errors: List[Dict[str, str]]) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    errors.append(_error(path, "Значение должно быть boolean: true или false"))
    return default


def _direction_names() -> set[str]:
    names = set()
    for item in get_directions() or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            names.add(str(name))
    return names


def _normalize_answer(
    answer: Any,
    path: str,
    seen_ids: set[str],
    errors: List[Dict[str, str]],
) -> Dict[str, Any]:
    if not isinstance(answer, dict):
        errors.append(_error(path, "Вариант ответа должен быть объектом"))
        return {"id": "", "text": "", "isCorrect": False}

    answer_id = _as_str(answer.get("id"))
    text = _as_str(answer.get("text"))
    raw_is_correct = answer.get("isCorrect")
    if not isinstance(raw_is_correct, bool):
        errors.append(_error(f"{path}.isCorrect", "isCorrect должен быть boolean"))
        is_correct = False
    else:
        is_correct = raw_is_correct

    if not answer_id:
        errors.append(_error(f"{path}.id", "ID варианта обязателен"))
    elif answer_id in seen_ids:
        errors.append(_error(f"{path}.id", f"ID варианта '{answer_id}' повторяется"))
    seen_ids.add(answer_id)

    if not text:
        errors.append(_error(f"{path}.text", "Текст варианта обязателен"))

    return {"id": answer_id, "text": text, "isCorrect": is_correct}


def _normalize_question(
    question: Any,
    index: int,
    seen_question_ids: set[int],
    errors: List[Dict[str, str]],
) -> Dict[str, Any]:
    path = f"questions[{index}]"
    if not isinstance(question, dict):
        errors.append(_error(path, "Вопрос должен быть объектом"))
        return {
            "questionId": index + 1,
            "type": "single",
            "text": "",
            "points": 1,
            "answers": [],
            "correctAnswers": [],
        }

    raw_qid = question.get("questionId")
    if isinstance(raw_qid, bool) or not isinstance(raw_qid, int) or raw_qid <= 0:
        errors.append(_error(f"{path}.questionId", "questionId должен быть целым числом больше 0"))
        question_id = index + 1
    else:
        question_id = raw_qid
        if question_id in seen_question_ids:
            errors.append(_error(f"{path}.questionId", f"questionId {question_id} повторяется"))
        seen_question_ids.add(question_id)

    q_type = _as_str(question.get("type"))
    if q_type not in SUPPORTED_TYPES:
        errors.append(_error(f"{path}.type", "Тип должен быть single, multiple или text"))
        q_type = "single"

    text = _as_str(question.get("text"))
    if not text:
        errors.append(_error(f"{path}.text", "Текст вопроса обязателен"))

    raw_points = question.get("points")
    if isinstance(raw_points, bool) or not isinstance(raw_points, int) or raw_points <= 0:
        errors.append(_error(f"{path}.points", "Баллы должны быть целым числом больше 0"))
        points = 1
    else:
        points = raw_points

    normalized = {
        "questionId": question_id,
        "type": q_type,
        "text": text,
        "points": points,
        "answers": [],
        "correctAnswers": [],
    }

    if q_type in ("single", "multiple"):
        answers = question.get("answers")
        if not isinstance(answers, list):
            errors.append(_error(f"{path}.answers", "answers должен быть массивом"))
            answers = []
        if len(answers) < 2:
            errors.append(_error(f"{path}.answers", "Добавьте минимум 2 варианта ответа"))

        seen_answer_ids: set[str] = set()
        normalized_answers = [
            _normalize_answer(answer, f"{path}.answers[{answer_index}]", seen_answer_ids, errors)
            for answer_index, answer in enumerate(answers)
        ]
        correct_count = sum(1 for answer in normalized_answers if answer.get("isCorrect"))
        if q_type == "single" and correct_count != 1:
            errors.append(_error(f"{path}.answers", "Для single должен быть ровно один правильный ответ"))
        if q_type == "multiple" and correct_count < 1:
            errors.append(_error(f"{path}.answers", "Для multiple нужен минимум один правильный ответ"))
        normalized["answers"] = normalized_answers
        normalized.pop("correctAnswers", None)
    else:
        raw_answers = question.get("answers", [])
        if raw_answers not in (None, []):
            errors.append(_error(f"{path}.answers", "Для text поле answers должно быть пустым"))
        correct_answers = question.get("correctAnswers")
        if not isinstance(correct_answers, list):
            errors.append(_error(f"{path}.correctAnswers", "correctAnswers должен быть массивом"))
            correct_answers = []
        normalized_correct = [
            _as_str(answer)
            for answer in correct_answers
            if _as_str(answer)
        ]
        if not normalized_correct:
            errors.append(_error(f"{path}.correctAnswers", "Добавьте минимум один правильный ответ"))
        normalized["answers"] = []
        normalized["correctAnswers"] = normalized_correct

    return normalized


def normalize_test_payload(payload: Any) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not isinstance(payload, dict):
        return {}, [_error("$", "JSON должен быть объектом теста")]

    title = _as_str(payload.get("title"))
    if not title:
        errors.append(_error("title", "Название теста обязательно"))

    direction = _as_str(payload.get("direction"))
    direction_names = _direction_names()
    if not direction:
        errors.append(_error("direction", "Направление обязательно"))
    elif direction not in direction_names:
        errors.append(_error("direction", f"Направление '{direction}' не найдено"))

    start_date = _parse_date(payload.get("startDate"), "startDate", errors)
    end_date = _parse_date(payload.get("endDate"), "endDate", errors)
    if start_date and end_date:
        try:
            if datetime.strptime(end_date, DATE_FORMAT) <= datetime.strptime(start_date, DATE_FORMAT):
                errors.append(_error("endDate", "Дата окончания должна быть позже даты начала"))
        except ValueError:
            pass

    raw_time_limit = payload.get("timeLimitMinutes")
    if isinstance(raw_time_limit, bool) or not isinstance(raw_time_limit, int) or raw_time_limit <= 0:
        errors.append(_error("timeLimitMinutes", "Время должно быть целым числом больше 0"))
        time_limit = 30
    else:
        time_limit = raw_time_limit

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        errors.append(_error("questions", "questions должен быть массивом"))
        raw_questions = []
    if not raw_questions:
        errors.append(_error("questions", "Добавьте хотя бы один вопрос"))

    seen_question_ids: set[int] = set()
    questions = [
        _normalize_question(question, index, seen_question_ids, errors)
        for index, question in enumerate(raw_questions)
    ]

    normalized = {
        "title": title,
        "direction": direction,
        "startDate": start_date,
        "endDate": end_date,
        "timeLimitMinutes": time_limit,
        "published": _parse_bool(payload.get("published"), "published", True, errors),
        "visible": _parse_bool(payload.get("visible"), "visible", False, errors),
        "questions": questions,
    }
    return normalized, errors


def build_preview(payload: Any) -> Dict[str, Any]:
    normalized, errors = normalize_test_payload(payload)
    questions = normalized.get("questions") or []
    summary = {
        "questionsTotal": len(questions),
        "totalPoints": sum(int(q.get("points") or 0) for q in questions),
        "singleCount": sum(1 for q in questions if q.get("type") == "single"),
        "multipleCount": sum(1 for q in questions if q.get("type") == "multiple"),
        "textCount": sum(1 for q in questions if q.get("type") == "text"),
        "errorsCount": len(errors),
    }
    return {
        "status": len(errors) == 0,
        "preview": normalized,
        "summary": summary,
        "errors": errors,
    }


def commit_import(payload: Any) -> Dict[str, Any]:
    preview = build_preview(payload)
    if preview["errors"]:
        return {
            "status": False,
            "error": "Исправьте ошибки перед созданием теста",
            "preview": preview["preview"],
            "summary": preview["summary"],
            "errors": preview["errors"],
        }

    test_id = create_test(preview["preview"])
    return {
        "status": True,
        "testId": test_id,
        "preview": preview["preview"],
        "summary": preview["summary"],
    }
