# Общие инженерные соглашения экзаменационного домена

Редакция 2026-09-12. Это обязательный контракт для всех FEX. Функциональные правила — в [бизнес-ТЗ](../00-business-requirements.md); проверенные точки интеграции — в [аудите](../02-review-and-decisions.md).

## 1. Границы и текущий проект

Backend: `cpm_back/services/exams/` — новый домен; blueprints `exams_v2_bp.py`, `examiner_exams_bp.py`, `student_exams_bp.py`. Существующий `services/exam/` содержит и тесты, и legacy-экзамены/рейтинг; не считать весь каталог модулем online-тестов.

Frontend: `lib/exams-v2/`, `components/admin/exams-v2/`, `components/examinator/exams/`, `components/student/exams-v2/`. Встраивание в существующий `components/sections/section-content.tsx`.

Пути ниже — фактические Flask routes от `API_BASE_URL`, включая ровно один `/api`:

- admin: `/api/exams`;
- examiner catalogs: `/api/examiner/exams`; attempts: `/api/examiner/attempts`;
- student: `/api/student/exams`;
- legacy routes остаются без добавления prefix; adapters фильтруют outside LMS.

## 2. API transport

Успех: `{"success":true,"data":{}}`. DELETE — 204 без тела, кроме команд с явно заданным receipt.
Ошибка:
```json
{"success":false,"error":"stale_state","message":"Обновите состояние сдачи","details":{"currentVersion":12},"correlationId":"uuid"}
```
400 — неверный формат, поля/enum/тип; 401 — не авторизован; 403 — не та роль; 404 — отсутствующий/чужой объект; 409 — версия/конкуренция/идемпотентность; 413 — лимит файла/тела; 422 — нарушено бизнес-условие; 500 — внутренняя ошибка без SQL.

В `lib/api/client.ts` расширить `ApiError` полями `code, details, correlationId`, сохранив существующий конструктор/consumers. Одинаково разобрать ошибки JSON и multipart. Для FormData использовать `apiFormRequest`: текущий `apiRequest` добавляет JSON Content-Type.

В `create_app()` разрешить CORS request headers `Idempotency-Key, X-Exam-Confirmation, X-Correlation-ID` вместе с существующими. Expose `X-Correlation-ID`. Проверить реальный OPTIONS запрос с origin frontend. JWT authentication errors нового namespace тоже приводить к общей форме без изменения legacy contract.

## 3. Поля и точность

- Каноническое имя типа — `examType`, направления — `directionId,directionName`, имени экзаменатора outside — `examinator`.
- JSON IDs — положительные safe integers <= 9007199254740991. PK новых таблиц BIGINT; FK к legacy имеет ТОЧНО тот же размер и signedness, что целевой PK. JWT `id` — ID строки ролевой таблицы; actor identity — пара `(role,id)`.
- Username берётся из `auth_users.username` через `role/ref_id`; не искать выдуманное `students.login`.
- Даты ISO aware; БД UTC `DATETIME(6)`, UI Moscow. При получении connection нового домена установить SQL session time_zone='+00:00', чтобы DB defaults CURRENT_TIMESTAMP тоже были UTC; интеграционный тест с non-UTC сервером обязателен. Date-only — YYYY-MM-DD без timezone conversion.
- Grade — integer 0..5; vote — number 0/0.5/1; boolean вместо любого integer/ID/version отвергать.
- Classic score — DECIMAL с шагом 0.5; outside points — DECIMAL(12,2), API number 0..9999999999.99, максимум 2 десятичных знака. Это предел хранения, не экзаменационный максимум; лишнюю точность не округлять молча.
- Вопрос/ответ TEXT: UTF-8 <= 61440 bytes каждый, непустой после trim. Литералы NaN/Infinity, HTML как разметка и unknown fields запрещены.
- Необязательное обновление — отсутствующее поле; null разрешён только для явно nullable поля.

## 4. Пагинация и чтение

Lists: `data.items` и `data.pagination={page,limit,total,totalPages,hasNext,hasPrev}`; page>=1, limit default20/max100, totalPages минимум1. Каждая сортировка завершается уникальным ID для стабильности. Search <=200 символов, parameterized SQL, %/_ экранировать.

Протоколы не вкладывать целиком в AttemptState. Questions: page limit default10/max20; rounds: default20/max100; appeals: default20/max100. Каждый question содержит не больше одного текущего/последнего раунда. История доступна полностью последовательными страницами без ограничения числа вопросов/раундов.

Все ответы экзаменационного namespace: `Cache-Control: private, no-store`. Серверные role serializers работают по allowlist; запрещено кешировать персональный response для другого actor.

## 5. Версии и транзакции

Числовой `version` каждой изменяемой административной записи; payload `expectedVersion` обязателен при update/delete. Timestamp — отображение, не lock token. Создание version=1; действительное изменение +1, no-op без увеличения.

`classic_exam_settings.config_version` — единая версия всего определения: settings, parts, questions, thresholds. Любое изменение этого набора (включая import/delete) увеличивает её в той же транзакции. Scoring PUT проверяет `expectedConfigVersion`; отдельной scoringVersion нет.

Порядок блокировок:

