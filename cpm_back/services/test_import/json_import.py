from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

from cpm_back.services.exam.create_test import create_test
from cpm_back.services.exam.get_directions import get_directions

SUPPORTED_TYPES = {"single", "multiple", "text"}
ONLINE_TEST_PAD_FORMAT = "online_test_pad"
DATE_FORMAT = "%Y-%m-%dT%H:%M"


def _error(path: str, message: str) -> Dict[str, str]:
    return {"path": path, "message": message}


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _value_to_str(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def _html_to_text(value: Any) -> str:
    text = _as_str(value)
    if not text:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _option_id(index: int) -> str:
    result = ""
    current = index
    while True:
        result = chr(ord("a") + (current % 26)) + result
        current = current // 26 - 1
        if current < 0:
            return result


def _is_online_test_pad_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("importFormat") == ONLINE_TEST_PAD_FORMAT
    )


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


def _extract_online_test_pad_test(source_text: Any, errors: List[Dict[str, str]]) -> Dict[str, Any]:
    source = _as_str(source_text).lstrip("\ufeff")
    if not source:
        errors.append(_error("sourceText", "Передайте содержимое файла test.js"))
        return {}

    marker = re.search(r"\bvar\s+test\s*=", source)
    if not marker:
        errors.append(_error("sourceText", "Не найден объект var test = {...}"))
        return {}

    start = source.find("{", marker.end())
    if start < 0:
        errors.append(_error("sourceText", "Не найден JSON-объект test"))
        return {}

    depth = 0
    in_string = False
    escaped = False
    end = -1
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    if end < 0:
        errors.append(_error("sourceText", "Не удалось определить конец объекта test"))
        return {}

    try:
        parsed = json.loads(source[start:end])
    except json.JSONDecodeError as exc:
        errors.append(_error("sourceText", f"test.js содержит некорректный JSON: {exc.msg}"))
        return {}

    if not isinstance(parsed, dict):
        errors.append(_error("sourceText", "Объект test должен быть JSON-объектом"))
        return {}
    return parsed


def _as_positive_points(value: Any) -> int:
    if isinstance(value, bool):
        return 1
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1
    return max(1, round(number))


def _online_question_type(answer_type: Any) -> Optional[str]:
    if answer_type == 10:
        return "single"
    if answer_type == 210:
        return "multiple"
    if answer_type in (410, 420):
        return "text"
    return None


def _score_is_positive(answer: Dict[str, Any]) -> bool:
    try:
        return float(answer.get("Score") or 0) > 0
    except (TypeError, ValueError):
        return False


def _answer_template_text(answer: Dict[str, Any]) -> str:
    return (
        _html_to_text(answer.get("TextWOHtml"))
        or _html_to_text(answer.get("Text"))
        or _as_str(answer.get("ValueText"))
        or _value_to_str(answer.get("ValueInt"))
        or _value_to_str(answer.get("ValueFloat"))
    )


def _text_answer_values(answer: Dict[str, Any]) -> List[str]:
    candidates = [
        _as_str(answer.get("ValueText")),
        _html_to_text(answer.get("TextWOHtml")),
        _html_to_text(answer.get("Text")),
    ]
    if answer.get("ValueInt") is not None and not isinstance(answer.get("ValueInt"), bool):
        candidates.append(_value_to_str(answer.get("ValueInt")))
    if answer.get("ValueFloat") is not None and not isinstance(answer.get("ValueFloat"), bool):
        raw_float = answer.get("ValueFloat")
        try:
            parsed = float(raw_float)
            candidates.append(str(int(parsed)) if parsed.is_integer() else str(parsed))
        except (TypeError, ValueError):
            candidates.append(_value_to_str(raw_float))

    result: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _convert_online_question(
    question: Any,
    index: int,
    errors: List[Dict[str, str]],
) -> Dict[str, Any]:
    path = f"Questions[{index}]"
    if not isinstance(question, dict):
        errors.append(_error(path, "Вопрос должен быть объектом"))
        return {
            "questionId": index + 1,
            "type": "single",
            "text": "",
            "points": 1,
            "answers": [],
        }

    q_type = _online_question_type(question.get("AnswerType"))
    if not q_type:
        errors.append(_error(f"{path}.AnswerType", f"Неизвестный AnswerType: {question.get('AnswerType')}"))
        q_type = "single"

    points = _as_positive_points(question.get("maxScore") or question.get("RightScore") or 1)
    converted = {
        "questionId": index + 1,
        "type": q_type,
        "text": _html_to_text(question.get("Text")),
        "points": points,
        "answers": [],
        "correctAnswers": [],
    }

    templates = question.get("AnswerTemplates") or []
    if not isinstance(templates, list):
        errors.append(_error(f"{path}.AnswerTemplates", "AnswerTemplates должен быть массивом"))
        templates = []
    templates = sorted(
        templates,
        key=lambda item: item.get("Number") if isinstance(item, dict) and isinstance(item.get("Number"), int) else 0,
    )

    if q_type in ("single", "multiple"):
        answers = []
        for answer_index, answer in enumerate(templates):
            if not isinstance(answer, dict):
                errors.append(_error(f"{path}.AnswerTemplates[{answer_index}]", "Вариант ответа должен быть объектом"))
                continue
            answers.append({
                "id": _option_id(answer_index),
                "text": _answer_template_text(answer),
                "isCorrect": _score_is_positive(answer),
            })
        converted["answers"] = answers
        converted.pop("correctAnswers", None)
    else:
        correct_answers: List[str] = []
        for answer in templates:
            if isinstance(answer, dict) and _score_is_positive(answer):
                for value in _text_answer_values(answer):
                    if value not in correct_answers:
                        correct_answers.append(value)
        if not correct_answers:
            errors.append(_error(f"{path}.AnswerTemplates", "Для text-вопроса не найден правильный ответ"))
        converted["answers"] = []
        converted["correctAnswers"] = correct_answers

    return converted


