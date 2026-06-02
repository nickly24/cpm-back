# 002 — MongoDB: test_attempts и индексы test_sessions

**Тип:** MongoDB (не SQL).  
**Применение:** `python3 scripts/setup_test_attempts_indexes.py`

## Коллекция `test_attempts`

Создаётся автоматически при первом `POST /test-attempt/start`. Схема — в коде `test_attempts.py`.

## Индексы

### `test_attempts`

| Индекс | Поля | Назначение |
|--------|------|------------|
| `student_test_status` | studentId, testId, status | Поиск активной попытки |
| `unique_in_progress_attempt` | studentId, testId (unique, partial status=in_progress) | Одна активная попытка на пару |

### `test_sessions` (существующая)

| Индекс | Поля | Назначение |
|--------|------|------------|
| `unique_student_test` | studentId, testId (unique) | Одна официальная сдача |

## Новое поле в `test_sessions`

`questionOrder` — массив `questionId`, заполняется при submit через attempt API (старые документы без поля — review использует fallback).
