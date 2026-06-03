# API тестов — руководство для фронтенда

Документ описывает **все HTTP-эндпоинты** модуля тестов exam-сервиса и **рекомендуемый сценарий** для студента после внедрения `test_attempts`.

Базовый URL совпадает с `API_EXAM_URL` в `cpm-app` (сейчас `https://nickly24-cpm-back-c633.twc1.net`). Префикса `/api` у этих путей **нет**.

Общая архитектура и схемы БД — в [03-testy.md](./03-testy.md).

---

## 1. Авторизация

| Способ | Как передать |
|--------|----------------|
| Bearer | Заголовок `Authorization: Bearer <token>` (токен из `localStorage.auth_token`, см. `cpm-app/src/api.js`) |
| Cookie | `withCredentials: true` — cookie `auth_token` после логина |

Декораторы в коде:

| Декоратор | Кто проходит |
|-----------|----------------|
| без auth | только `GET /directions` |
| `@require_auth` | любая авторизованная роль |
| `@require_role('student')` | только студент |
| `@require_role('admin')` | только admin |
| `@require_self_or_role('student_id', 'admin')` | admin или пользователь с `id === student_id` в URL/body |

При отсутствии или невалидном токене — **401**. При неверной роли — **403**.

---

## 2. Сценарий студента (актуальный)

**Не используйте** для официальной сдачи:

- `GET /test/<id>` — только **admin** (в ответе есть ключи ответов).
- `POST /create-test-session` от роли **student** — **410 Gone** (`deprecated`).

**Используйте** цепочку attempt:

```mermaid
sequenceDiagram
    participant UI
    participant API

    UI->>API: GET /tests/{direction}/with-sessions
    API-->>UI: tests + sessions + serverTimeMoscow + activeAttempt

    alt Новый тест (canStart)
        UI->>API: POST /test-attempt/start { testId }
        API-->>UI: attempt + questions (без ключей)
    else Продолжить (canResume)
        UI->>API: GET /test-attempt/active?testId=
        или GET /test-attempt/{attemptId}
    end

    loop На каждый вопрос (один раз)
        UI->>API: PATCH /test-attempt/{id}/answer
    end

    UI->>API: POST /test-attempt/{id}/submit
    API-->>UI: sessionId, score

    opt Разбор ответов
        UI->>API: GET /test-session/{sessionId}/review
    end
```

### Что делает бэкенд за вас

| Раньше (фронт) | Сейчас (бэкенд) |
|----------------|-----------------|
| Shuffle вопросов в `localStorage` | `questionOrder` при `start` |
| Таймер только на клиенте | `expiresAt`, `remainingSeconds` в attempt |
| Повторное изменение ответа | **403** `answer_locked` |
| Расчёт `score` на клиенте | Только в `submit` |
| Хранение черновика в LS | Коллекция Mongo `test_attempts` |

### Тренировка (`canPractice`)

На бэкенде **нет** server-side attempt для practice: `start` с practice не реализован (`practice_use_frontend_only`). Для тренировки пока оставляйте локальный режим **без** `create-test-session` (или загружайте вопросы только у admin-инструментов — не для студента).

---

## 3. Справочники и списки

### `GET /directions`

| | |
|--|--|
| Auth | нет |
| Ответ | массив направлений из MySQL: `{ id, name, ... }` |

```http
GET /directions
```

---

### `GET /tests/<direction>`

| | |
|--|--|
| Auth | `@require_auth` |
| `direction` | строка **`directions.name`** (не numeric id) |
| Query (опционально) | `page`, `limit` — пагинация; если оба не переданы — **плоский массив** тестов |

**Ответ без пагинации:** массив объектов теста (лёгкий формат, **без вопросов**).

**Ответ с пагинацией:**

```json
{
  "tests": [ /* страница */ ],
  "external_tests": [],
  "pagination": { "current_page": 1, "total_pages": 3, "total_items": 42, "items_per_page": 20 },
  "counts": { "all": 42, "available": 5, "upcoming": 10, "completed": 20, "missed": 2, "external": 5 }
}
```

**Флаги на каждом тесте** (внутренний тест, время — **Москва**):

