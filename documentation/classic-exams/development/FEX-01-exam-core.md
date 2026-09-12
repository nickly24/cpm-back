# FEX-01. Единая модель экзамена и направления — инженерная декомпозиция

## 1. Результат задачи

После поставки существует единый type-aware API экзаменов. Администратор создаёт базовую запись типа `outside_lms` или `classic`, выбирая существующее направление. Экзамен не имеет собственного имени: все ответы получают актуальный `directionName` через таблицу `directions`.

Задача не реализует результаты outside LMS, classic-настройки, вопросы, комиссии или проведение. Она создаёт устойчивый фундамент для FEX-02–FEX-16.

## 2. Изменения БД

### 2.1. Миграция `014_exam_core.sql`

Перед написанием DDL разработчик обязан снять фактический `SHOW CREATE TABLE exams`, `exam_sessions`, `directions`; в репозитории нет исходной миграции этих legacy-таблиц.

Целевые изменения `exams`:

```sql
ALTER TABLE exams
    ADD COLUMN exam_type VARCHAR(20) NOT NULL DEFAULT 'outside_lms',
    ADD COLUMN direction_id INT NULL, -- точный тип/signedness как directions.id
    ADD COLUMN version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    ADD COLUMN created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    ADD COLUMN updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    ADD INDEX idx_exams_type_date (exam_type, date),
    ADD INDEX idx_exams_direction (direction_id);
```

Также разрешить exams.name=NULL и exams.date=NULL с сохранением текущих типов: classic создаётся без даты/имени. Фиктивные даты/копии имени запрещены. Приведённый SQL — проект, финальный DDL после preflight.

После проверки типов PK добавить FK с точно совпадающим типом:

```sql
ALTER TABLE exams
    ADD CONSTRAINT fk_exams_direction
    FOREIGN KEY (direction_id) REFERENCES directions(id)
    ON UPDATE RESTRICT ON DELETE RESTRICT;
```

`exam_type` проверяется приложением по allowlist. Если production MySQL гарантированно поддерживает и уже использует CHECK constraints, добавить:

```sql
CHECK (exam_type IN ('outside_lms', 'classic'))
```

Не делать `direction_id NOT NULL` в этой миграции: legacy backfill завершается в FEX-16. Для новых записей non-null обеспечивается API/service.

### 2.2. Backfill типа

Все существующие строки получают `outside_lms` через DEFAULT и явный контрольный UPDATE. Колонка `name` не удаляется и временно используется legacy endpoints. Новый API никогда не принимает и не изменяет `name`.

### 2.3. Сопоставление направлений

В рамках FEX-01 реализовать read-only audit command/script:

- нормализовать `exams.name` и `directions.name` через trim + casefold;
- автоматически предложить только однозначные exact-match соответствия;
- сформировать CSV/JSON отчёт `exam_id, legacy_name, suggested_direction_id, status`;
- не создавать направления и не применять неоднозначные соответствия;
- фактический production backfill и hardening выполняются в FEX-16.

## 3. Backend

### 3.1. Доменные типы

```python
class ExamType(str, Enum):
    OUTSIDE_LMS = "outside_lms"
    CLASSIC = "classic"
```

Read model:

```text
ExamSummary
- id: int
- examType: outside_lms | classic
- directionId: int | null (только legacy до FEX-16)
- directionName: string
- legacyName: string | null (admin-only до завершения backfill)
- date: YYYY-MM-DD | null
- startAt: ISO datetime | null
- endAt: ISO datetime | null
- readiness: not_applicable | incomplete | ready
- createdAt: ISO datetime
- updatedAt: ISO datetime
- version: int
```

Поля `startAt/endAt` добавятся физически в FEX-03, но сериализатор v2 сразу резервирует nullable contract, чтобы не ломать frontend позднее.

### 3.2. Репозиторий

Создать `ExamRepository` с методами:

- `list_admin(filters, page, limit, sort)`;
- `count_admin(filters)`;
- `get_by_id(exam_id, for_update=False)`;
- `create(exam_type, direction_id, legacy_date=None)`;
- `update_direction(exam_id, direction_id)`;
- `direction_exists(direction_id)`.

До полного backfill list/get используют `LEFT JOIN directions d ON d.id = e.direction_id`; для legacy null возвращают `COALESCE(d.name, e.name)` только админу до FEX-16.

### 3.3. Сервис

`ExamCoreService` отвечает за:

- parse/allowlist типа; после create examType неизменяем;
- административный Idempotency-Key при create, повтор не создаёт второй экзамен;
- существование направления;
- type-specific базовую валидацию;
- запрет принимать `name` от клиента;
- создание в транзакции;
- сериализацию без SQL-деталей.

