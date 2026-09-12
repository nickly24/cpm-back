# FEX-13. Кабинет экзаменатора — инженерная декомпозиция

## 1. Результат задачи

Роль `examinator` получает рабочий mobile/desktop кабинет: список назначенных экзаменов и студентов, подготовку полного состава, проведение вопросов, замену, голосование, переголосование и завершение через ручное REST-обновление.

Новых таблиц/миграций в FEX-13 нет. Кабинет читает и изменяет модели FEX-06 и FEX-08–FEX-11 только через их service contracts.

## 2. Backend read contracts

Lifecycle commands принадлежат FEX-08–FEX-11. FEX-13 добавляет scoped catalogs.

### 2.1. Назначенные экзамены

`GET /api/examiner/exams?page=1&limit=20&state=all&search=`

Auth: examinator.

Возвращать только exams, где actor присутствует в assignment_members attemptNo1/2.

Item:

```json
{
  "id":41,
  "directionId":3,
  "directionName":"Математика",
  "startAt":"...",
  "endAt":"...",
  "readiness":"ready",
  "assignedStudentsCount":20,
  "assignments":{"total":21,"notStarted":8,"pendingReady":2,"inProgress":1,"completed":10},
  "hasUnavailableAssignments":false
}
```

State filter: `all,incomplete,upcoming,active,ended` по времени/settings; null start/end = incomplete; ended exam с in-progress attempt остаётся доступен.

### 2.2. Студенты экзамена

`GET /api/examiner/exams/<exam_id>/students?page=1&limit=50&status=&search=`

Только assignments, snapshot которого содержит actor. Если student имеет retake assignment с другой комиссией, original examiner видит историческую completed first attempt, но не получает управление retake assignment, если не состоит в новой комиссии.

Item:

```json
{
  "assignmentId":201,
  "attemptNo":1,
  "student":{"id":125,"fullName":"Петров Пётр"},
  "commission":[{"id":12,"fullName":"...","ready":true}],
  "replacementLimit":2,
  "status":"in_progress",
  "phase":"regular_questions",
  "attemptId":301,
  "progress":{"answered":2,"required":6},
  "historyGeneration":1,
  "canPrepare":false,
  "canStart":false,
  "unavailableReasons":[]
}
```

Search ФИО/ID. Status allowlist not_started,pending_ready,in_progress,completed.

### 2.3. Access invariant

Catalog query и command membership используют один repository predicate. Нельзя показывать student в списке, а затем отказывать из-за другой логики membership.

## 3. Frontend architecture

Использовать существующую навигацию `examinator → exams`; заменить EmptySection реальной секцией.

```text
ExaminatorExamsSection
├─ AssignedExamsList
├─ AssignedStudentsList
└─ ExamAttemptWorkspace
   ├─ CommissionReadiness
   ├─ QuestionCard
   ├─ VotePanel
   ├─ DisputeHistory
   ├─ ProgressPanel
   └─ CompletedSummary
```

URL/query params содержат `examId,assignmentId,attemptId`, чтобы reload/deep-link восстанавливался после auth.

## 4. UX state machine

### Not started

- показать student/commission/replacement limit;
- `Открыть сдачу` вызывает ensure;
- validation errors отображаются read-only.

### Pending ready

- список всех members и ready marks;
- current user button ready;
- ready irreversible; parallel ready не требуют refresh между чужими подтверждениями;
- start available каждому member only after all ready/server canStart.

### In progress

- topbar student/direction/attempt/progress/manual Refresh;
- question/answer plain text with preserved lines;
- replace before votes with confirmation/counter;
- vote fixed controls с presentedQuestionId/roundId; parallel peers не дают stale_state;
- after own vote waiting count;
- disputed history + next round;
- consensus + Next только пока есть regular квота; последний consensus автоматически публикует result/extra;
- tie-breaker visually distinguished but same vote controls.

### Completed

- read-only total/max/grade;
- no admin-only appeal controls;
- back to students.

## 5. Сетевое поведение

- Никаких timers/intervals/EventSource/WebSocket.
- Refresh only user click, page load, tab focus не должен автоматически poll; одноразовый fetch при navigation допустим.
- Mutation button генерирует/reuses idempotency key for retry.
- Применять bounded AttemptState только если version>=текущей и attempt/account совпадают. История загружается страницами.
- Network uncertainty after mutation: offer `Проверить состояние`; не предлагать повтор с новым key автоматически.
- 409 stale → prominent refresh CTA.

## 6. Responsive design

- Mobile single column; sticky bottom vote/actions safe-area aware.
- Desktop two columns: question/answer main, progress/commission side.
- Full text readable, not truncated.
- Buttons touch target >=44px.
- Six-member lists compact but names fully accessible.
- Conflict votes use cards/table depending width.

## 7. Backend-задачи

1. Examiner catalog repositories without N+1.
2. Two list endpoints/pagination/filter.
3. Align membership predicate with commands.
4. Role serializer counts/progress.
5. Query/performance/auth tests.

## 8. Frontend-задачи

1. Catalog DTO/API and section routing.
2. Exam/student lists.
3. Attempt workspace composition of FEX-08–FEX-11 primitives.
4. Manual refresh/idempotency/network uncertainty UX.
5. Mobile/desktop responsive styles.
6. Accessibility and component/E2E tests.

## 9. Тесты

- examiner sees only snapshot memberships;
- retake new commission visibility;
- ended with active attempt accessible;
- role 403/foreign 404;
- reload each state;
- no interval requests verified with fake timers/network spy;
- offline/timeout after vote then refresh;
- mobile 320px and desktop widths;
- one/six-member flows;
- stale concurrent actions.

## 10. Definition of Done

- Examinator navigation no longer opens EmptySection.
- Full oral exam is conductible on phone and desktop.
- No realtime/background transport introduced.
- Membership/privacy consistent across list and commands.
- State survives reload/network ambiguity.
- All server transitions are presented without client business calculations.
- E2E with 1 and 6 accounts passes.

## 11. Receipt, списки и приватность

В current state только lastCompletedRound, остальная история страницами FEX-11. При auto transition показать результат прошлого вопроса из receipt вместе с новым вопросом/итогом. Не повторять старый vote автоматически в новом round.

Строка списка — assignment; один student может иметь first/retake строки при членстве actor в обеих. Счётчики различают students/assignments. Старый examiner не видит retake protocol без membership; при изменении template доступ active сохраняется по attempt snapshot.