| Поле | Смысл |
|------|--------|
| `status` | `available` \| `upcoming` \| `completed` \| `missed` \| `external` |
| `isCompleted` | есть Mongo `test_sessions` (или результат внешнего) |
| `canStart` | окно открыто и ещё не сдан |
| `canResume` | есть `in_progress` attempt и `remainingSeconds > 0` |
| `canPractice` | уже сдан, можно тренировку (только UI) |
| `canViewResults` | сдан + (`visible` или роль ≠ student) |
| `activeAttempt` | `{ id, expiresAt, remainingSeconds, answeredCount, totalQuestions }` — только student |

Если `canResume === true`, бэкенд выставляет `canStart: false`.

---

### `GET /tests/<direction>/with-sessions`

| | |
|--|--|
| Auth | **только student** (иначе 403) |
| Ответ | `{ tests, sessions, serverTimeMoscow }` |

`serverTimeMoscow` — ISO время сервера (Europe/Moscow), для UI статусов «скоро» / «пропущен».

У каждой сессии в `sessions` может быть вложенный `stats` (если удалось посчитать).

**Рекомендация:** главный экран списка тестов студента — этот эндпоинт.

---

### `GET /external-tests/direction/<direction_id>`

| | |
|--|--|
| Auth | `@require_auth` |
| `direction_id` | **числовой** id из MySQL `directions.id` |
| Student | тесты + `hasResult`, `rate` |
| Admin / прочие | все внешние тесты направления |

Те же внешние тесты дублируются в `GET /tests/<direction>` с `isExternal: true`.

---

### `GET /external-tests/student/<student_id>/direction/<direction_id>`

| | |
|--|--|
| Auth | student только **свой** `student_id`, admin — любой |

---

## 4. Попытка прохождения (student)

Blueprint: `test_attempts_bp`. Все маршруты — роль **student**.

### `POST /test-attempt/start`

Создать попытку или **возобновить** существующую `in_progress`.

**Тело:**

```json
{ "testId": "507f1f77bcf86cd799439011" }
```

**Успех 200:**

```json
{
  "success": true,
  "resumed": false,
  "attempt": {
    "attemptId": "...",
    "studentId": 123,
    "testId": "507f1f77bcf86cd799439011",
    "status": "in_progress",
    "isPractice": false,
    "startedAt": "2026-06-02T10:00:00.000Z",
    "expiresAt": "2026-06-02T11:30:00.000Z",
    "remainingSeconds": 5400,
    "timeExpired": false,
    "questionOrder": [1, 3, 2],
    "answers": [],
    "answeredCount": 0,
    "totalQuestions": 3,
    "questions": [
      {
        "questionId": 1,
        "type": "single",
        "text": "Вопрос?",
        "points": 2,
        "answers": [{ "id": 10, "text": "A" }, { "id": 11, "text": "B" }],
        "locked": false
      }
    ]
  }
}
```

`resumed: true` — та же попытка, порядок вопросов **не меняется**.

**Ошибки:**

| HTTP | `error` | Когда |
|------|---------|--------|
| 400 | `testId_required` | нет testId |
| 404 | `test_not_found` | нет теста |
| 400 | `test_has_no_questions` | пустой тест |
| 403 | `test_not_started` / `test_ended` | вне окна Москвы |
| 409 | `test_already_completed` | уже есть `test_sessions` |
| 403 | `attempt_expired` | попытка истекла по таймеру |

---

### `GET /test-attempt/active?testId=<id>`

Активная попытка по тесту или `{ "success": true, "attempt": null }`.

Формат `attempt` — как в `start` (с `questions` и `locked`).

---

### `GET /test-attempt/<attempt_id>`

Полное состояние попытки для восстановления UI после перезагрузки страницы.

**404** — `attempt_not_found` (чужой id или неверный ObjectId).

---

### `PATCH /test-attempt/<attempt_id>/answer`

Сохранить ответ **один раз** на вопрос. Повтор по тому же `questionId` → **403** `answer_locked`.

**Тело по типам:**

```json
// single
{ "questionId": 1, "type": "single", "selectedAnswer": 10 }

// multiple
{ "questionId": 2, "type": "multiple", "selectedAnswers": [20, 21] }

// text
{ "questionId": 3, "type": "text", "textAnswer": "ответ" }
```

**Успех 200:** `{ "success": true, "attempt": { ... } }` — обновлённый attempt с пересчитанными `locked` на вопросах.

