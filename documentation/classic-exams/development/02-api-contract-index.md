# Индекс API-контрактов

Редакция2026-09-12. Paths — от backend origin, включая /api ровно один раз. Имена параметров здесь camelCase; Flask использует int converters. Значения/DTO определены владельцами и общими соглашениями, это индекс для совместной работы frontend/backend.

## 1. Общие формы

- S: `{success:true,data:{...}}`; списки `data.items,pagination`.
- E: `{success:false,error,message,details,correlationId}`.
- Любой новый route проверяет роль и принадлежность дочерних IDs указанному exam/attempt.
- Admin create/update/appeal/retake/import mutations дополняют domain data полями receipt/replayed, как FEX-08; key lookup до CAS. DELETE204 повторяет204 без тела.
- HTTP201 create,200 read/update/replay,204 delete; первый/replay commit200.
- Version аргументы никогда не передаются как timestamp: expectedVersion, expectedConfigVersion, expectedPreviewVersion, expectedResultVersion или expectedHistoryGeneration по таблице.

## 2. Admin — ядро/outside

| Метод | Path | Body/query | Ответ | FEX |
|---|---|---|---|---|
| GET | /api/exams | page,limit,type,directionId,search,sort,dateFrom,dateTo | ExamSummary[] |01|
| POST | /api/exams | examType,directionId,date? | data.exam |01|
| GET | /api/exams/{examId} | — | data.exam |01|
| PATCH | /api/exams/{examId}/direction | directionId,expectedVersion | data.exam |01|
| GET | /api/exams/{examId}/overview | — | exam + type-specific counts |12|
| GET | /api/exams/{examId}/delete-preview | — | counts/token/expiresAt |11|
| DELETE | /api/exams/{examId} | X-Exam-Confirmation |204|11|
| GET | /api/exams/lookups/students | page,limit,search | id/fullName/login[] |01|
| GET | /api/exams/lookups/examinators | page,limit,search | id/fullName/login[] |01|
| PATCH | /api/exams/{examId}/outside-lms | date,expectedVersion | data.exam |02|
| GET | /api/exams/{examId}/outside-lms/results | page,limit,search,sort | OutsideExamResult[] |02|
| POST | /api/exams/{examId}/outside-lms/results | studentId,points,grade,examinator | data.result |02|
| PATCH | /api/exams/{examId}/outside-lms/results/{resultId} | points/grade/examinator,expectedVersion | data.result |02|
| DELETE | /api/exams/{examId}/outside-lms/results/{resultId} | query expectedVersion |204|02|

## 3. Admin — настройка/банк

| Метод | Path | Body/query | Ответ | FEX |
|---|---|---|---|---|
| GET/PATCH | /api/exams/{examId}/classic/config | PATCH partial + expectedConfigVersion | config/localValidation |03|
| GET/PUT | /api/exams/{examId}/classic/scoring | PUT profile + expectedConfigVersion | scoring/validation |05|
| GET/POST | /api/exams/{examId}/classic/parts | POST code,weight?,quota? (DTO questionWeight/questionCount) | list / data.part |04|
| PATCH | /api/exams/{examId}/classic/parts/{partId} | partial+expectedVersion | data.part |04|
| GET | /api/exams/{examId}/classic/parts/{partId}/delete-preview | — | counts/token/versions |04|
| DELETE | /api/exams/{examId}/classic/parts/{partId} | expectedVersion query + X-Exam-Confirmation |204|04|
| GET/POST | /api/exams/{examId}/classic/questions | GET filters; POST partId,questionText,answerText | list / data.question |04|
| PATCH | /api/exams/{examId}/classic/questions/{questionId} | partial+expectedVersion | data.question |04|
| DELETE | /api/exams/{examId}/classic/questions/{questionId} | expectedVersion query |204|04|
| GET | /api/exams/{examId}/classic/readiness | — | data.readiness |07|
| GET | /api/exams/{examId}/classic/assignments/{assignmentId}/readiness | — | readiness/permissions |07|

Последняя readiness route доступна также examinator из assignment. Остальные маршруты таблицы admin-only.

## 4. Admin — комиссии/назначения/результаты

