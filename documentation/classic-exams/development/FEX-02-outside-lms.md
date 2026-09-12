# FEX-02. Экзамен вне LMS — инженерная декомпозиция

## 1. Результат задачи

Администратор ведёт экзамен вне LMS полностью через систему: создаёт его с направлением и датой, вручную управляет результатами или загружает Excel. Студенческий read-contract и рейтинг получают нормализованные `points`, `grade`, `examinator`, без предположения о максимуме 6.

## 2. Изменения БД

### 2.1. Миграция `015_outside_lms_results.sql`

После обязательного `SHOW CREATE TABLE exam_sessions` привести смысл колонок:

- `val` — произвольные неотрицательные баллы, целевой тип `DECIMAL(12,2)`;
- `points` — целая итоговая оценка, целевой тип `TINYINT UNSIGNED`;
- `examinator` — свободный текст, не FK, trim1..255; nullable допустим только у прежних legacy записей;
- добавить `created_at DATETIME(6)`, `updated_at DATETIME(6)` при отсутствии и `version BIGINT UNSIGNED NOT NULL DEFAULT 1`;
- добавить индекс `(exam_id, student_id)`.

Перед unique constraint сформировать отчёт дублей `(exam_id, student_id)`. Автоматически не удалять: админ/оператор выбирает действующую запись. После очистки:

```sql
ALTER TABLE exam_sessions
    ADD CONSTRAINT uq_exam_sessions_exam_student
    UNIQUE (exam_id, student_id);
```

Если legacy FK отсутствуют, после проверки данных добавить FK на `exams` и `students` с `ON DELETE CASCADE` для exam и `RESTRICT` для student либо использовать текущую общесистемную политику удаления студента. Не менять эту политику молча.

### 2.2. Импорт-сессии

Создать специализированную таблицу:

```text
outside_exam_result_import_sessions
- id PK
- exam_id FK exams ON DELETE CASCADE
- source_filename VARCHAR(255)
- preview_payload JSON
- created_by FK admins nullable
- created_at
- expires_at
- committed_at nullable
```

TTL72h; [общий контракт импортов](./01-contracts-and-imports.md). Максимум5000 строк, сверх —413 с просьбой разделить файл. Атомарный commit, retry возвращает результат.

## 3. Backend

### 3.1. Сервисы

`OutsideExamService`:

- проверяет `exam_type == outside_lms`;
- обновляет дату экзамена;
- CRUD результата;
- гарантирует grade 0..5 и points >=0;
- нормализует examiner trim, но не сопоставляет с аккаунтом;
- возвращает conflict при повторе студента.

`OutsideExamImportService`:

- читает `.xlsx` через openpyxl;
- разрешает идентификацию студента по `student_id` или точному login;
- не использует ФИО как уникальный ключ;
- строит редактируемый preview;
- повторно валидирует каждую строку при update и commit;
- commit вставляет create rows; existing rows — blocking conflict.

### 3.2. DTO

```text
OutsideExamResult
- id: int
- examId: int
- studentId: int
- studentName: string
- points: number
- grade: 0..5
- examinator: string
- createdAt: ISO datetime
- updatedAt: ISO datetime
- version: int
```

Текущий get_exams.py::_map_session_row уже правильно отображает val→points, points→grade; сохранить маппинг. Ошибка была в /6 и неполном фильтре оценок2–5. Adapter legacy endpoint может преобразовывать отдельно до FEX-16.

## 4. REST-контракты

### 4.1. Изменение даты

`PATCH /api/exams/<exam_id>/outside-lms`

Auth: admin.

```json
{
  "date": "2026-08-20",
  "expectedVersion": 1
}
```

200 возвращает актуальный exam summary. Ошибки: `exam_not_found`, `wrong_exam_type`, `invalid_date`, `exam_modified`.

### 4.2. Список результатов

`GET /api/exams/<exam_id>/outside-lms/results?page=1&limit=25&search=&sort=student_asc`

Auth: admin.

200: paginated `OutsideExamResult[]`. Search — ID или ФИО студента, examiner. Sort allowlist: `student_asc,grade_desc,points_desc,updated_desc`.

### 4.3. Создание результата

`POST /api/exams/<exam_id>/outside-lms/results`

```json
{
  "studentId": 125,
  "points": 17.75,
  "grade": 4,
  "examinator": "Иванов И. И."
}
```

201: `{ success, data: { result: OutsideExamResult } }`.

Ошибки:

- 404 `exam_not_found` / `student_not_found`;
- 422 `wrong_exam_type`, `points_must_be_non_negative`, `grade_out_of_range`, `examinator_required`;
- 409 `outside_result_already_exists` с existingResultId.

