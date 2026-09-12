# FEX-11. Результат, пересдача, апелляция и удаление

## 1. Результат

Последний требуемый консенсус автоматически публикует результат. Администратор назначает одну пересдачу, регистрирует апелляции, очищает всю историю студента или удаляет экзамен. Backend/DB/REST обязательны по [соглашениям](./00-engineering-conventions.md).

## 2. Миграция `023_classic_exam_results.sql`

Расширить attempts:
```text
raw_total DECIMAL(12,1) NULL
rounded_total INT UNSIGNED NULL
max_score INT UNSIGNED NULL
calculated_grade TINYINT UNSIGNED NULL
resolution_type VARCHAR(32) NULL
result_version BIGINT UNSIGNED NOT NULL DEFAULT 1
published_at DATETIME(6) NULL
```

`classic_exam_appeals`:
```text
id BIGINT AUTO_INCREMENT PK
attempt_id FK attempts ON DELETE CASCADE
previous_grade TINYINT UNSIGNED NOT NULL
new_grade TINYINT UNSIGNED NOT NULL
changed_by_admin_id FK admins ON DELETE RESTRICT
admin_name_snapshot VARCHAR(255) NOT NULL
created_at DATETIME(6) NOT NULL
INDEX(attempt_id,id)
```
Grade 0..5 валидируется всегда. Effective grade не хранить второй mutable колонкой: latest appeal по ID, иначе calculated_grade.

## 3. Завершение

Vote последнего regular consensus вызывает scoring в той же transaction:

1. посчитать rawTotal только по regular consensus occurrences (replaced/extra не входят);
2. integer → grade по snapshot thresholds;
3. .5 + configured up/down → округлить;
4. .5 + extra → зафиксировать raw/max, phase=tie_breaker, выдать первый extra;
5. если итог определён, установить completed/status/phase/completedAt/publishedAt, totals/calculatedGrade, current_presented_question_id=NULL, инвалидировать рейтинг.

Extra consensus: 1 вверх, 0 вниз, .5 по настройке repeat/up/down. Repeat выдаёт новый extra автоматически; вес extra информационный, awarded score=0. Отдельного финального next/подтверждения admin нет. Receipt последнего vote содержит `resolvedQuestionId,consensus,outcome=next_extra|completed`. Одна vote-команда увеличивает stateVersion только один раз.

Resolution: integer/configured_up/configured_down/tie_breaker_up/tie_breaker_down. Решение воспроизводится из immutable definition + regular scores + extra consensus.

## 4. Effective result

1. Если completed attempt2 существует — current она, даже если хуже.
2. Иначе completed attempt1.
3. Для выбранной attempt применить её latest appeal.
4. Незавершённая attempt2 не скрывает первую.
5. История первой попытки включает её собственный effective grade/marker; апелляция первой после завершённой второй не меняет рейтинг.
6. Нет completed — результата нет; рейтинг подставляет 0 отдельным resolver-ом.

Один service `get_effective_result(exam_id,student_id)` используется student/admin/rating. Для списков batch эквивалент без N+1.

## 5. Пересдача

`POST /api/exams/{examId}/classic/students/{studentId}/retake`
Auth admin, Idempotency-Key:
```json
{"commissionId":15,"expectedHistoryGeneration":1}
```

Lock exam exclusive + основной assignment; first completed, assignment2/attempt2 отсутствует. Комиссия принадлежит exam, 1..6 аккаунтов. Множество IDs отличается от первой хотя бы одним участником; допускаются общие участники. Изменение только имени/template ID при том же составе →422 `retake_commission_unchanged`.

Создать assignment2 с новыми member snapshots; attempt появится при ensure. Лимит FEX-06 общий на exam/student, снова предоставляется полностью при start2. Start2 использует актуальное определение и период. 201 `data.assignment` + receipt; replay200; ошибки first_attempt_not_completed/retake_already_assigned/history_generation_changed/commission_not_found.

Назначение2 можно изменить/удалить до ensure, это ещё не сданная пересдача и не новый результат. После ensure применяется только очистка всей истории, не частичное удаление попытки.

## 6. Апелляция

`POST /api/exams/{examId}/classic/attempts/{attemptId}/appeals`
Auth admin, Idempotency-Key:
```json
{"grade":5,"expectedResultVersion":1}
```

Lock exam shared + attempt. Только completed; CAS resultVersion; previousGrade берётся внутри lock. При той же grade факт апелляции сохраняется. Grade 0..5; reason/date/comments не принимаются. Insert appeal + resultVersion+1 + rating revision, если это current, в одной transaction.

201:
```json
{
  "success":true,
  "data":{
    "appeal":{"id":7,"previousGrade":4,"newGrade":5,"changedBy":{"id":1,"fullName":"Администратор"},"createdAt":"2026-09-12T14:00:00+03:00"},
    "effectiveGrade":5,"hasAppeal":true,"resultVersion":2,"isCurrentAttempt":true
  }
}
```
409 result_modified/idempotency_key_reused. Две вкладки не перезаписывают апелляции молча; replay не создаёт вторую запись.

## 7. Чтение и размер протокола

Admin:

