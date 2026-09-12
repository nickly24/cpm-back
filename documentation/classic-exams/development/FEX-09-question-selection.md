# FEX-09. Выдача и замена вопросов

## 1. Результат и зависимости

Вопросы выбираются сервером по одному из immutable definition FEX-08, квоты выполняются точно, замены исключают вопрос навсегда в этой попытке. FEX-10 создаёт раунд в той же transaction; FEX-11 завершает после последнего консенсуса.

## 2. Миграция `021_classic_exam_presented_questions.sql`

```text
classic_exam_attempt_part_progress
- attempt_id FK attempts ON DELETE CASCADE
- source_part_id (immutable scalar ID из definition)
- part_code CHAR(1)
- question_weight INT UNSIGNED
- required_count INT UNSIGNED
- consensus_count INT UNSIGNED DEFAULT 0
- cycle_no INT UNSIGNED DEFAULT 1
- PRIMARY KEY(attempt_id,source_part_id)

classic_exam_presented_questions
- id BIGINT AUTO_INCREMENT PK
- attempt_id FK attempts ON DELETE CASCADE
- definition_question_id FK definition_questions ON DELETE RESTRICT
- sequence_no BIGINT UNSIGNED
- purpose VARCHAR(24): regular/tie_breaker
- cycle_no INT UNSIGNED
- status VARCHAR(24): open/replaced/consensus
- replaces_presented_question_id BIGINT NULL (scalar без обратного каскада)
- replaced_by_examinator_id NULL FK examinators ON DELETE RESTRICT
- replaced_at DATETIME(6) NULL
- created_at DATETIME(6)
- UNIQUE(attempt_id,sequence_no)
- INDEX(attempt_id,definition_question_id,cycle_no)
- INDEX(attempt_id,status,purpose)

classic_exam_attempt_question_usage
- attempt_id FK attempts ON DELETE CASCADE
- definition_question_id FK definition_questions ON DELETE RESTRICT
- last_cycle_no INT UNSIGNED
- ever_presented BOOLEAN
- permanently_excluded BOOLEAN DEFAULT FALSE
- PRIMARY KEY(attempt_id,definition_question_id)
```

Тексты/ответы и части читаются по immutable definition_question; повторение вопроса не требует копировать два TEXT для каждого показа. Это сохранённый снимок, не JOIN к текущему банку.

В attempts добавить `current_presented_question_id BIGINT NULL` как service-validated scalar без FK назад на дочернюю таблицу, и `last_definition_question_id BIGINT NULL`. Указатели и дочерние записи меняются атомарно; integrity test проверяет принадлежность attempt. Не создавать цикл FK attempt→question→attempt.

## 3. Алгоритм regular

При start создать progress из definition, затем первый вопрос. Для следующего regular:

1. Веса выбора части — число оставшихся слотов `required-consensus`.
2. Выбрать часть равновероятно из мультимножества слотов (без выделения огромного массива: cumulative weights).
3. Выбрать равновероятно definition question части, который ещё никогда не показывался.
4. Создать presented + usage + round1, установить current/last pointers.

Readiness FEX-07 гарантирует запас. Наличие заменённых показов не уменьшает основную квоту. Только consensus regular увеличивает progress, один раз.

## 4. Дополнительные вопросы и циклы

Для каждой части:

- cycle1 уже содержит все regular и заменённые показы этой части;
- кандидаты — immutable вопросы, не permanently_excluded и не использованные в текущем cycle;
- когда таких кандидатов нет, увеличить cycle и начать новый проход;
- из выдаваемых кандидатов исключить `attempt.last_definition_question_id` — запрет повтора именно подряд во всей сдаче;
- если в части сейчас допустимого кандидата нет, при any_part выбрать другую часть; не запрещать возврат в часть после вопроса другой части.

Для specific_part применять только указанную часть. Для any_part сначала равновероятно выбрать доступную часть, затем вопрос; не смешивать эту вероятность с количеством её вопросов. Сначала вычислить кандидатов всех разрешённых частей, затем выбрать. Цикл не сбрасывается только ради обхода запрета повтора.