### 4.4. Редактирование результата

`PATCH /api/exams/<exam_id>/outside-lms/results/<result_id>`

Body содержит points/grade/examinator плюс expectedVersion. studentId/examId неизменяемы; ошибочного студента исправляют удалением/созданием, не переносом записи молча.

200 возвращает result. Ошибки: not found, wrong type, invalid fields, `outside_result_already_exists`, `result_modified`.

### 4.5. Удаление результата

`DELETE /api/exams/<exam_id>/outside-lms/results/<result_id>`

204 без body. Удаление физическое. Endpoint инвалидирует рейтинг студента за релевантные периоды.

### 4.6. Импорт

- `POST /api/outside-exam-results-import/parse` multipart `exam_id,file`;
- `GET /api/outside-exam-results-import/sessions/<id>`;
- `PUT /api/outside-exam-results-import/sessions/<id>` с expectedPreviewVersion/changes по общему контракту;
- `POST /api/outside-exam-results-import/sessions/<id>/commit`.

Preview row по общему import DTO:
```json
{
  "rowId":"row-2","sourceRow":2,"excluded":false,
  "input":{"studentId":125,"studentLogin":"st125","points":17.75,"grade":4,"examinator":"Иванов И.И."},
  "resolved":{"student":{"id":125,"fullName":"Петров Пётр"},"existingResultId":null},
  "action":"create","errors":[],"warnings":[]
}
```

Existing row action=conflict. Импорт только добавляет результаты: админ удаляет/редактирует строку preview; существующий результат меняет ручным PATCH. Никакого скрытого upsert.

## 5. Frontend

### 5.1. Типы/API

Добавить `OutsideExamResult`, list params, create/update payload, import preview/session types. API-функции строго соответствуют шести контрактам выше.

### 5.2. Admin UI

В type-specific workspace:

- дата с сохранением optimistic token;
- таблица результатов с pagination/search/sort;
- форма create/edit с decimal points, select grade 0–5 и examiner text;
- destructive confirmation отдельного результата;
- ссылка/действие перейти в глобальный Upload с выбранным exam ID;
- после mutation точечно обновлять строку и summary, не перезагружать всё приложение.

### 5.3. Upload UI

Добавить тип загрузки «Результаты экзамена вне LMS»:

- сначала выбор экзамена типа outside LMS;
- download/template hint с точными колонками;
- preview table с inline edit/remove;
- summary create/conflict/error;
- commit блокирован при errors;
- итоговый отчёт counts и строки ошибок.

Student UI относится к FEX-14; в этой задаче подготовить только shared DTO/adapter.

## 6. Задачи backend

1. Migration/dedup report/constraints.
2. Repository + service + serializer.
3. Пять CRUD endpoints.
4. Import parser/session/preview/commit endpoints.
5. Rating invalidation hook interface без изменения формулы.
6. Legacy adapter tests для `val`/`points` semantics.

## 7. Задачи frontend

1. Types/API adapters.
2. Outside LMS admin workspace.
3. Result form/table/delete flow.
4. Upload panel/preview/commit/report.
5. Удалить все подписи `/ 6` для outside LMS.
6. Component/Vitest coverage.

## 8. Тесты

- points 0, decimal и большое допустимое значение;
- reject negative, NaN/Infinity/string garbage;
- grade boundaries 0/5 и reject -1/6/decimal;
- unique student per exam;
- wrong exam type на каждом endpoint;
- optimistic conflicts update/result;
- import unknown/duplicate/existing/concurrent-changed row;
- transaction rollback всего commit при DB error;
- no `/ 6` in rendered outside result;
- rating hook вызван create/update/delete/import.

## 9. Definition of Done

- Legacy DDL и duplicates проаудированы.
- Ручной CRUD и импорт работают по одному доменному сервису валидации.
- Контракты документированы и покрыты API tests.
- UI полностью управляет outside LMS без старой семантической путаницы.
- Рейтинг получает grade и инвалидируется после изменений.
- Старые данные отображаются, `/ 6` отсутствует.
- CI backend/frontend зелёный.


## 10. Миграционные границы

Перед ALTER проверить null, отрицательные значения, >2 десятичных знака, диапазон grade и дубли. Несоответствия блокируют data migration. Значение6 нельзя автоматически заменить5, дроби нельзя обрезать: отчёт/backup и явное исправление владельцем данных. До этого legacy read работает.

Все CRUD admin-only. DELETE результата требует expectedVersion и Idempotency-Key, UI подтверждает удаление. Result mutations и смена даты атомарно увеличивают rating revision. Legacy endpoint не может создать result для classic.