| Метод | Path | Body/query | Ответ | FEX |
|---|---|---|---|---|
| GET/POST | /api/exams/{examId}/classic/commissions | POST name?,examinatorIds | list / data.commission |06|
| PUT/DELETE | /api/exams/{examId}/classic/commissions/{id} | PUT full+expectedVersion; DELETE version query | commission/204 |06|
| GET/POST | /api/exams/{examId}/classic/assignments | GET filters; POST studentId,commissionId,replacementLimit?,expectedPrivilegeVersion? | list / data.assignment |06|
| PUT/DELETE | /api/exams/{examId}/classic/assignments/{id} | immutable student/no; CAS + privilege CAS | assignment/204 |06|
| GET | /api/exams/{examId}/classic/privileges | page,limit,search | list |06|
| GET/PUT/DELETE | /api/exams/{examId}/classic/students/{studentId}/privilege | PUT replacementLimit,expectedVersion; DELETE version query | privilege/204 |06|
| GET | /api/exams/{examId}/classic/attempts | page,limit,status,search,commissionId,attemptNo | attempts[] |11|
| GET | /api/exams/{examId}/classic/results | page,limit,search,grade,hasAppeal | current results[] |11|
| GET | /api/exams/{examId}/classic/attempts/{attemptId} | — | summary/counts |11|
| GET | /api/exams/{examId}/classic/attempts/{attemptId}/questions | page,limit,throughSequenceNo? | protocol questions[] |11|
| GET | /api/exams/{examId}/classic/attempts/{attemptId}/questions/{presentedId}/rounds | page,limit,throughRoundNo? | rounds/votes[] |11|
| GET/POST | /api/exams/{examId}/classic/attempts/{attemptId}/appeals | POST grade,expectedResultVersion | appeal list / appeal+resultVersion |11|
| POST | /api/exams/{examId}/classic/students/{studentId}/retake | commissionId,expectedHistoryGeneration | assignment2 |11|
| GET | /api/exams/{examId}/classic/students/{studentId}/delete-preview | — | counts/token/generation |11|
| DELETE | /api/exams/{examId}/classic/students/{studentId}/history | X-Exam-Confirmation |204|11|

## 5. Examiner

| Метод | Path | Body/query | Ответ | FEX |
|---|---|---|---|---|
| GET | /api/examiner/exams | page,limit,state,search | assigned exams[] |13|
| GET | /api/examiner/exams/{examId}/students | page,limit,status,search | assignment rows[] |13|
| POST | /api/examiner/exams/{examId}/assignments/{assignmentId}/attempts/ensure | expectedHistoryGeneration | created,attempt,receipt |08|
| GET | /api/examiner/attempts/{attemptId} | — | data.attempt |08|
| POST | /api/examiner/attempts/{attemptId}/ready | {} | receipt+attempt |08|
| POST | /api/examiner/attempts/{attemptId}/start | expectedStateVersion | receipt+attempt |08|
| POST | /api/examiner/attempts/{attemptId}/vote | presentedQuestionId,roundId,value | receipt+attempt |10|
| POST | /api/examiner/attempts/{attemptId}/next-question | presentedQuestionId,expectedStateVersion | receipt+attempt |09|
| POST | /api/examiner/attempts/{attemptId}/replace-question | presentedQuestionId,expectedStateVersion | receipt+attempt |09|
| GET | /api/examiner/attempts/{attemptId}/questions | page,limit,throughSequenceNo? | questions[] |11|
| GET | /api/examiner/attempts/{attemptId}/questions/{presentedId}/rounds | page,limit,throughRoundNo? | role-filtered rounds[] |11|

## 6. Student / imports / capabilities

| Метод | Path | Ответ | FEX |
|---|---|---|---|
| GET | /api/student/exams/results | type-aware current result list |14|
| GET | /api/student/exams/{examId}/result | current/history summaries |14|
| GET | /api/student/exams/{examId}/attempts/{attemptId}/questions | paginated consensus-only protocol |14|
| GET | /api/exams/capabilities | actor-scoped capabilities |16|

Импорты: три prefix × parse/session GET/session PUT/commit — полностью в [общем контракте](./01-contracts-and-imports.md). Parse/PUT/commit тоже требуют Idempotency-Key; parse hash включает SHA256 файла и exam ID, response replay возвращает тот же session ID.

Рейтинг использует существующие routes/roles/формы ответов из FEX-15, с freshness scope=exams. Никаких новых export endpoints.

## 7. Обязательная транспортная приёмка

По каждой строке backend/FE согласуют конкретный сериализуемый DTO и contract test; enum/range/defaults/unknown fields — по владельцу. В реализации этот индекс переносится в OpenAPI schemas и typed clients. Не заменять проверку типа TypeScript cast-ом, если сервер возвращает другой enum/field. User-facing текст ошибки не использовать как условие UI: только stable error code.
