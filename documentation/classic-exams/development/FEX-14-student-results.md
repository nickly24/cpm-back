# FEX-14. Результаты экзаменов студента

## 1. Результат и данные

Единый mobile-first список собственных опубликованных результатов. Outside — одна exam_sessions запись. Classic — effective resolver FEX-11. До завершения first attempt карточки нет; unfinished retake не скрывает first. Данные из MySQL, новые таблицы не нужны.

## 2. Контракт списка

GET /api/student/exams/results?page=1&limit=20&type=all&directionId=&grade=&sort=date_desc
Auth student; ID только JWT. Type all/outside_lms/classic, grade0..5, sort date_desc/date_asc/direction_asc + examId. Classic сортируется по текущему Moscow start, outside по date. Фильтр grade применяется к current effective grade ДО пагинации.

200 paginated data.items; discriminated union examType.

Outside:
```json
{
  "examId":18,"examType":"outside_lms","directionId":3,"directionName":"Математика",
  "date":"2026-09-12","points":87.5,"grade":5,"examinator":"Иванов И.И.",
  "hasAppeal":false
}
```
Nullable examiner только legacy. Баллы без знаменателя.

Classic:
```json
{
  "examId":41,"examType":"classic","directionId":3,"directionName":"Математика",
  "startAt":"2026-09-12T10:00:00+03:00","endAt":"2026-09-12T18:00:00+03:00",
  "currentAttempt":{
    "attemptId":302,"attemptNo":2,"rawTotal":8.5,"roundedTotal":9,"maxScore":12,
    "grade":4,"hasAppeal":true,"completedAt":"2026-09-12T15:30:00+03:00",
    "commission":[{"id":12,"fullName":"Иванов И.И."}]
  },
  "hasPreviousAttempt":true
}
```
Summary содержит только completed попытки, без готовности/активного состояния.

## 3. Контракт подробности

GET /api/student/exams/{examId}/result
200 data={examId,examType,directionId,directionName,date?,startAt?,endAt?,current,history}.
Outside current=поля результата, history=[].
Classic current и history — AttemptResultSummary как в списке; history максимум первая completed attempt. Добавить questionCount и snapshot периода сдачи, без всего банка/всех вопросов. Для каждой попытки grade — её latest effective; hasAppeal показывает метку, цепочка апелляций не передаётся.

GET /api/student/exams/{examId}/attempts/{attemptId}/questions?page=1&limit=10
Только completed attempt этого exam и JWT student; чужое/незавершённое404. История выданных вопросов страницами; limit<=20, sequenceNo ascending. Counts в detail согласованы с completed snapshot.

Question item:
```json
{
  "id":501,"sequenceNo":1,"purpose":"regular","partCode":"A","weight":2,
  "questionText":"Текст вопроса","answerText":"Эталон",
  "cycleNo":1,"status":"consensus","consensus":1,"awardedPoints":2,
  "replacesPresentedQuestionId":null
}
```
У replaced status=replaced/consensus=null/awardedPoints=0. У заменившего вопроса replacesPresentedQuestionId заполнен; replacement — связь показов, не третий purpose. У tie_breaker awardedPoints=0. У parts только code/weight, не выдуманное name «Теория».

## 4. Privacy

Отдельный allowlist serializer. Не отдаёт персональные votes/rounds, raw appeal history/calculatedGrade вместо effectiveGrade, admin audit/readiness/commands/definition bank. Snapshot имён комиссии допустим. В историю включать все показанные вопросы, включая заменённые, но не невыпавшие.

Не раскрывать готовность/наличие чужого результата через разные errors. Cache no-store; переключение аккаунта отменяет запросы и очищает предыдущий result state.

## 5. Backend-задачи

1. Paged union/result queries с grade filter перед pagination.
2. Shared/batch effective resolver и commission summary без N+1.
3. Три routes и student-specific serializers.
4. Consistent result summary/history, joins к immutable definition.
5. Auth/contract/privacy tests.

## 6. Frontend-задачи

StudentExamResultsSection → filters/cards → detail → current/history selector → paged consensus protocol.
API в lib/exams-v2/student-results.ts; examType/directionId/directionName/examinator едины с доменом.

- Mobile320px: одна колонка, readable TEXT/newlines, кнопки>=44px.
- Desktop расширяет layout без скрытых функций.
- Query state filters/examId/attemptId/page; back/reload восстанавливают.
- После appeal только текущая grade и «После апелляции»; raw/rounded/max отдельно как фактические баллы.
- После retake назвать текущую/первую сдачу.
- Empty all-results и empty filters различаются; 404 после delete закрывает detail.
- Safe text rendering, no HTML, no bank тренажёр.
- Не делать polling.

## 7. Definition of Done и тесты

- Student видит только свой result, сразу после last consensus.
- Retake incomplete/complete/worse; appeal first/current и same-grade marker.
- Весь протокол доступен постранично, без round/voter data.
- Максимальный TEXT не ломает mobile/desktop.
- Current grade filter до pagination.
- Replaced repeat не стирает прежний counted regular.
- Outside grade0/1 в фильтрах, points без /6.
- Logout/account switch не показывает предыдущего студента.
