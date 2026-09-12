# FEX-12. Рабочее место администратора — инженерная декомпозиция

## 1. Результат задачи

В существующей секции кабинета администратора появляется единый desktop-first workspace, через который выполняются все операции FEX-01–FEX-11 без ручного доступа к БД или разрозненных экранов.

Новых таблиц/миграций в FEX-12 нет. Backend read model агрегирует сущности FEX-01–FEX-11; UI не создаёт параллельного состояния домена.

## 2. Backend aggregation contract

CRUD endpoints остаются во владельцах доменных задач. FEX-12 добавляет только read-aggregation, чтобы frontend не создавал waterfall из 8–10 запросов.

### 2.1. Overview

`GET /api/exams/<exam_id>/overview`

Auth: admin.

Для outside LMS:

```json
{
  "success":true,
  "data":{
    "exam":{"id":18,"examType":"outside_lms","directionId":3,"directionName":"Математика","date":"2026-09-12","version":1},
    "outsideLms":{"resultsCount":120,"averageGrade":3.82,"lastUpdatedAt":"..."},
    "availableTabs":["overview","results"]
  }
}
```

Для classic:

```json
{
  "success":true,
  "data":{
    "exam":{"id":41,"examType":"classic","directionId":3,"directionName":"Математика","startAt":null,"endAt":null,"version":1},
    "classic":{
      "readiness":{"isConfigured":false,"errorCount":3},
      "partsCount":4,
      "questionsCount":80,
      "commissionsCount":6,
      "assignmentsCount":120,
      "attempts":{"pending":2,"inProgress":4,"completed":95},
      "resultsCount":95
    },
    "availableTabs":["overview","questions","scoring","commissions","assignments","attempts","results"]
  }
}
```

Endpoint использует aggregate queries и не возвращает question/protocol content. Cache `private, no-store`; frontend refetch после relevant mutations.

### 2.2. Recent activity необязательна

Не добавлять отдельный audit feed в v1. Полный event protocol доступен в attempt detail. Это удерживает scope.

## 3. Frontend architecture

### 3.1. Route/state

Сохранить текущую route-модель `/cabinet/admin/exams`, но использовать query params:

- `examId=<number>`;
- `tab=overview|questions|scoring|commissions|assignments|attempts|results`;
- list filters `type,direction,page,search`.

Back/forward браузера должны восстанавливать выбранный exam/tab. Нельзя хранить единственную навигацию только в component local state.

### 3.2. Component boundaries

```text
AdminExamsSection
├─ AdminExamsList
├─ AdminExamCreateDialog
└─ AdminExamWorkspace
   ├─ OverviewTab
   ├─ OutsideResultsTab
   ├─ ClassicQuestionsTab
   ├─ ClassicScoringTab
   ├─ ClassicCommissionsTab
   ├─ ClassicAssignmentsTab
   ├─ ClassicAttemptsTab
   └─ ClassicResultsTab
```

Каждый tab владеет своим query/mutations и не тянет данные скрытых tabs. Shared header получает only overview.

### 3.3. Data layer

- plain hooks around existing `apiRequest`; не вводить новую state library только ради feature;
- request cancellation через AbortController при смене exam/tab;
- debounced server search 300ms;
- stale response guard;
- mutation response применяет локальное DTO, затем refetch overview;
- common `ApiError` mapper по stable codes.

## 4. UX спецификация

### 4.1. Список

- type tabs/filter outside/classic/all;
- direction filter/search/date sort;
- type badge, актуальное directionName, date/period;
- readiness badge classic;
- create menu two types;
- bulk delete можно сохранить только с individual preview per exam и итоговым подтверждением; реализация optional, single delete обязательна.

### 4.2. Workspace header

- direction name as title;
- type label;
- date/period;
- readiness/summary;
- change direction action;
- delete with preview.

### 4.3. Classic tabs

- Overview: period and dispute config quick summary/errors.
- Questions: FEX-04 UI.
- Scoring: FEX-05 UI.
- Commissions/Assignments: FEX-06 UI.
- Attempts: server list, pending/in-progress/completed, manual refresh, admin full protocol.
- Results: effective result, history, retake and appeal actions.

### 4.4. Active-session warning

При edit configuration/bank/commission template и `inProgress>0` показывать warning, но не блокировать save согласно требованиям. Immutable definition сохраняет правила/банк active; assignment snapshot после ensure не меняется.

### 4.5. Destructive UX

- preview first;
- explicit object label/direction/date;
- show counts;
- одна модалка preview с явной кнопкой подтверждения;
- disable on pending;
- success navigates list and clears examId.

## 5. Backend-задачи

1. Overview query/service/endpoint.
2. Ensure every dependent endpoint returns consistent envelopes/error codes.
3. Add permission/API contract tests.
4. Optimize attempts/results list queries for admin pagination.

## 6. Frontend-задачи

1. Route/query-param state and deep linking.
2. Split existing monolithic `AdminExamsSection` into workspace boundaries.
3. Integrate all FEX-01–FEX-11 panels/contracts.
4. Unified loading/error/empty/conflict states.
5. Destructive flows и import navigation; новых exports нет.
6. Desktop layout and responsive fallback.
7. Component/integration/E2E tests.

## 7. Контрактные ошибки UI

| Code | UX |
|---|---|
| `exam_modified/config_modified/stale_state` | Не перетирать; предложить обновить |
| `exam_not_ready` | Открыть readiness panel |
| `wrong_exam_type` | Показать inconsistency и вернуться в список |
| `import_preview_invalid` | Оставить preview, подсветить строки |
| `delete_confirmation_invalid` | Запросить новый preview |
| 403/404 | Закрыть workspace и показать access/not found |

## 8. Тесты

- route deep links and back/forward;
- type-specific tabs;
- no hidden-tab requests;
- overview refresh after mutations;
- debounced search/cancel stale request;
- every contract error UX;
- active warning not blocking;
- delete preview token expiry;
- large paginated tables;
- desktop primary and tablet fallback snapshots.

## 9. Definition of Done

- Все admin workflows FEX-01–FEX-11 доступны из единой секции.
- URL отражает выбранный объект/tab.
- Нет client-side duplicated business rules.
- Overview не создаёт N+1/waterfall.
- Destructive/import flows безопасны и проверяемы.
- Existing unrelated admin sections не регрессировали.
- Component/E2E tests green.

## 10. Счётчики и состояние форм

assignmentsCount — назначения с пересдачами; assignedStudentsCount — DISTINCT student. attempts.completed считает попытки, resultsCount — студентов с current completed, поэтому два completed одного студента дают один result. Вкладка results использует GET /api/exams/{examId}/classic/results, не дедупликацию страницы attempts на клиенте.

У разделов независимый dirty-state; уход с несохранённым — сохранить/отменить/остаться. После409 введённые значения не стирать. Версии/key не пользовательские поля. Для ошибок нужен ApiError.code/details и разрешённый CORS Idempotency-Key.
