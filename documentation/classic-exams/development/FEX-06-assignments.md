# FEX-06. Комиссии, назначения и привилегии — инженерная декомпозиция

## 1. Результат задачи

Администратор формирует внутри classic exam шаблоны комиссий, назначает студентам состав первой сдачи, задаёт общий для экзамена лимит замен и массово загружает экзаменационный лист.

## 2. Изменения БД

### 2.1. Миграция `019_classic_exam_assignments.sql`

```text
classic_exam_commissions
- id BIGINT PK
- exam_id BIGINT FK exams ON DELETE CASCADE
- name VARCHAR(120) NOT NULL
- created_at, updated_at
- version BIGINT UNSIGNED DEFAULT 1
- UNIQUE(exam_id, name)
```

```text
classic_exam_commission_members
- commission_id BIGINT FK commissions ON DELETE CASCADE
- examinator_id BIGINT FK examinators ON DELETE RESTRICT
- position SMALLINT UNSIGNED NOT NULL
- PRIMARY KEY(commission_id, examinator_id)
- UNIQUE(commission_id, position)
```

```text
classic_exam_assignments
- id BIGINT PK
- exam_id BIGINT FK exams ON DELETE CASCADE
- student_id BIGINT FK students ON DELETE RESTRICT
- attempt_no TINYINT UNSIGNED NOT NULL DEFAULT 1
- source_commission_id BIGINT NULL FK commissions ON DELETE SET NULL
- created_by FK admins ON DELETE RESTRICT
- commission_name_snapshot VARCHAR(120) NOT NULL
- history_generation BIGINT UNSIGNED DEFAULT 1 (используется у attemptNo1)
- version BIGINT UNSIGNED DEFAULT 1
- created_at, updated_at
- UNIQUE(exam_id, student_id, attempt_no)
- INDEX(exam_id, student_id)
```

`attempt_no` допускает 1/2; FEX-06 создаёт только 1. FEX-11 использует ту же модель для пересдачи.

```text
classic_exam_assignment_members
- assignment_id BIGINT FK assignments ON DELETE CASCADE
- examinator_id BIGINT FK examinators ON DELETE RESTRICT
- position SMALLINT UNSIGNED NOT NULL
- full_name_snapshot VARCHAR(255) NOT NULL
- PRIMARY KEY(assignment_id, examinator_id)
- UNIQUE(assignment_id, position)
```

Эти строки — снимок. Изменение template members не обновляет assignment members.

```text
classic_exam_student_privileges
- exam_id BIGINT FK exams ON DELETE CASCADE
- student_id BIGINT FK students ON DELETE RESTRICT
- replacement_limit INT UNSIGNED NOT NULL DEFAULT 0
- version BIGINT UNSIGNED NOT NULL DEFAULT 1
- created_at, updated_at
- PRIMARY KEY(exam_id, student_id)
```

```text
classic_exam_assignment_import_sessions
- id, exam_id, source_filename
- preview_payload JSON
- created_by, created_at, expires_at, committed_at
- FK exam ON DELETE CASCADE
```

## 3. Backend

### 3.1. CommissionService

- classic type guard;
- unique trimmed name, auto-name `Комиссия N` under transaction;
- 1..6 unique examinators с существующей auth_users role=examinator/ref_id; отдельного is_active в схеме не предполагать;
- stable ordered members;
- update affects template only;
- delete разрешён: SET NULL source link, имена/состав assignment берутся из snapshots; version/CAS и одна UI confirmation.

### 3.2. AssignmentService

- create attempt_no=1 from commission snapshot;
- one first assignment per exam/student;
- update commission только пока у этого assignment нет attempt (в том числе pending). Состав после ensure меняется только через очистку истории; studentId/attemptNo неизменяемы;
- delete assignment only while no attempt exists; physical result deletion belongs FEX-11;
- upsert privilege independently;
- return assignment with snapshot members, never dynamically from template.

### 3.3. ImportService

Headers:

- `student_id` и/или `student_login` с проверкой совпадения;
- `examinator_1_id` … `examinator_6_id` и/или соответствующие `_login`; старые колонки `examinator_N` — только login;
- `replacement_limit`.

Rules:

- at least one examiner;
- duplicates within row error;
- unknown/ambiguous accounts error;
- existing assignment error until row removed/edited;
- identical unordered member sets reuse existing template or create one auto-name;
- commit atomically creates templates, assignments, snapshots, privileges;
- name collision retry under lock;
- no user creation.

### 3.4. Просмотр листа

Использовать paginated assignments API. Нового экспорта нет по прямому решению заказчика.

## 4. REST-контракты комиссий

### 4.1. List/create

- `GET /api/exams/<exam_id>/classic/commissions`;
- `POST /api/exams/<exam_id>/classic/commissions`.

Create request:

```json
{
  "name": "Комиссия 1",
  "examinatorIds": [12, 18, 27]
}
```

Result commission:

```json
{
  "id": 9,
  "name": "Комиссия 1",
  "members": [
    {"id":12,"fullName":"Иванов И. И.","position":1}
  ],
  "assignedStudentsCount": 14,
  "updatedAt": "...",
  "version": 1
}
```

### 4.2. Update/delete

- `PUT /api/exams/<exam_id>/classic/commissions/<id>` full name+members+expectedVersion;
- `DELETE /api/exams/<exam_id>/classic/commissions/<id>?expectedVersion=...` → 204.

Update response дополнительно: `affectedExistingAssignments: 0`, подчёркивая snapshot behavior.

Errors:400 invalid_commission_size/duplicate_member;404 examinator_not_found;409 commission_name_conflict/commission_modified.

