# FEX-07. Валидатор готовности — инженерная декомпозиция

## 1. Результат задачи

Backend вычисляет готовность конфигурации экзамена и возможность старта конкретного студента. Один набор стабильных кодов используется admin UI, examiner UI и всеми start-командами.

Изменений БД в этой задаче нет: валидатор читает модели FEX-03–FEX-06 и не хранит вычисляемый readiness как отдельное состояние.

## 2. Модель проверки

Не хранить boolean `is_ready` в БД: он устаревает после любого изменения. Создать pure-ish `ClassicExamReadinessService`, который собирает агрегированные counts/settings и возвращает report.

### 2.1. Уровни

1. `configuration` — экзамен в целом без динамического времени/студента.
2. `assignment` — конкретный student/attempt number и его snapshot composition/privilege.
3. `start` — configuration + assignment + current server time + attempt availability.

### 2.2. Error DTO

```text
ReadinessError
- code: stable snake_case
- scope: configuration | part | scoring | assignment | privilege | window | attempt
- section: basic | questions | scoring | commissions | assignments
- entityId: int | null
- field: string | null
- message: Russian text
- blocking: true
```

Warnings не влияют на ready и имеют отдельный массив.

## 3. Проверки

### Configuration

- direction exists;
- settings row;
- start/end complete and end>start;
- >=1 part;
- unique valid codes (DB additionally protects);
- each weight/count positive;
- each question/answer non-empty (normally protected at write);
- max score >=5;
- complete six thresholds strictly increasing;
- fractional config coherent;
- specific tie part exists/belongs exam;
- для каждого part базово bankSize>=questionCount (без привилегий остальных студентов);
- extra specific: минимум2 вопроса выбранной части; extra any: минимум2 суммарно; проверка нужна при любом halfMode, даже если повтор не настроен (последний regular нельзя немедленно повторить).

### Assignment

- assignment exists for requested attemptNo;
- 1..6 snapshot members;
- all examiner accounts still exist/role valid;
- privilege non-negative;
- student exists;
- для конкретного R: каждый part Np>=qp+R; extra specific Ns>=R+2; extra any ΣNp>=R+2. Это гарантирует основной запас, замену в той же части и непрерывные циклы.
- Не включать сюда maxReplacement других студентов; их проблемы показывать в assignment report, не блокировать всех.

### Start

- configuration/assignment error-free;
- server now in inclusive window;
- attemptNo is 1 or admin-created 2;
- старт только собственного pending attempt; existing in_progress/completed возвращается lifecycle сервисом без повторного старта;
- retake only after completed first (FEX-11 policy hook).

## 4. Кеширование/производительность

- Не кешировать dynamic start result.
- Configuration report можно кешировать in-process максимум 30 секунд по `(exam_id, max(updated_at...))`, но v1 предпочтительно оптимизированный aggregate SQL без кеша.
- Readiness list admin должен избегать N+1: batch counts by exam IDs.
- Детальный endpoint может выполнять несколько indexed queries.

## 5. REST-контракты

### 5.1. Полный admin report

`GET /api/exams/<exam_id>/classic/readiness`

Auth: admin.

```json
{
  "success": true,
  "data": {
    "readiness": {
      "isConfigured": false,
      "errors": [
        {
          "code":"part_bank_too_small",
          "scope":"part",
          "section":"questions",
          "entityId":12,
          "field":null,
          "message":"В части A нужно не менее 4 вопросов, доступно 3",
          "blocking":true,
          "details":{"partCode":"A","required":4,"available":3}
        }
      ],
      "warnings": [],
      "checkedAt":"2026-08-04T14:00:00+03:00"
    }
  }
}
```

### 5.2. Проверка назначения

`GET /api/exams/<exam_id>/classic/assignments/<assignment_id>/readiness`

Auth: admin or assigned examinator. Для examiner чужой assignment → 404.

Response:

```json
{
  "success":true,
  "data":{
    "configurationReady":true,
    "assignmentReady":true,
    "canStartNow":false,
    "errors":[{"code":"exam_not_started","scope":"window","section":"basic","message":"...","blocking":true}],
    "checkedAt":"..."
  }
}
```

## 6. Внутренний contract

Все start/ready endpoints вызывают:

```text
assert_can_prepare(exam_id, assignment_id)
assert_can_start(exam_id, assignment_id, attempt_no, now_utc)
```

При ошибке выбрасывается domain exception с тем же error report; blueprint возвращает 422 `exam_not_ready` или конкретный window/attempt code, сохраняя `details.readiness`.

Frontend никогда не считается достаточной проверкой.

## 7. Frontend

### Admin

- readiness summary badge в list/detail;
- detail panel со сгруппированными errors;
- click error navigates на section/entity;
- refetch после mutation частей/scoring/assignments;
- не создавать локальный алгоритм ready.

### Examiner

- неполный exam/assignment остаётся в списке;
- показать blocking messages read-only;
- readiness/prepare/start buttons disabled по server report;
- ручное обновление report.

## 8. Backend-задачи

1. Error code registry и DTO serializer.
2. Aggregate repository queries/batch mode.
3. Configuration/assignment/start policies.
4. Два GET endpoints.
5. Exception adapter для FEX-08.
6. Unit/integration/performance tests.

## 9. Frontend-задачи

1. Types/error mapping.
2. Admin grouped panel/navigation.
3. Examiner unavailable state.
4. Refetch triggers and tests.

## 10. Тесты

- каждый code отдельным fixture;
- multiple simultaneous errors returned together;
- высокий лимит одного студента не блокирует другого;
- без assignments проверяется базовая конфигурация;
- specific student reserve including source guarantee;
- exact start/end boundary;
- attempt policy hooks;
- authorization assignment;
- batch list query count (no N+1 regression).

## 11. Definition of Done

- Единственный backend readiness source используется всеми consumers.
- Коды стабильны и задокументированы.
- UI не дублирует бизнес-расчёт.
- Старт direct API call не обходит ошибки.
- Reports performant for admin list and detail.
- FEX-08 интегрируется через assert contracts.
- CI green.

## 12. Граничные состояния

`canPrepare`=configuration+assignment valid, время не требуется. `canStartNow` дополнительно требует pending, allReady, период. Сам GET ошибки окна не создаёт сдачу. Для existing active/completed prepare validator не применяется при открытии. Configuration report не обращается к max privileges: admin дополнительно видит count назначений с ошибками.

Ограничения capacity FEX-04 возвращаются отдельными кодами. ReadinessError всегда содержит details object (пустой при отсутствии параметров). Extra source references проверяются к текущему exam. Readiness при start и snapshot берутся под тем же exam lock — изменения банка между проверкой и выдачей не допускаются.
