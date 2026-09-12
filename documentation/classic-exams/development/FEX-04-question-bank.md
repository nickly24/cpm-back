# FEX-04. Части и банк вопросов — инженерная декомпозиция

## 1. Результат задачи

Администратор создаёт устойчивые части A–Z, задаёт вес и квоту, ведёт текстовый банк вручную и через Excel. Backend предоставляет единый contract для редактора, readiness и будущего алгоритма выдачи.

## 2. Изменения БД

### 2.1. Миграция `017_classic_exam_question_bank.sql`

```text
classic_exam_parts
- id BIGINT PK AUTO_INCREMENT
- exam_id BIGINT NOT NULL FK exams ON DELETE CASCADE
- code CHAR(1) NOT NULL
- question_weight INT UNSIGNED NULL
- question_count INT UNSIGNED NULL
- sort_order INT UNSIGNED NOT NULL
- created_at, updated_at DATETIME(6)
- version BIGINT UNSIGNED NOT NULL DEFAULT 1
- UNIQUE(exam_id, code)
- UNIQUE(exam_id, sort_order)
- INDEX(exam_id)
```

`question_weight/question_count` nullable, потому что импорт может создать новую ещё не настроенную часть. При non-null приложение требует `>=1`.

```text
classic_exam_questions
- id BIGINT PK AUTO_INCREMENT
- exam_id BIGINT NOT NULL FK exams ON DELETE CASCADE
- part_id BIGINT NOT NULL FK classic_exam_parts ON DELETE CASCADE
- question_text TEXT NOT NULL
- answer_text TEXT NOT NULL
- content_hash CHAR(64) NOT NULL
- sort_order INT UNSIGNED NOT NULL
- created_at, updated_at DATETIME(6)
- version BIGINT UNSIGNED NOT NULL DEFAULT 1
- UNIQUE(part_id, content_hash)
- UNIQUE(part_id, sort_order)
- INDEX(exam_id, part_id)
```

`content_hash = sha256(canonical JSON array [normalizedQuestion, normalizedAnswer])`. Normalize CRLF/CR→LF и trim; регистр/внутренние пробелы значимы. JSON исключает неоднозначность разделителя. Дубликат = оба текста совпадают в одной части; одинаковый вопрос с другим эталоном — предупреждение, не блокировка.

```text
classic_exam_question_import_sessions
- id BIGINT PK
- exam_id FK exams ON DELETE CASCADE
- source_filename VARCHAR(255)
- preview_payload JSON
- created_by BIGINT
- created_at, expires_at, committed_at
- INDEX(exam_id, expires_at)
```

Добавить FK `classic_exam_settings.tie_breaker_part_id → classic_exam_parts.id ON DELETE SET NULL` после проверки, что часть принадлежит тому же exam на уровне service.

## 3. Backend

### 3.1. Репозитории

`ClassicExamPartRepository`:

- list/get/lock/create/update/delete;
- allocate next sort_order;
- count questions;
- validate code uniqueness.

`ClassicExamQuestionRepository`:

- paginated list/filter;
- CRUD;
- bulk insert within provided transaction;
- lookup IDs by part;
- counts by part;
- content hash conflict.

### 3.2. Сервисы

`PartService`:

- type guard classic;
- uppercase single Latin code;
- nullable or positive integer weight/count;
- stable ID, no auto-renaming other parts;
- delete preview and cascade delete;
- deletion leaves tie-breaker setting null/incomplete.

`QuestionBankService`:

- non-empty TEXT after trim;
- length constrained by MySQL TEXT bytes, API rejects >60 KiB UTF-8 per field with clear error;
- part ownership check;
- consistent hash;
- manual CRUD and import share validators.

`QuestionImportService`:

- `.xlsx` only, size limit configurable, default 10 MiB;
- aliases only for exact documented columns in v1;
- new code creates preview part action, not DB row during parse;
- commit creates parts/questions atomically;
- default new part weight/count remain null;
- exact duplicates are errors, not silent skips;
- preview TTL 72h and single commit.

## 4. REST-контракты частей

### 4.1. Список

`GET /api/exams/<exam_id>/classic/parts`