**Ошибки:**

| HTTP | `error` |
|------|---------|
| 400 | `question_id_required`, `invalid_answer_type`, `invalid_question_id` |
| 403 | `answer_locked`, `time_expired` |
| 404 | `attempt_not_found`, `attempt_not_active` |

**UI:** после успешного PATCH не давайте менять ответ на этом вопросе (или показывайте read-only). Навигация «назад» только для просмотра, без повторного PATCH.

---

### `POST /test-attempt/<attempt_id>/submit`

Финальная сдача: scoring на сервере, запись `test_sessions`, attempt → `submitted`.

**Успех 200:**

```json
{
  "success": true,
  "sessionId": "665a1b2c3d4e5f678901234",
  "score": 18,
  "answers": [
    {
      "questionId": 1,
      "type": "single",
      "selectedAnswer": 10,
      "points": 2,
      "isCorrect": true
    }
  ]
}
```

**Ошибки:**

| HTTP | `error` | Примечание |
|------|---------|------------|
| 403 | `time_expired` | лимит `timeLimitMinutes` |
| 403 | `test_not_started` / `test_ended` | окно сдачи |
| 409 | `test_already_completed` | + `existingSessionId`, `existingScore`, `completedAt` |
| 404 | `attempt_not_found` | |

Непройденные вопросы в submit учитываются как **0 баллов** (пустых placeholder в сессии не создаётся — только ответы из attempt).

---

## 5. Результаты и разбор

### `GET /test-session/<session_id>`

| | |
|--|--|
| Auth | admin или владелец сессии (`studentId`) |
| Ответ | документ сессии из Mongo (включая `answers`, `score`, `questionOrder`, …) |

---

### `GET /test-session/<session_id>/stats`

Краткая статистика по сессии (доля верных, баллы и т.д. — см. `get_test_session_stats`).

Те же правила доступа, что у `GET /test-session/<id>`.

---

### `GET /test-session/<session_id>/review`

Разбор после сдачи: вопросы в порядке `questionOrder`, ответ студента, баллы.

**Правильные ответы** (`correct` в items):

| Роль | Условие |
|------|---------|
| admin | всегда |
| student | только если у теста `visible: true` |
| proctor / supervisor | **не** видят ключи (как student без visible) |

**Успех 200 (фрагмент):**

```json
{
  "success": true,
  "review": {
    "sessionId": "...",
    "testId": "...",
    "testTitle": "Тест 1",
    "score": 18,
    "completedAt": "2026-06-02T11:25:00.000Z",
    "timeSpentMinutes": 25,
    "questionOrder": [1, 3, 2],
    "visible": true,
    "showCorrectAnswers": true,
    "items": [
      {
        "questionId": 1,
        "question": { "questionId": 1, "type": "single", "text": "...", "answers": [...] },
        "studentAnswer": { "type": "single", "selectedAnswer": 10 },
        "points": 2,
        "isCorrect": true,
        "correct": { "correctOptionIds": [10], "answers": [ /* с isCorrect */ ] }
      }
    ]
  }
}
```

Если `showCorrectAnswers: false`, поля `correct` в items **нет** — показывайте только свой ответ и итоговый балл.

**403** `forbidden` — чужая сессия.

---

### `GET /test-session/student/<student_id>/test/<test_id>`

Одна сессия по паре студент+тест (для кнопки «результат» без перебора списка).

---

### `GET /test-sessions/student/<student_id>`

Список всех сессий студента. Student — только свой id.

---

## 6. Админ: CRUD тестов и сессии

### `GET /test/<test_id>`

Полный тест **с правильными ответами** — **только admin**.

Студенту вопросы при прохождении отдаёт только attempt API (`questions` без `isCorrect`).

---

### `POST /create-test`

Создание теста. Тело — объект теста (title, direction, dates, `timeLimitMinutes`, `questions[]`, …). Ответ: `{ "id": "<ObjectId>" }`.

---

### `PUT /test/<test_id>`

Обновление + **пересчёт** всех `test_sessions` по этому тесту (`recalc` в ответе).

---

### `DELETE /test/<test_id>`

Удаление теста, связанных сессий и attempts.

---

### `PUT /test/<test_id>/toggle-visibility`