def _online_warnings(source: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    if source.get("IsLimitedQuestions") is True:
        count = source.get("LimitedQuestionsCount")
        warnings.append(
            f"В исходном тесте включён лимит случайных вопросов"
            f"{f' ({count})' if count else ''}; CPM v1 импортирует все вопросы."
        )
    if source.get("IsRandomQuestions") is True:
        warnings.append("В исходном тесте включён случайный порядок вопросов; CPM перемешивает вопросы при старте попытки.")
    if source.get("IsRandomAnswersInAllQuestions") is True:
        warnings.append("В исходном тесте включено перемешивание ответов; CPM v1 сохраняет порядок вариантов из файла.")
    return warnings


def _convert_online_test_pad_payload(
    payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, str]], Dict[str, Any]]:
    errors: List[Dict[str, str]] = []
    source = _extract_online_test_pad_test(payload.get("sourceText"), errors)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    params = source.get("Params") if isinstance(source.get("Params"), dict) else {}
    raw_questions = source.get("Questions") if isinstance(source, dict) else []
    if source and not isinstance(raw_questions, list):
        errors.append(_error("Questions", "В test.js нет массива Questions"))
        raw_questions = []
    if source and not raw_questions:
        errors.append(_error("Questions", "Добавьте хотя бы один вопрос"))

    questions = [
        _convert_online_question(question, index, errors)
        for index, question in enumerate(
            sorted(
                raw_questions,
                key=lambda item: item.get("Number") if isinstance(item, dict) and isinstance(item.get("Number"), int) else 0,
            )
        )
    ]

    time_limit = metadata.get("timeLimitMinutes")
    if time_limit is None:
        time_limit = params.get("timelimitminutes") if params.get("timelimited") is True else 30

    converted = {
        "title": metadata.get("title", source.get("Name") if source else ""),
        "direction": metadata.get("direction", ""),
        "startDate": metadata.get("startDate", ""),
        "endDate": metadata.get("endDate", ""),
        "timeLimitMinutes": time_limit,
        "published": metadata.get("published", False),
        "visible": metadata.get("visible", False),
        "questions": questions,
    }
    meta = {
        "source": ONLINE_TEST_PAD_FORMAT,
        "sourceTitle": source.get("Name") if source else "",
        "sourceQuestionsTotal": len(raw_questions) if isinstance(raw_questions, list) else 0,
        "warnings": _online_warnings(source) if source else [],
    }
    return converted, errors, meta


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
    meta: Dict[str, Any] = {"source": "json", "sourceTitle": "", "warnings": []}
    conversion_errors: List[Dict[str, str]] = []
    if _is_online_test_pad_payload(payload):
        payload, conversion_errors, meta = _convert_online_test_pad_payload(payload)

    normalized, errors = normalize_test_payload(payload)
    errors = conversion_errors + errors
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
        "source": meta.get("source"),
        "sourceTitle": meta.get("sourceTitle") or normalized.get("title") or "",
        "warnings": meta.get("warnings") or [],
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