При смене направления сервис не обновляет текстовые имена в зависимых таблицах.

### 3.4. Blueprint

Создать `exams_v2_bp` с prefix `/api/exams`. До возможности создавать classic добавить outside_lms type guards в старое чтение/удаление согласно FEX-16; старые формы ответа сохранить.

## 4. REST-контракты

### 4.1. Список экзаменов администратора

`GET /api/exams`

Auth: admin.

Query:

| Поле | Тип | Default | Правило |
|---|---|---|---|
| `page` | int | 1 | >=1 |
| `limit` | int | 20 | 1..100 |
| `type` | string | all | `outside_lms`, `classic`, `all` |
| `directionId` | int | — | существование не обязательно для пустого списка |
| `search` | string | — | по актуальному direction name и legacy name |
| `sort` | string | `date_desc` | allowlist `date_desc,date_asc,direction_asc,created_desc` |

Response 200:

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": 41,
        "examType": "classic",
        "directionId": 3,
        "directionName": "Математика",
        "legacyName": null,
        "date": null,
        "startAt": null,
        "endAt": null,
        "readiness": "incomplete",
        "createdAt": "2026-08-04T12:00:00+03:00",
        "updatedAt": "2026-08-04T12:00:00+03:00",
        "version": 1
      }
    ],
    "pagination": {
      "page": 1,
      "limit": 20,
      "total": 1,
      "totalPages": 1,
      "hasNext": false,
      "hasPrev": false
    }
  }
}
```

### 4.2. Создание базового экзамена

`POST /api/exams`

Auth: admin.

Request для classic:

```json
{
  "examType": "classic",
  "directionId": 3
}
```

Request для outside LMS:

```json
{
  "examType": "outside_lms",
  "directionId": 3,
  "date": "2026-08-20"
}
```

Для `outside_lms` календарная дата обязательна уже при создании, поскольку это базовый идентифицирующий признак типа и legacy-колонка может быть NOT NULL. Для `classic` период добавляется отдельными сохранениями в FEX-03.

Response 201:

```json
{
  "success": true,
  "data": {
    "exam": {
      "id": 41,
      "examType": "classic",
      "directionId": 3,
      "directionName": "Математика",
      "legacyName": null,
      "date": null,
      "startAt": null,
      "endAt": null,
      "readiness": "incomplete",
      "createdAt": "2026-08-04T12:00:00+03:00",
      "updatedAt": "2026-08-04T12:00:00+03:00",
        "version": 1
    }
  }
}
```

Errors:

- 400 `invalid_exam_type`;
- 400 `direction_id_required`;
- 400 `date_required` или `invalid_date` для outside LMS;
- 404 `direction_not_found`;
- 409 `exam_create_conflict` только при фактическом unique conflict; одинаковое направление само по себе не конфликт.

### 4.3. Получение базовой карточки

`GET /api/exams/<exam_id>`

Auth: admin. Examiner/student используют свои read endpoints в FEX-13/FEX-14.

Response 200: `{ success, data: { exam: ExamSummary } }`.

Error 404 `exam_not_found`.

### 4.4. Смена направления

`PATCH /api/exams/<exam_id>/direction`

Auth: admin.

Request:

```json
{
  "directionId": 7,
  "expectedVersion": 1
}
```

Response 200: актуальный `ExamSummary`.

Errors:

- 404 `exam_not_found`;
- 404 `direction_not_found`;
- 409 `exam_modified` с `details.currentVersion`;
Смена направления разрешена; UI предупреждает о переносе всех результатов под другое направление. Она увеличивает exam.version, classic configVersion и rating revision; сами оценки не пересчитываются.

## 5. Frontend

### 5.1. Типы

Создать в `lib/exams-v2/types.ts`:

```ts
export type ExamType = "outside_lms" | "classic";
export type ExamReadiness = "not_applicable" | "incomplete" | "ready";