- GET /api/exams/{examId}/classic/attempts?page=1&limit=20&search=&status=&commissionId=&attemptNo=
- GET /api/exams/{examId}/classic/attempts/{attemptId}
- GET /api/exams/{examId}/classic/attempts/{attemptId}/questions?page=1&limit=10
- GET /api/exams/{examId}/classic/attempts/{attemptId}/questions/{presentedId}/rounds?page=1&limit=20
- GET /api/exams/{examId}/classic/attempts/{attemptId}/appeals?page=1&limit=20

Examiner (только attempt membership):

- GET /api/examiner/attempts/{attemptId}/questions?page=1&limit=10
- GET /api/examiner/attempts/{attemptId}/questions/{presentedId}/rounds?page=1&limit=20

Detail — definition rules/parts без невыпавшего банка, members, progress, totals/effective grade, resultVersion, counts и page links. Questions — items с immutable TEXT, ID, sequenceNo, purpose, partCode, weight, cycleNo, status, consensus, awardedPoints, replacesPresentedQuestionId, completedRoundsCount. Rounds — полные votes, максимум6; examiner только закрытые и собственный голос текущего, admin все. Sorting ascending sequenceNo/roundNo/id.

При чтении активного протокола сервер фиксирует верхнюю sequenceNo/roundNo на первую страницу и возвращает `throughSequenceNo`/`throughRoundNo`; дальнейшие страницы используют этот query anchor. Refresh начинает новый просмотр. Студенту routes/serializers FEX-14.

Неограниченная история никогда не добавляется целиком в каждый command response.

## 8. Физическое удаление

### История студента

GET /api/exams/{examId}/classic/students/{studentId}/delete-preview
DELETE /api/exams/{examId}/classic/students/{studentId}/history

DELETE: Idempotency-Key + X-Exam-Confirmation. Preview включает exam/student/current result, counts обеих attempts и assignment2, historyGeneration, versions, expiresAt. Под exam exclusive + основным assignment + attempts по ID проверить fingerprint/token.

Порядок:

1. удалить обе attempts и их presented/rounds/votes/usage/commands/members/appeals/results через каскады владения;
2. удалить assignment2/members;
3. оставить assignment1 и privilege; увеличить history_generation и assignment.version;
4. удалить только более не используемые definition versions этого экзамена (в них нет персональных данных);
5. очистить административные receipts, содержащие удаляемую историю; сохранить только tombstone hash/key без payload;
6. rating revision+1, компактный deletion receipt, commit.

204; replay204. Пустая история: 204/no-op без изменения generation, если не было assignment2/attempts. Старые попытки404, старый ensure409. Студент исчезает из result list; рейтинг включает экзамен с нулём.

### Весь экзамен

GET /api/exams/{examId}/delete-preview
DELETE /api/exams/{examId}

Тот же token/key flow. Exam exclusive запрещает новые starts/commands во время короткой transaction. Удалить attempts первым (освободить RESTRICT definition links), затем definitions; root exam cascade удаляет assignments/commissions/privileges/import sessions/settings/parts/questions/outside results. Служебные receipts очистить от payload, оставить только replay tombstones; ни одного protocol snapshot.

## 9. Backend-задачи

1. Result/appeal migration/repositories.
2. Finalization hook inside vote transaction.
3. Shared/batch effective resolver.
4. Retake/appeal CAS/idempotency.
5. Paginated role protocols.
6. Delete fingerprint/receipt/order/history generation.
7. Transactional rating invalidation FEX-15.

## 10. Frontend-задачи

1. Admin current/history views с row-level resultVersion.
2. Retake dialog с новым составом; незавершённая retake отдельно от current result.
3. Appeal dialog: только grade; версия/key внутренние.
4. One confirmation preview для полного удаления с перечислением сохранённых assignment1/лимита.
5. Protocol pagination/anchors, lazy rounds.
6. Examiner auto result/extra transition и баннер последнего consensus.

## 11. Definition of Done и тесты

- Last regular vote автоматически публикует результат/extra, без финальной кнопки.
- Same/worse retake становится current; незавершённая сохраняет первую.
- Ровно один assignment2 при параллельном назначении.
- Same-grade appeal сохраняет факт; replay нет; CAS race=409.
- Апелляция исторической попытки не меняет current grade.
- 1000 конфликтных раундов/extra читаются страницами, command response ограничен.
- Delete vs vote/start/appeal: либо действие предшествует новому preview, либо delete завершается и позднее действие получает404/409; частично удалённого протокола нет.
- Replayed ensure после очистки не создаёт попытку.
- Delete rollback сохраняет весь домен и рейтинг; successful delete сохраняет только описанные reusable назначения/лимиты.

## 12. Список текущих результатов admin

GET /api/exams/{examId}/classic/results?page=1&limit=20&search=&grade=&hasAppeal=
Auth admin. Строка на студента с completed: `{student:{id,fullName},currentAttemptId,currentAttemptNo,rawTotal,roundedTotal,maxScore,calculatedGrade,effectiveGrade,hasAppeal,resultVersion,firstAttemptId,retakeAssignmentId,retakeStatus}`. Grade/hasAppeal filter до pagination; поиск ID/ФИО; sort studentName,studentId. RetakeStatus не меняет current пока не completed. Студенты без результата остаются в assignments/attempts.