Гарантия доступности: каждую часть проверяет `N_p >= q_p + R`; specific source дополнительно `N_s >= R+2`; any source дополнительно `ΣN_p >= R+2`. После максимум R исключений остаётся достаточно вопросов для продолжения и замены в той же части. Конечный банк допускает неограниченное число дополнительных вопросов через циклы.

## 5. Замена

В общей attempt transaction:

1. current ID совпадает с presentedQuestionId, status=open, нет ни одного vote за этот показ во всех раундах;
2. replacementUsed < snapshotLimit;
3. вычислить доступную замену из той же части/purpose с учётом permanent exclusion текущего вопроса;
4. если кандидата нет — 422, не менять счётчик и не помечать вопрос заменённым;
5. пометить current replaced, его пустой round voided, usage permanently_excluded, actor/time;
6. создать следующий presented с replaces link, usage/round1, переключить pointers и увеличить replacementUsed.

Замена regular использует никогда не показанный вопрос. Замена tie-breaker может открыть следующий цикл. Если тот же definition question ранее был оценён в другом показе, его прежний consensus не стирается и уже начисленные regular points сохраняются.

## 6. REST contracts

Headers JWT + Idempotency-Key. Auth attempt member.

`POST /api/examiner/attempts/{attemptId}/next-question`
```json
{"expectedStateVersion":12,"presentedQuestionId":501}
```
Разрешён только после regular consensus, если ещё осталась квота. При последнем consensus переход уже сделан автоматически FEX-11; старый next не выдаёт extra второй раз.

`POST /api/examiner/attempts/{attemptId}/replace-question`
```json
{"expectedStateVersion":12,"presentedQuestionId":501}
```
Оба возвращают 200 command receipt + актуальный AttemptState.

Current question:
```json
{
  "id":501,"sequenceNo":3,"purpose":"regular","partCode":"B",
  "questionText":"Текст","answerText":"Эталон","weight":4,
  "cycleNo":1,"status":"open","replacesPresentedQuestionId":null,
  "canReplace":true,"round":null,"consensus":null,"canGoNext":false
}
```
round из FEX-10. Progress: `regularConsensus,regularRequired,replacementUsed,replacementLimit,parts[{sourcePartId,code,consensus,required}]`.

Errors: 409 stale_state/question_not_current/question_already_has_votes/current_question_not_resolved; 422 attempt_not_active/replacement_limit_exhausted/question_pool_exhausted. Последняя означает нарушение инварианта/повреждение данных после успешного старта: rollback, диагностический ID, никакого молчаливого повторения запрещённого вопроса.

## 7. Backend-задачи

1. Progress/presented/usage migration и repositories.
2. RNG interface с production secure random и deterministic test RNG.
3. Weighted part selection, question pools/cycles, immutable definition integration.
4. Atomic replace/next, hooks start/round.
5. Bounded current DTO и protocol page repository FEX-11.
6. Index/performance/concurrency tests.

## 8. Frontend-задачи

1. Types/API с явным question ID для next/replace.
2. Plain text, part/weight/purpose/progress, лимит замен.
3. Одна модалка замены с student/question/остатком лимита.
4. Обновление по stateVersion, 409 refresh, no auto retry с новым key.
5. Показ завершённого previous question из receipt при automatic transition.

## 9. Definition of Done и тесты

- Все квоты, отсутствие regular повторов, случайный порядок частей.
- Замены до голоса; одна успешная ветвь при race vote/replace.
- Один и тот же question ID не выпадает подряд, в том числе на границе циклов.
- Any-part из двух частей по одному вопросу, R=0, может продолжаться неограниченно.
- Specific-part два вопроса, R=0, тоже может продолжаться.
- Q=1,R=1,N=3: две замены запрещены, одна разрешена, цикл продолжим.
- Уже начисленный regular consensus не исчезает при замене повторного показа в extra.
- При ошибке выбора исходный вопрос и лимит остаются прежними.
- Два next с одним показом не создают два вопроса; retry того же key повторяет receipt.
- Удаление source question/part не меняет выдачу из definition.