1. Exam row: shared lock для команд уже начатых attempts, exclusive для definition CRUD/start/delete/назначений. Использовать SQL синтаксис поддерживаемой установленной версии MySQL.
2. Assignment/history generation (при ensure/delete/retake), затем attempt; при нескольких — по возрастанию ID.
3. Question/round/member/command rows.
4. Rating revision singleton — последним, если результат влияет на рейтинг.
Не брать exam exclusive после attempt lock. Разные attempts одного экзамена могут одновременно держать shared exam lock; whole-exam DELETE ждёт их завершения.

Один connection и transaction на mutation; сервисы внутри не коммитят. Excel разбирается до transaction. Rollback обязателен. Start видит одну атомарную версию определения.

## 6. Идемпотентность

`Idempotency-Key` UUID на каждую новую пользовательскую команду; retry с тем же payload/key.

- Ready — собственная строка участника; global version не проверяется.
- Vote — текущие presentedQuestionId/roundId и отсутствие собственного голоса; чужой vote не инвалидирует запрос.
- Start/next/replace — expectedStateVersion; next/replace обязательно presentedQuestionId.
- Ensure — expectedHistoryGeneration; никогда не воскрешает удалённую историю.
- Admin create, appeal, retake, import commit, destructive delete — также idempotent.

Ключ scoped к actor role+ID и command target. Hash включает canonical payload/route. Тот же key с другим запросом →409. Сначала auth/ownership, затем lookup receipt; успешный replay не исполняет mutation заново. Receipt хранит outcome IDs/применённую версию, но не полный протокол/чужие ответы. Ответ содержит неизменный receipt и заново сериализованное текущее состояние; `replayed=true`. Ошибки валидации не резервируют key.

Административные receipts (FEX-01) хранятся 72h; одноразовые destructive receipts — только hash ключа, actor и отсутствие данных объекта после удаления. Attempt receipts удаляются с историей; assignment.history_generation +1 блокирует поздние ensure. Старые attempt IDs никогда не переиспользуются.

## 7. Физическое удаление

Preview возвращает `confirmationToken,expiresAt,counts`, токен TTL10 минут, подписан и scoped actor/target/version. Клиент отправляет его в `X-Exam-Confirmation`, не URL. DELETE с тем же idempotency key возвращает receipt; при изменении состава затрагиваемых данных нужен новый preview (409 `delete_preview_changed`). Одна модалка preview с явным подтверждением, без дополнительного цепочного подтверждения.

Каскады — только по владению. Пользователи/направления не удаляются вслед за экзаменом. Ссылки на source bank не удаляют snapshots. Порядок очистки FEX-11 включает immutable definitions, import previews, command receipts; технические replay tombstones не содержат протокол и не позволяют его восстановить.

## 8. Browser state

Manual refresh, own mutation, navigation/reload — единственные поводы загрузить attempt; focus/polling/WS не добавлять. На фоне могут работать существующие rating jobs — запрет polling касается экзаменационных экранов.

Отклонять response меньшей stateVersion; отменять/игнорировать старый fetch после смены attempt/account. Snapshot query одной страницы должен быть согласованным. Перед необратимым vote показывать student/question/round. При timeout — проверить состояние или повторить тот же key, не переносить голос в новый раунд.

## 9. Миграции и диагностика

Схема legacy полностью отсутствует в migrations. Before DDL: SHOW CREATE TABLE, MySQL version/sql_mode/charset/indexes; сохранить без данных/секретов. Не выдавать шаблон BIGINT за готовый DDL для INT PK.

Реестра применённых SQL-миграций в репозитории не обнаружено: FEX-16 создаёт `schema_migrations(version,checksum,applied_at)` и deploy runner с pre/postconditions. Исторические миграции не запускать вслепую заново.

Логи: correlation/actor/exam/attempt/command/outcome/duration; без текстов, персональных vote values, preview rows, токенов. Unit тесты правил, MySQL integration транзакций, API contracts и E2E ролей обязательны для соответствующих FEX.

## 10. Обязательные поля общих объектов

Все админские Exam/Part/Question/Commission/Assignment/Privilege/OutsideResult DTO содержат числовую version даже если пример в feature сокращает поля; PATCH/PUT/DELETE требует expectedVersion. Config/Scoring используют только configVersion/expectedConfigVersion. При создании (кроме config/scoring) version=1. Все scalar new table id — AUTO_INCREMENT; timestamps NOT NULL с UTC default/явной записью. Auth role и exam ownership проверяются на каждом type-specific route, включая import и lookup. Синхронные изменяющие admin запросы, включая CRUD создания частей/вопросов, используют Idempotency-Key с receipts FEX-01.

## 11. Межтабличные инварианты и DB defaults

DB FKs не заменяют ownership: question.part.exam == question.exam, settings.tieBreakerPart.exam == settings.exam, assignment/attempt/definition.exam одинаковы; presented.definitionQuestion.definition == attempt.definition; vote.examinator входит в attempt membership. Проверять под тем же lock в repositories, покрыть integrity query и negative tests. Обратные scalar links validated, не входят в каскад.

Для deletes row expectedVersion передаётся query parameter; history/whole exam используют fingerprint confirmation вместо отдельного row token. Common errors дополняют410 import_session_expired/413 payload_too_large/503 exam_temporarily_unavailable. Receipt lookup успешного delete разрешён после JWT проверки по actor-scoped tombstone до existence lookup; это единственное исключение отсутствующего target и не раскрывает данные.