export interface ExamSummary {
  id: number;
  examType: ExamType;
  directionId: number | null;
  directionName: string;
  legacyName: string | null;
  date: string | null;
  startAt: string | null;
  endAt: string | null;
  readiness: ExamReadiness;
  createdAt: string;
  updatedAt: string;
  version: number;
}
```

Ответы API описывать generic envelope types, не передавать backend DTO напрямую в form state.

### 5.2. API client

`lib/exams-v2/admin-api.ts`:

- `fetchAdminExams(params)`;
- `createAdminExam(payload)`;
- `fetchAdminExam(id)`;
- `updateAdminExamDirection(id, payload)`.

Добавить URLSearchParams builder с omit undefined; не собирать query строковой конкатенацией.

### 5.3. Минимальный UI этой задачи

До полного FEX-12 необходимо:

- заменить источник базового admin-списка на `/api/exams`;
- добавить фильтр типа;
- добавить меню создания с двумя типами и combobox направлений;
- после 201 переходить в type-specific workspace placeholder;
- показывать актуальное имя направления, тип и available date fields;
- для legacy без `directionId` показывать admin badge **«Нужно назначить направление»**;
- предоставить смену направления с optimistic conflict message.

Не реализовывать в FEX-01 конфигурационные вкладки, результаты, проведение или student/examiner UI.

## 6. Backend-задачи разработчику

1. Снять и приложить фактический DDL legacy-таблиц.
2. Написать и dry-run миграцию `014_exam_core.sql`.
3. Создать доменный package, repository, service и serializers.
4. Создать/register blueprint и пять endpoint-контрактов и safe lookups.
5. Создать audit report script сопоставления направлений без destructive update.
6. Добавить contract/API/repository tests.
7. Обновить backend README и OpenAPI/ручную API-документацию проекта.

## 7. Frontend-задачи разработчику

1. Создать `exams-v2` types/envelopes/API client.
2. Перевести базовый список на новый endpoint без удаления старых detail views.
3. Реализовать фильтр типа и create dialog.
4. Переиспользовать существующий direction source `/directions`.
5. Реализовать optimistic direction update и обработку 409.
6. Добавить Vitest для query builder, adapters и type labels.
7. Добавить component tests create/filter/error/loading/empty.

## 8. Тесты

### Backend unit

- allowlist обоих типов;
- запрет собственного `name` в payload;
- serializer всегда предпочитает `directions.name`;
- legacy fallback доступен только там, где разрешён.

### Backend integration/API

- migration на копии текущей schema;
- FK запрещает несуществующий direction;
- admin CRUD access, запрет другим ролям;
- pagination/filter/sort;
- rename направления меняет следующий API response;
- одинаковое направление допускает несколько экзаменов;
- optimistic update возвращает 409.

### Frontend

- create payload не содержит name;
- список показывает новое direction name;
- фильтр типов корректно формирует query;
- legacy null direction отображает remediation badge;
- ошибки 404/409 имеют пользовательский текст.

## 9. Definition of Done

- Миграция проверена на копии production schema и имеет описанный rollback.
- Новый blueprint зарегистрирован и защищён role decorators.
- Пять базовых REST-контрактов и safe lookups реализованы и документированы.
- Новый admin list/create flow использует v2 API.
- Старые endpoints и данные не сломаны.
- Переименование направления подтверждено интеграционным тестом.
- Нет дублируемого mutable имени экзамена в новом контракте.
- Все backend/frontend тесты задачи проходят в CI.
- Документ FEX-01 и индекс отмечены как `Готова к разработке` после review техлида и frontend-лида.

## 10. Оценочные пакеты

Для планирования команда оценивает отдельно:

- DB/migration + backfill audit;
- backend domain/API;
- frontend list/create/types;
- automated tests/documentation;
- production rehearsal из FEX-16 не входит в реализацию FEX-01, но блокирует rollout.

## 11. Общие receipts и безопасные lookup API

В 014 создать `exam_admin_commands`: id BIGINT AUTO_INCREMENT PK; actor_admin_id FK admins RESTRICT; idempotency_key CHAR(36); command_type VARCHAR(64); target_exam_id/target_student_id/target_attempt_id nullable scalar; request_hash CHAR(64); receipt JSON nullable; tombstone BOOLEAN default false; created_at/expires_at DATETIME(6); UNIQUE(actor_admin_id,idempotency_key); INDEX(target_exam_id,expires_at). Payload — outcome IDs, не протокол. TTL72h, после удаления очистка payload по FEX-11.

Same-key create сначала резервирует уникальную command row в той же transaction, затем создаёт exam. Никаких записей в GET.

Admin-only:

- GET /api/exams/lookups/students?search=&page=1&limit=20
- GET /api/exams/lookups/examinators?search=&page=1&limit=20

200 paginated `{id,fullName,login}`; login из auth_users по role/ref_id. Exam assignments требуют существующую авторизацию; outside result можно внести по student ID без login. Password/credentials/Telegram не выбирать. Текущий полный users response с credentials для selector не использовать.

GET /api/exams поддерживает dateFrom/dateTo: outside date/classic Moscow start date; null date исключается при фильтре. Сортировка всегда заканчивается exam.id.
