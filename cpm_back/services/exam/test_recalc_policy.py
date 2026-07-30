"""Политика: нужен ли пересчёт test_sessions после правки теста (ТЗ методолога)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


REASON_QUESTION_REMOVED = "question_removed"
REASON_QUESTION_TYPE_CHANGED = "question_type_changed"
REASON_POINTS_CHANGED = "points_changed"
REASON_CORRECT_FLAG_CHANGED = "correct_flag_changed"
REASON_OPTION_REMOVED = "option_removed"
REASON_TEXT_CORRECT_REMOVED = "text_correct_removed"


@dataclass(frozen=True)
class RecalcDecision:
    needs_recalc: bool
    reasons: Tuple[str, ...]
    exclude_question_ids: frozenset


def _as_qid(value: Any) -> Any:
    return value


def _questions_by_id(test_doc: Optional[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    result: Dict[Any, Dict[str, Any]] = {}
    if not test_doc:
        return result
    for raw in test_doc.get("questions") or []:
        if not isinstance(raw, dict):
            continue
        qid = raw.get("questionId")
        if qid is None:
            continue
        result[_as_qid(qid)] = raw
    return result


def _option_map(question: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in (question.get("answers") or [])
        if isinstance(item, dict) and item.get("id") is not None
    }


def _correct_text_set(question: Dict[str, Any]) -> Set[str]:
    return {str(item or "") for item in (question.get("correctAnswers") or [])}


def _collect_reasons_for_shared_question(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Tuple[List[str], bool]:
    """Returns (reasons, type_changed)."""
    reasons: List[str] = []
    type_changed = (before.get("type") or None) != (after.get("type") or None)
    if type_changed:
        reasons.append(REASON_QUESTION_TYPE_CHANGED)
        return reasons, True

    if int(before.get("points") or 0) != int(after.get("points") or 0):
        reasons.append(REASON_POINTS_CHANGED)

    before_opts = _option_map(before)
    after_opts = _option_map(after)

    removed_ids = set(before_opts.keys()) - set(after_opts.keys())
    if removed_ids:
        reasons.append(REASON_OPTION_REMOVED)

    for oid in set(before_opts.keys()) & set(after_opts.keys()):
        prev_correct = bool(before_opts[oid].get("isCorrect"))
        cur_correct = bool(after_opts[oid].get("isCorrect"))
        if prev_correct != cur_correct:
            reasons.append(REASON_CORRECT_FLAG_CHANGED)
            break

    before_text = _correct_text_set(before)
    after_text = _correct_text_set(after)
    if before_text - after_text:
        reasons.append(REASON_TEXT_CORRECT_REMOVED)

    return reasons, False


def decide_session_recalc(
    before_test: Optional[Dict[str, Any]],
    after_test: Optional[Dict[str, Any]],
) -> RecalcDecision:
    """
    Решает, нужно ли пересчитывать старые test_sessions после update теста.

    Scoring-relevant (needs_recalc):
    - удалён вопрос
    - сменён type (тот же questionId) → exclude из старых session
    - изменён points
    - сменён isCorrect у варианта
    - удалён любой вариант ответа
    - из correctAnswers что-то убрано

    Не триггерит: текст вопроса, metadata, add question/option, только-add correctAnswers,
    правка текста варианта без isCorrect, reorder.
    """
    before_q = _questions_by_id(before_test)
    after_q = _questions_by_id(after_test)

    reasons: List[str] = []
    exclude: Set[Any] = set()

    removed_ids = set(before_q.keys()) - set(after_q.keys())
    if removed_ids:
        reasons.append(REASON_QUESTION_REMOVED)

    for qid in sorted(before_q.keys() & after_q.keys(), key=lambda x: str(x)):
        q_reasons, type_changed = _collect_reasons_for_shared_question(
            before_q[qid], after_q[qid]
        )
        if type_changed:
            exclude.add(qid)
        reasons.extend(q_reasons)

    # Stable unique order
    seen: Set[str] = set()
    ordered: List[str] = []
    for code in reasons:
        if code not in seen:
            seen.add(code)
            ordered.append(code)

    return RecalcDecision(
        needs_recalc=bool(ordered),
        reasons=tuple(ordered),
        exclude_question_ids=frozenset(exclude),
    )


def decision_to_api_dict(decision: RecalcDecision) -> Dict[str, Any]:
    return {
        "needsRecalc": decision.needs_recalc,
        "reasons": list(decision.reasons),
        "excludeQuestionIds": [
            qid for qid in sorted(decision.exclude_question_ids, key=lambda x: str(x))
        ],
    }