Переключить `visible` (влияет на `canViewResults` у студента и `showCorrectAnswers` в review).

Ответ: `{ "message", "visible", "testId" }`.

---

### `GET /test-sessions/test/<test_id>`

Все сессии по тесту — **admin**. То же, что `GET /test/<test_id>/sessions`.

---

### Админ: сессии и попытки по тесту

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/test/<test_id>/overview` | Сводка: сдало / в работе / средний балл |
| GET | `/test/<test_id>/sessions` | Список сдач (пагинация + поиск) |
| GET | `/test-session/<session_id>/admin` | Детали сессии + `stats` + `studentFullName` |
| DELETE | `/test-session/<session_id>` | Удалить сдачу (студент сможет сдать снова) |
| GET | `/test/<test_id>/attempts` | Попытки (пагинация + поиск + `status`) |
| GET | `/test-attempt/<attempt_id>/admin` | Детали попытки: ответы по вопросам, ключи |
| DELETE | `/test-attempt/<attempt_id>` | Удалить попытку (пересдача, если нет сессии) |

**Query для списков** (sessions и attempts):

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `page` | `1` | Страница |
| `limit` | `10` | Записей (макс. 50) |
| `search` или `q` | — | Подстрока имени студента (мин. 2 символа) |

Поиск: сначала MySQL `students.full_name LIKE`, затем фильтр Mongo по `studentId` (до 50 id). Пустой результат поиска — сразу пустой список без полного скана.

**Ответ списка:**

```json
{
  "sessions": [],
  "pagination": { "page": 1, "limit": 10, "total": 42, "totalPages": 5, "hasNext": true, "hasPrev": false },
  "search": "иван"
}
```

**`GET /test/<test_id>/overview`** — только аналитика:

```json
{
  "analytics": {
    "sessionsCompleted": 120,
    "averageScore": 78.5,
    "attemptsInProgress": 3,
    "attemptsExpired": 1,
    "attemptsSubmitted": 0,
    "attemptsActive": 4
  }
}
```

**`GET /test/<test_id>/attempts?status=`**

| Значение | Что вернёт |
|----------|------------|
| `active` (по умолчанию) | `in_progress` + `expired` |
| `all` | + `submitted` (без practice) |
| `in_progress,expired` | явный список статусов |

Ответ детали попытки: `items[]` — порядок вопросов, `studentAnswer`, полный `question` с правильными вариантами.

---

### `POST /create-test-session`

| Роль | Поведение |
|------|-----------|
| **student** | **410** — `deprecated`, сообщение про attempt API |
| **admin** | Ручная сдача за студента (миграции, исправления) |

**Тело (admin):**

```json
{
  "studentId": 123,
  "testId": "507f1f77bcf86cd799439011",
  "testTitle": "Название",
  "answers": [ /* как раньше */ ],
  "score": 20,
  "timeSpentMinutes": 30
}
```

**409** — уже есть сдача (`existingSessionId`, …).

---

## 7. Таблица всех эндпоинтов

| Метод | Путь | Роль | Назначение |
|-------|------|------|------------|
| GET | `/directions` | — | Справочник направлений |
| GET | `/tests/:direction` | auth | Список тестов (+ флаги, activeAttempt) |
| GET | `/tests/:direction/with-sessions` | student | Список + сессии + время сервера |
| GET | `/external-tests/direction/:direction_id` | auth | Внешние тесты |
| GET | `/external-tests/student/:sid/direction/:did` | self/admin | Внешние + результаты студента |
| POST | `/test-attempt/start` | student | Начать / продолжить попытку |
| GET | `/test-attempt/active` | student | Активная попытка по testId |
| GET | `/test-attempt/:id` | student | Состояние попытки |
| PATCH | `/test-attempt/:id/answer` | student | Сохранить ответ (immutable) |
| POST | `/test-attempt/:id/submit` | student | Сдать, получить sessionId |
| GET | `/test/:id` | admin | Полный тест с ключами |
| POST | `/create-test` | admin | Создать тест |
| PUT | `/test/:id` | admin | Обновить + recalc сессий |
| DELETE | `/test/:id` | admin | Удалить тест и связанное |
| PUT | `/test/:id/toggle-visibility` | admin | visible on/off |
| POST | `/create-test-session` | admin (student → 410) | Ручная сдача |
| GET | `/test-session/:id` | owner/admin | Сессия |
| GET | `/test-session/:id/stats` | owner/admin | Статистика |
| GET | `/test-session/:id/review` | owner/admin | Разбор |
| GET | `/test-session/student/:sid/test/:tid` | self/admin | Сессия по паре |
| GET | `/test-sessions/student/:sid` | self/admin | Все сессии студента |
| GET | `/test-sessions/test/:tid` | admin | Все сессии по тесту |
| GET | `/test/:tid/overview` | admin | Аналитика по тесту |
| GET | `/test/:tid/sessions` | admin | Список сдач с именами (page, search) |
| GET | `/test-session/:id/admin` | admin | Детали сдачи |
| DELETE | `/test-session/:id` | admin | Удалить сдачу |
| GET | `/test/:tid/attempts` | admin | Список попыток (page, search) |
| GET | `/test-attempt/:id/admin` | admin | Детали попытки |
| DELETE | `/test-attempt/:id` | admin | Удалить попытку |
| PUT | `/test/:id/toggle-published` | admin | Видимость теста для студентов |

---

## 8. Миграция `Tests.js` (чеклист)

1. **Список:** оставить `GET /tests/:direction/with-sessions`; использовать `canStart`, `canResume`, `activeAttempt`, `serverTimeMoscow` вместо только локальной логики.
2. **Старт:** `POST /test-attempt/start` вместо `GET /test/:id` + localStorage.
3. **Таймер:** UI = `remainingSeconds` с сервера (периодический `GET /test-attempt/:id` или polling `active` — по желанию; при submit проверяется снова).
4. **Ответы:** `PATCH .../answer` при переходе «Далее» / фиксации вопроса; не копить массив только в LS.
5. **Финиш:** `POST .../submit` вместо `POST /create-test-session` и клиентского `score`.
6. **Разбор:** `GET /test-session/:id/review` вместо повторного `GET /test/:id` + сравнения на клиенте; учитывать `showCorrectAnswers`.
7. **Удалить** для официальной сдачи: shuffle/score в localStorage, вызов `create-test-session` для student.
8. **Тренировка:** без изменений на бэке до отдельной задачи; не вызывать `start` с practice.

---

## 9. Типы вопросов (контракт)

| `type` | PATCH поле | Проверка на бэке |
|--------|------------|------------------|
| `single` | `selectedAnswer` — id варианта | совпадение с `isCorrect` |
| `multiple` | `selectedAnswers` — массив id | все верные и ни одного лишнего |
| `text` | `textAnswer` | trim + lower, список `correctAnswers` в тесте |

В `review` / admin-тесте у text в ключах — `correctAnswers`; у single/multiple в вариантах — `isCorrect` на `answers[]`.

---

## 10. Коды ошибок (сводка)

Все ошибки attempt API: `{ "success": false, "error": "<code>" }` (+ опциональные поля).

| `error` | Типичный HTTP |
|---------|----------------|
| `testId_required` | 400 |
| `test_not_found` | 404 |
| `test_has_no_questions` | 400 |
| `test_not_started`, `test_ended` | 403 |
| `test_already_completed` | 409 |
| `attempt_expired`, `time_expired` | 403 |
| `question_id_required`, `invalid_answer_type`, `invalid_question_id` | 400 |
| `answer_locked` | 403 |
| `attempt_not_found`, `attempt_not_active` | 404 |
| `deprecated` (create-test-session student) | 410 |

---

## 11. Связанные файлы в репозитории

| Компонент | Путь |
|-----------|------|
| Attempt routes | `cpm_back/blueprints/test_attempts_bp.py` |
| Tests + sessions + review | `cpm_back/blueprints/tests_bp.py` |
| External | `cpm_back/blueprints/external_tests_bp.py` |
| Directions | `cpm_back/blueprints/directions_bp.py` |
| Логика attempt | `cpm_back/services/exam/test_attempts.py` |
| Scoring | `cpm_back/services/exam/scoring.py` |
| Санитизация вопросов | `cpm_back/services/exam/test_sanitize.py` |
| Фронт (до миграции) | `cpm-app/src/cabinets/StudentFunctions/Tests.js` |
