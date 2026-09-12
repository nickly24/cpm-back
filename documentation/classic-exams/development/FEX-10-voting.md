# FEX-10. Голосование комиссии — инженерная декомпозиция

## 1. Результат задачи

Каждый член комиссии независимо и необратимо оценивает текущий вопрос. Сервер скрывает чужие голоса до полного раунда, фиксирует консенсус либо автоматически открывает новый раунд, сохраняя полную персональную историю для комиссии и администратора.

## 2. Изменения БД

### 2.1. Миграция `022_classic_exam_voting.sql`

```text
classic_exam_vote_rounds
- id BIGINT PK
- attempt_id BIGINT FK attempts ON DELETE CASCADE
- presented_question_id BIGINT FK presented_questions ON DELETE CASCADE
- round_no INT UNSIGNED NOT NULL
- status VARCHAR(24) NOT NULL (`open|disputed|consensus|voided`)
- consensus_value DECIMAL(2,1) NULL
- opened_at DATETIME(6)
- completed_at DATETIME(6) NULL
- UNIQUE(presented_question_id, round_no)
- INDEX(attempt_id, status)
```

```text
classic_exam_votes
- id BIGINT PK
- round_id BIGINT FK vote_rounds ON DELETE CASCADE
- examinator_id BIGINT FK examinators ON DELETE RESTRICT
- value DECIMAL(2,1) NOT NULL
- created_at DATETIME(6)
- UNIQUE(round_id, examinator_id)
```

Значение проверяется приложением exact allowlist `{0,0.5,1}`; добавить CHECK при гарантированной поддержке production MySQL.

В `classic_exam_presented_questions` добавить:

- `consensus_value DECIMAL(2,1) NULL`;
- `weighted_score DECIMAL(12,1) NULL` (regular = consensus × weight; extra всегда 0);
- `consensus_at DATETIME(6) NULL`.

## 3. Backend

### 3.1. Round lifecycle

- При выдаче каждого presented question FEX-09 в той же транзакции создаёт round 1 open.
- У текущего open question ровно один open round. При замене неоценённого показа его пустой round становится voided (не удаляется из аудита), новый question получает свой round1.
- Actor может проголосовать, если входит в attempt members и ещё не имеет vote.
- После insert посчитать votes count against immutable members count.
- Пока не все — round open, state version increment.
- После последнего голоса сравнить Decimal values.
- Все одинаковы: round consensus, presented consensus/weighted score/status consensus, progress regular count increment для regular.
- Есть отличие: round disputed, immediately create round N+1 open.
- Последний regular consensus автоматически вызывает FEX-11, если квота исчерпана; extra consensus тоже вызывает FEX-11. StateVersion увеличивается ровно один раз за всю vote-команду, включая выдачу extra/завершение.
- В той же transaction сохраняется компактный actor-scoped receipt, не полный response.

### 3.2. Неизменяемость

- Нет update/delete vote API.
- Duplicate unique превращается в 409 `vote_already_cast`, если это не retry того же idempotency key.
- Admin appeal не меняет votes/consensus.
- DB write service не предоставляет public method изменения завершённого round.

### 3.3. Role-aware serializer

Для open round examiner видит:

- roundNo;
- votesReceived/votesRequired;
- собственный `myVote` либо null;
- `hasVoted`;
- чужие votes отсутствуют в JSON, не masked values.

После completed round комиссия/admin видят список `{examinatorId,fullName,value,votedAt}`. Student serializer никогда не включает rounds/votes, только presented consensus.

## 4. REST-контракт

### 4.1. Подать голос

`POST /api/examiner/attempts/<attempt_id>/vote`

Headers: JWT + `Idempotency-Key`.

```json
{
  "presentedQuestionId": 501,
  "roundId": 602,
  "value": 0.5
}
```

200 command receipt + updated AttemptState. Global expectedStateVersion для vote не требуется: все шесть членов могут отправить голос из одной прочитанной версии. Под attempt lock проверяются текущие question/round IDs, членство и UNIQUE(round_id,examinator_id). Устаревший round не заменяется автоматически.

Current question extension:

```json
{
  "round": {
    "id": 602,
    "roundNo": 2,
    "status": "open",
    "votesReceived": 2,
    "votesRequired": 3,
    "hasVoted": true,
    "myVote": 0.5
  },
  "lastCompletedRound": {
    "id":601,"roundNo":1,"status":"disputed",
    "votes":[
      {"examinatorId":12,"fullName":"Иванов И.И.","value":1,"votedAt":"2026-09-12T10:05:00+03:00"},
      {"examinatorId":18,"fullName":"Петров П.П.","value":0,"votedAt":"2026-09-12T10:05:01+03:00"},
      {"examinatorId":27,"fullName":"Сидоров С.С.","value":0.5,"votedAt":"2026-09-12T10:05:02+03:00"}
    ]
  },
  "completedRoundsCount":1,
  "consensus":null,
  "canGoNext":false
}
```

В response включён только последний завершённый раунд; полный protocol — страницами по FEX-11. Admin видит уже отправленные голоса открытого раунда; examiner до его завершения видит только свой.

При консенсусе `round.status=consensus`, `consensus=1`, `canGoNext=true` только если у regular осталась квота. Для tie-breaker последний vote может вызвать FEX-11 transition и вернуть следующий extra question либо completed attempt.

Errors:

- 400 `invalid_vote_value`;
- 404 чужой/not found;
- 409 `vote_already_cast`, `round_not_current`, `question_not_current`, `question_already_resolved`, `idempotency_key_reused`;
- 422 `attempt_not_active`.

Отдельные endpoints «переголосовать» или «закрыть раунд» отсутствуют.

## 5. Frontend

### Examiner

- три фиксированные кнопки с точными labels;
- после click кнопки pending, затем все disabled permanently for current round;
- own vote visible;
- count ожидания without names/values open peers;
- disputed completed round раскрывается персонально;
- новый round отображает fresh controls;
- consensus card + Next;
- no edit confirmation because vote irreversible; перед click допустима короткая явная подпись.

### Admin

Shared protocol types preserve every round/vote; monitoring UI FEX-12.

### Security

Frontend не получает hidden data заранее. Нельзя просто скрывать DOM/CSS.

## 6. Backend-задачи

1. Migration/round/vote repositories.
2. Round creation hook FEX-09.
3. Vote transition service.
4. Role serializers.
5. POST endpoint/idempotency.
6. Consensus progress integration.
7. Concurrency/privacy tests.

## 7. Frontend-задачи

1. Round/vote DTO extensions.
2. Vote controls/wait/conflict/consensus components.
3. API mutation and stale handling.
4. Tests proving hidden votes absent before completion.

## 8. Тесты

- one-member commission immediate consensus;
- six-member incomplete/full round;
- 0/0.5/1 consensus;
- every divergence creates exactly one next round;
- шесть одновременных голосов из одного состояния принимаются без stale_state;
- simultaneous last votes; один finalization/новый round;
- запоздалый vote не попадает в новый round;
- replay не раскрывает данные другого actor;
- duplicate/idempotent retry;
- actor not member;
- replaced/current mismatch;
- weighted score snapshot;
- examiner/admin/student serializer privacy;
- no mutation after consensus.

## 9. Definition of Done

- Votes immutable at API/service/schema level.
- Hidden votes truly absent from response.
- Unlimited re-voting preserves history.
- Consensus atomically updates presented/progress.
- Голос scoped к question/round/actor и idempotent; stateVersion обновляется для чтения, но не отклоняет независимые голоса.
- UI covers waiting, conflict and consensus on mobile/desktop primitives.
- FEX-11 receives tie-breaker consensus hook.