Auth: admin.

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": 12,
        "code": "A",
        "questionWeight": 3,
        "questionCount": 2,
        "bankSize": 18,
        "sortOrder": 1,
        "updatedAt": "2026-08-04T13:00:00+03:00",
        "version": 1
      }
    ]
  }
}
```

### 4.2. Создание

`POST /api/exams/<exam_id>/classic/parts`

```json
{
  "code": "A",
  "questionWeight": 3,
  "questionCount": 2
}
```

Вес/квота могут быть null. 201 возвращает part.

### 4.3. Изменение

`PATCH /api/exams/<exam_id>/classic/parts/<part_id>`

```json
{
  "code": "B",
  "questionWeight": 4,
  "questionCount": 2,
  "expectedVersion": 1
}
```

Не менять sortOrder автоматически при переименовании.

### 4.4. Delete preview и удаление

- `GET /api/exams/<exam_id>/classic/parts/<part_id>/delete-preview` → part, questionCount, referencedAsTieBreaker;
- `DELETE /api/exams/<exam_id>/classic/parts/<part_id>?expectedVersion=1` с Idempotency-Key и X-Exam-Confirmation из preview; без альтернативных timestamp headers.

Удаление возвращает 204; вопросы каскадны, tie-breaker part очищается.

Errors: `wrong_exam_type`, `invalid_part_code`, `part_code_conflict`, `invalid_weight`, `invalid_question_count`, `part_modified`.

## 5. REST-контракты вопросов

### 5.1. Список

`GET /api/exams/<exam_id>/classic/questions?partId=12&page=1&limit=50&search=&sort=order_asc`

Result item: `id,partId,partCode,questionText,answerText,sortOrder,updatedAt`.

### 5.2. Создание

`POST /api/exams/<exam_id>/classic/questions`

```json
{
  "partId": 12,
  "questionText": "Сформулируйте определение...",
  "answerText": "Эталонный ответ..."
}
```

### 5.3. Изменение

`PATCH /api/exams/<exam_id>/classic/questions/<question_id>` с partial fields и `expectedVersion`. Разрешён перенос в другую часть того же exam после duplicate check.

### 5.4. Удаление

`DELETE /api/exams/<exam_id>/classic/questions/<question_id>?expectedVersion=...` → 204. История читает immutable definition FEX-08 и не ссылается FK на source bank. Source IDs сохраняются scalar. Удаление не меняет историю/выдачу активной сдачи.

Errors:400 question_text_required/answer_text_required/text_too_long;404 part_not_found (также чужой part);409 question_duplicate/question_modified.

## 6. REST-контракты импорта

- `POST /api/exams/<exam_id>/imports/questions/parse` multipart file;
- `GET /api/exams/<exam_id>/imports/questions/sessions/<session_id>`;
- `PUT /api/exams/<exam_id>/imports/questions/sessions/<session_id>` expectedPreviewVersion/changes по общему контракту;
- `POST /api/exams/<exam_id>/imports/questions/sessions/<session_id>/commit`.

Preview использует общий session DTO; дополнительные partsToCreate и type-specific input:
```json
{
  "partsToCreate":[{"code":"C","questionWeight":null,"questionCount":null}],
  "rows":[
    {"rowId":"row-2","sourceRow":2,"excluded":false,
     "input":{"partCode":"C","questionText":"Вопрос","answerText":"Ответ"},
     "resolved":{"partId":null},"action":"create","errors":[],"warnings":[]}
  ],
  "summary":{"total":1,"included":1,"excluded":0,"creatable":1,"conflicts":0,"errors":0}
}
```

Preview update позволяет редактировать code/question/answer или удалить row. Commit никогда не обновляет existing question.

## 7. Frontend

### 7.1. Types/API

Создать Part/Question DTO, form types, pagination и import session types. API-клиент покрывает все endpoints.

### 7.2. Admin workspace

- вертикальный список/табы частей с code, weight, quota, bankSize;
- create/edit/delete part dialogs;
- deletion preview;
- question table/editor с простыми textarea;
- фильтр по части и search;
- import button, preview editor, commit result;
- parts с null weight/count имеют warning badge;
- никаких rich-text editors или file controls.

## 8. Задачи backend

1. Migration + FK settings.
2. Repositories/services/hash validation.
3. Parts CRUD + delete preview.
4. Questions CRUD/pagination.
5. Excel parse/preview/update/commit.
6. API and integration tests.

## 9. Задачи frontend

1. DTO/API layer.
2. Parts navigator/forms/delete preview.
3. Question list/editor.
4. Excel preview flow.
5. Loading/conflict/error/empty/mobile fallback tests.

## 10. Тесты

- A/Z valid, lowercase normalized, other symbols rejected;
- max 26 unique parts;
- delete B leaves A/C codes unchanged;
- nullable imported config and positive validation;
- text Unicode/newlines/60KiB boundary;
- duplicate hash manual/import/concurrent;
- atomic import rollback;
- deletion keeps future historical snapshots unaffected;
- tie-breaker part null after deletion;
- cross-exam part ownership rejected.

## 11. Definition of Done

- DB supports stable parts and TEXT bank.
- CRUD/import use shared validators.
- All documented contracts implemented.
- Admin can fully manage bank without DB access.
- No auto-renumbering or shared cross-exam questions.
- FEX-07 and FEX-09 receive repository interfaces/counts without querying UI DTOs.
- Tests and docs pass CI.


## 12. Версии, ёмкость и порядок

Каждый CRUD/import берёт exam exclusive lock, увеличивает configVersion один раз; row PATCH проверяет expectedVersion и увеличивает row.version. DELETE части очищает tie-breaker settings атомарно. Import использует [общий контракт](./01-contracts-and-imports.md).

Deployment limits v1: до5000 вопросов/экзамен, суммарно16MiB UTF-8 банка, 61440bytes на поле; quota1..100, weight1..1000, replacements0..100. Повышение limits требует повторной нагрузки. Ответ422 bank_capacity_exceeded с current/limit; без truncate.

sortOrder=max+1 под exam lock; удаления не перенумеровывают. Ручного reorder в v1 нет. При переносе вопроса выделить следующий order целевой части. При создании части только буква обязательна; weight/quota nullable.
