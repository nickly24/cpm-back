# FEX-08. Жизненный цикл сдачи и конкурентность

## 1. Результат и зависимости

Одна сдача на назначение, бессрочная готовность полного состава, атомарный старт с неизменяемой версией правил/банка. Зависимости FEX-06/07; выдача первого вопроса интегрируется FEX-09. Проведение включается только после FEX-10/11.

## 2. Миграция `020_classic_exam_attempts.sql`

Здесь и далее ID новых сущностей — BIGINT AUTO_INCREMENT; внешние role/exam FK имеют реальный тип целевого PK. Все NOT NULL, кроме явно указанных nullable.

```text
classic_exam_definition_versions
- id PK
- exam_id FK exams ON DELETE CASCADE
- config_version BIGINT
- config_json JSON (parts: sourcePartId/code/weight/quota; thresholds; fractional settings; window)
- created_at DATETIME(6)
- UNIQUE(exam_id, config_version)

classic_exam_definition_questions
- id PK
- definition_id FK definition_versions ON DELETE CASCADE
- source_question_id (исходный ID как scalar без FK)
- source_part_id (исходный ID как scalar без FK)
- part_code CHAR(1)
- question_text TEXT
- answer_text TEXT
- UNIQUE(definition_id, source_question_id)
- INDEX(definition_id, source_part_id, id)

classic_exam_attempts
- id PK
- exam_id FK exams ON DELETE CASCADE
- student_id FK students ON DELETE RESTRICT
- assignment_id FK assignments ON DELETE RESTRICT
- definition_id BIGINT NULL FK definition_versions ON DELETE RESTRICT
- attempt_no TINYINT (1/2)
- status VARCHAR(24): pending_ready/in_progress/completed
- phase VARCHAR(32): preparation/regular_questions/tie_breaker/completed
- state_version BIGINT UNSIGNED DEFAULT 1
- history_generation BIGINT UNSIGNED (из основного assignment)
- student_name_snapshot VARCHAR(255)
- replacement_limit_snapshot INT UNSIGNED NULL (фиксируется при start)
- replacement_used INT UNSIGNED DEFAULT 0
- started_at, completed_at DATETIME(6) NULL
- created_at, updated_at DATETIME(6)
- UNIQUE(exam_id, student_id, attempt_no)
- UNIQUE(assignment_id)
- INDEX(exam_id,status,id)
- INDEX(student_id,exam_id,id)

classic_exam_attempt_members
- attempt_id FK attempts ON DELETE CASCADE
- examinator_id FK examinators ON DELETE RESTRICT
- full_name_snapshot VARCHAR(255)
- position TINYINT (1..6)
- ready_at DATETIME(6) NULL
- PRIMARY KEY(attempt_id,examinator_id)
- UNIQUE(attempt_id,position)

classic_exam_attempt_commands
- id PK
- attempt_id FK attempts ON DELETE CASCADE
- actor_examinator_id FK examinators ON DELETE RESTRICT
- idempotency_key CHAR(36)
- command_type VARCHAR(32)
- request_hash CHAR(64)
- receipt JSON (только outcome IDs/version)
- created_at DATETIME(6)
- UNIQUE(attempt_id,actor_examinator_id,idempotency_key)
```

Все вопросы определения копируются при первом start на configVersion, один раз для всех сдач этой версии. Дальнейший start переиспользует definition. Части/банк текущего экзамена могут изменяться; в definition нет FK на mutable parts/questions, поэтому старые IDs и тексты не исчезают. Snapshot персональных участников принадлежит attempt, не общему definition.

В FEX-06 в основном assignment хранится `history_generation` default1; его увеличение при очистке защищает ensure от старого запроса. Поколение не является номером попытки.

## 3. Состояния

```text
нет сдачи → ensure → pending_ready
pending_ready → готовность каждого члена → pending_ready/allReady
pending_ready + allReady + окно старта → start → in_progress
in_progress → regular consensus → next или auto final/tie-breaker
tie-breaker consensus → auto repeat либо completed
completed → только чтение/апелляция администратора
```

Нет pause, timeout, кворума или замены участника. Ожидающая сдача сохраняется после конца окна, но начать её можно лишь после изменения окна администратором.

## 4. Сервисы и транзакции

### Ensure

Проверить auth и принадлежность exam/assignment. Lock exam shared + основной assignment. Проверить expectedHistoryGeneration. Существующий attempt вернуть без проверки текущего банка/времени, включая completed. Для создания проверить prepare readiness (без времени), зафиксировать участников, создать pending. Старт ещё не состоялся, вопроса/definition нет. UNIQUE ограничивает гонку.

### Ready

Lock exam shared + attempt. Actor входит в attempt; status pending. Повторная собственная ready — no-op, чужая готовность не даёт 409. Проверить prepare readiness при первой ready. Никакого expectedStateVersion: независимые участники не перезаписывают друг друга. Изменение assignment после ensure запрещено; админ очищает историю, затем меняет назначение.

### Start

Lock exam exclusive, основной assignment, attempt. Replay receipt проверить до версий. Если attempt уже in_progress/completed — вернуть outcome `already_started`, не выдавать второй вопрос. Иначе проверить expectedStateVersion, готовность всех, readiness именно этого assignment, UTC окно.

В одной transaction:

1. получить/create immutable definition по current configVersion, скопировав весь банк;
2. связать attempt и definition, зафиксировать лимит замен/время;
3. создать progress и первый presented question/round через FEX-09/10;
4. выставить status/phase, увеличить stateVersion ровно один раз;
5. сохранить command receipt, commit.

Если любой шаг неуспешен, откатить всё, включая ready/start статус и созданный незавершённый definition. Активная сдача больше не читает mutable банк и scoring. Ограничения размера банка заданы FEX-04; копирование не держит transaction во время сетевой передачи.

## 5. REST contracts

Auth всех routes — examinator + membership. Все POST требуют Idempotency-Key.

| Метод/путь | Body | Успех |
|---|---|---|
| POST /api/examiner/exams/{examId}/assignments/{assignmentId}/attempts/ensure | `{expectedHistoryGeneration:1}` | 201 новая, 200 существующая |
| GET /api/examiner/attempts/{attemptId} | — | 200 AttemptState |
| POST /api/examiner/attempts/{attemptId}/ready | `{}` | 200 command response |
| POST /api/examiner/attempts/{attemptId}/start | `{expectedStateVersion:7}` | 200 command response |

Command response:
```json
{
  "success":true,
  "data":{
    "receipt":{"commandId":801,"outcome":"applied","appliedStateVersion":8,"presentedQuestionId":501},
    "replayed":false,
    "attempt":{
      "id":301,"examId":41,"examType":"classic","directionId":3,"directionName":"Математика",
      "assignmentId":201,"attemptNo":1,"historyGeneration":1,
      "student":{"id":125,"fullName":"Петров Пётр"},
      "status":"in_progress","phase":"regular_questions","stateVersion":8,
      "members":[{"id":12,"fullName":"Иванов И.И.","position":1,"ready":true,"readyAt":"2026-09-12T10:00:00+03:00"}],
      "allMembersReady":true,
      "startedAt":"2026-09-12T10:01:00+03:00","completedAt":null,
      "serverNow":"2026-09-12T10:01:00+03:00",
      "currentQuestion":{
        "id":501,"sequenceNo":1,"purpose":"regular","partCode":"A",
        "questionText":"Вопрос","answerText":"Эталон","weight":5,"cycleNo":1,
        "status":"open","replacesPresentedQuestionId":null,"canReplace":true,
        "round":{"id":601,"roundNo":1,"status":"open","votesReceived":0,"votesRequired":1,"hasVoted":false,"myVote":null},
        "lastCompletedRound":null,"completedRoundsCount":0,"consensus":null,"canGoNext":false
      },
      "progress":{"regularConsensus":0,"regularRequired":1,"replacementUsed":0,"replacementLimit":1,"parts":[{"sourcePartId":7,"code":"A","consensus":0,"required":1}]},
      "result":null,
      "permissions":{"canReady":false,"canStart":false,"canVote":true,"canReplace":true,"canGoNext":false}
    }
  }
}
```
currentQuestion/progress заполняют FEX-09/10; null допустим только pending/completed согласно DTO. GET возвращает `data.attempt` без receipt. Ensure добавляет `data.created`.

Ошибки: 400 missing/invalid key/field; 404 чужой объект; 409 history_generation_changed/stale_state/idempotency_key_reused/attempt_not_pending; 422 exam_not_ready/exam_outside_window/commission_not_ready. `details.currentVersion` только после membership.

## 6. Replay и удаление

Key lookup всегда учитывает actor. Нельзя вернуть одному члену комиссии сериализованный response другого (включая myVote). Receipt воспроизводит эффект; attempt сериализуется заново для текущего actor. Completed receipt не содержит текстов/голосов.

После физического удаления attempt старые ready/start/vote/next получают 404. Старый ensure с generation1 при текущем2 — 409 без создания сдачи. Новый открытый список отдаёт generation2; осознанное действие пользователя получает новый key.

## 7. Frontend-задачи

1. Typed AttemptState/CommandReceipt/error DTO и API.
2. `useExamAttempt`: explicit refresh, no-store, request cancellation; не применять меньшую stateVersion.
3. Ready без глобальной версии; показывать allReady по серверу.
4. Start с версией; restore после reload/401 с новой авторизацией.
5. Pending/uncertain request: сохранить key и payload до выяснения результата; не отправлять старое действие после переключения студента.
6. Отдельный receipt banner при auto переходе, чтобы последний голосующий видел результат предыдущего вопроса.

## 8. Backend-задачи

1. Migration/repositories immutable definitions/attempts/members/commands.
2. Ensure/ready/start с единым lock order.
3. Read serializers и permissions.
4. Idempotency runner и hash canonicalization.
5. Readiness, selection, vote hooks через один connection.
6. API/CORS/privacy/concurrency tests.

## 9. Критерии приёмки и тесты

- Шесть одновременных ready успешно сохраняются; повтор не меняет дату/версию.
- Два start дают ровно один первый вопрос/round/definition.
- Изменение/удаление банка после start не влияет на текущую сдачу.
- Ensure existing возвращает active/completed даже при текущем incomplete config.
- Один key у двух разных actor не пересекается; replay не раскрывает myVote другого.
- Deleted history не воскресает от повторного ensure.
- Старый GET, завершившийся позже mutation, не откатывает UI.
- Два разных студента одного экзамена голосуют без глобальной сериализации всех команд.
- Сохранение снапшота и первой выдачи атомарно, UTC границы проверены.