## 5. REST-контракты назначений

### 5.1. Список

`GET /api/exams/<exam_id>/classic/assignments?page=1&limit=50&search=&commissionId=&status=`

До FEX-08 status всегда `not_started`; contract резервирует future values.

Item:

```json
{
  "id": 201,
  "student":{"id":125,"fullName":"Петров Пётр","login":"st125"},
  "attemptNo":1,
  "sourceCommissionId":9,
  "commissionName":"Комиссия 1",
  "members":[{"id":12,"fullName":"Иванов И. И.","position":1}],
  "replacementLimit":2,
  "privilegeVersion":1,
  "status":"not_started",
  "updatedAt":"...",
  "version":1,
  "historyGeneration":1
}
```

### 5.2. Create/update/delete

- `POST /api/exams/<exam_id>/classic/assignments` body `{studentId,commissionId,replacementLimit?,expectedPrivilegeVersion?}`;
- `PUT /api/exams/<exam_id>/classic/assignments/<id>` body `{commissionId,replacementLimit,expectedVersion,expectedPrivilegeVersion}`;
- `DELETE /api/exams/<exam_id>/classic/assignments/<id>?expectedVersion=...`.

Create/update под exam exclusive lock copies template members/name. Create допускает только attemptNo1; attemptNo2 создаётся FEX-11. Update допускает commissionId и replacementLimit до ensure; studentId/attemptNo неизменяемы. Если обновляется уже существующая привилегия, требуется expectedPrivilegeVersion; отсутствие token не означает разрешение перезаписать её.

Errors:409 assignment_already_exists/assignment_has_attempt/assignment_modified/privilege_modified;404 commission_not_found/student_not_found;400 invalid_replacement_limit.

### 5.3. Отдельная привилегия

`PUT /api/exams/<exam_id>/classic/students/<student_id>/privilege`

```json
{"replacementLimit":3,"expectedVersion": 1}
```

Используется, если UI редактирует privilege без смены состава.

## 6. Импорт API

- `POST /api/exams/<exam_id>/imports/assignments/parse` multipart;
- GET/PUT /api/exams/<exam_id>/imports/assignments/sessions/<session_id>;
- POST /api/exams/<exam_id>/imports/assignments/sessions/<session_id>/commit;
Полные пути sessions/preview/commit и схема указаны в [контракте импортов](./01-contracts-and-imports.md).

Preview group содержит `commissionKey` как sorted examiner IDs, proposed/existing template и rows. Commit response counts templatesCreated, assignmentsCreated, privilegesUpdated, errors.

## 7. Frontend

### 7.1. Комиссии

- list/card templates;
- create/edit name and 1–6 examinator multi-select;
- duplicate prevention client-side plus server validation;
- explicit text: изменения не затронут назначенных студентов;
- delete confirmation with assigned count.

### 7.2. Экзаменационный лист

- server pagination/search/filter;
- columns student, commission members, replacement limit, status;
- create/edit assignment modal;
- member snapshot displayed, not fetched dynamically from current template;
- import preview with conflict row edit/remove;
- признак изменённого snapshot и состояние назначения;

## 8. Backend-задачи

1. Migration/entities/repositories.
2. Commission CRUD service/API.
3. Assignment/privilege service/API.
4. Import parse-preview-commit.
5. Список привилегий, CAS и safe account lookup.
6. Policy seam for future attempt lock.

## 9. Frontend-задачи

1. Types/API.
2. Commission management.
3. Assignment list/forms.
4. Import preview.
5. Отдельный список/редактор привилегий.
6. Tests.

## 10. Тесты

- commission 1/6 accepted, 0/7 rejected;
- duplicate/inactive/unknown examiner;
- template update leaves snapshot unchanged;
- same unordered set import grouping;
- assignment uniqueness and snapshot ownership;
- privilege 0 and reset semantics interface;
- existing assignment import conflict;
- atomic rollback and concurrent auto-name;
- privilege list CAS and access;

## 11. Definition of Done

- Схема хранит templates и immutable assignment snapshots.
- CRUD/import/list contracts реализованы.
- Привилегия едина для exam+student.
- Никакой импорт не перезаписывает назначение скрыто.
- UI покрывает ручной и массовый workflow.
- FEX-07/FEX-08 могут читать assignments через service/repository contracts.
- CI green.


## 12. Привилегии и account contracts

GET /api/exams/{examId}/classic/privileges?page=1&limit=20&search= — paginated student/id/limit/version; привилегию можно завести до назначения комиссии.
GET /api/exams/{examId}/classic/students/{studentId}/privilege —200 `{replacementLimit:0,version:0}` если записи нет, без INSERT.
PUT по тому же пути `{replacementLimit:3,expectedVersion:0}` создаёт запись; existing требует её текущую version. Все значения0..100. DELETE с expectedVersion удаляет entitlement, future limit=0; active attempt не меняется.

Привилегия фиксируется при start, не ensure; её изменение влияет только на ещё не начатые attempts. Удаление assignment не удаляет entitlement. При create назначения без replacementLimit существующий лимит сохраняется; отсутствующий трактуется0. Импорт, меняющий existing privilege, требует явного совпадения preview-version, показывая old/new value; existing assignment всегда конфликт.

Опции students/examinators берутся из safe lookups FEX-01. Шаблоны с одинаковым составом, но разными именами разрешены; импорт переиспользует минимальный существующий ID с точно тем же множеством участников. Exam lock защищает grouping/name allocation от гонки; удалённый template не меняет assignment snapshots. В DTO отдавать version и historyGeneration; у assignment2 generation берётся из основного assignment.
