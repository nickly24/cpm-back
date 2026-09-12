# FEX-16. Совместимость, миграции и выпуск

## 1. Результат и реальные ограничения

Классические экзамены внедряются в существующие Flask/MySQL и Next.js кабинеты. Это задача реализации/проверки выпуска, не утверждение, что migration или нагрузочный тест уже выполнены.

Обновление реализации 12.09.2026: фактический DDL получен, registry и runner реализованы, backup восстановлен на локальной копии. Production-операции отдельно разрешены владельцем. Состояние выполнения, отличия от эскизов и команды выпуска — [в журнале реализации](../../../docs/classic-exams-implementation/README.md). Нумерация и типы в таблице ниже актуализированы; старые номера миграций в остальных задачах — проектные обозначения, а не файлы для запуска.

## 2. Порядок миграций

| Файл | Владелец | Изменение |
|---|---|---|
| 015_exam_core.sql | FEX-01 | type/direction/version, nullable date/name, role-scoped admin receipts |
| 016_outside_lms_results.sql | FEX-02 | версии результатов, import sessions; без изменения legacy numeric types |
| 017_classic_exam_settings.sql | FEX-03 | settings/configVersion |
| 018_classic_exam_question_bank.sql | FEX-04 | parts/questions/imports |
| 019_classic_exam_scoring.sql | FEX-05 | thresholds |
| 020_classic_exam_assignments.sql | FEX-06 | commissions/assignments/privileges/imports/generation |
| 021_classic_exam_attempts.sql | FEX-08 | immutable definitions/questions, attempts/members/receipts |
| 022_classic_exam_presented_questions.sql | FEX-09 | progress/presented/usage |
| 023_classic_exam_voting.sql | FEX-10 | rounds/votes/consensus |
| 024_classic_exam_results.sql | FEX-11 | totals/resultVersion/appeals |
| 025_rating_exam_invalidation.sql | FEX-15 | revision/staging/details_json/job lease |
| 026_exam_legacy_hardening.sql | FEX-16 | reviewed direction backfill, NOT NULL, legacy CHECK/FK/UNIQUE, numeric types |
| 027_outside_exam_text_unicode.sql | FEX-16 | Unicode имени экзаменатора |
| 028_exam_retention_indexes.sql | FEX-16 | additive индексы bounded TTL cleanup |

До новых migrations deploy runner создаёт schema_migrations(version VARCHAR(128) PK, checksum CHAR(64), applied_at DATETIME(6)). Старые миграции фиксировать baseline только после проверки фактической схемы, не исполнять повторно. На каждый шаг pre/postconditions/checksum, один migrator lock. MySQL DDL не считать общей rollback transaction: частичный сбой исправлять по postcondition шагам, не слепым повтором ALTER.

## 3. Preflight и подготовка данных

Read-only CLI:

- `python scripts/exams_migration_preflight.py --pilot-student-id 2081`
- `python scripts/exams_migrations.py --phase additive` — read-only check
- `python scripts/exams_migrations.py --phase hardening --direction-map <mapping.json>` — read-only check

CLI не стартуют приложение и не подключаются к Mongo/боту. Без `--connection-json` используется настроенная проектом база; `--plan` полностью offline. `--apply` требует `--backup-verified`, hardening дополнительно `--legacy-compatible-code`; этот флаг означает уже развёрнутый совместимый код, а не только локальные изменения.

Preflight собирает version/sql_mode/charset/collation/PK types/FK/indexes/nullable и counts/checksums, null/invalid grades, val precision/range, orphan sessions, duplicate exam+student, Allratings student duplicates, unmatched/ambiguous direction names. Только агрегаты/ID, без паролей/credentials.

Map:
```json
{"examDirectionMap":{"1":3,"4":3}}
```
Exact trim+casefold match — предложение, не запись без review. Unknown/ambiguous требуют явного mapping; направления не создаются автоматически. Никакого unique direction_id: допускаются разные exam events.

Invalid grade6 не превращается в5, дробные points не обрезаются. Сохранить исходные значения/backup, исправления входят отдельным reviewed mapping. Constraint только после очистки. Счётчики до/после объясняются по каждому явно объединённому дублю, не требовать одновременно сохранения дублей и UNIQUE.

Типы всех FK точно совпадают с legacy PK; новые BIGINT не навязывают legacy INT. Перед production нужен восстановленный backup на копии, без secrets в документации.

## 4. Legacy compatibility — до первых classic writes

В текущем exams_bp есть чтение/удаление, но нет ручного create/update экзаменов/результатов: новые CRUD создаёт FEX-01/02. Не проектировать adapters для несуществующих routes.

Существующие:

- /get-all-exams (защищён auth): v1 оставляет только outside_lms metadata; classic скрыты.
- /get-exam-session, /get-student-exam-sessions/{id}, /get-all-exam-sessions, /get-exam-sessions/{id}: outside_lms only; прежние поля, актуальное direction.name.
- /exams/{id}/delete-preview и DELETE /exams/{id}: outside_lms only; classic→409 use_classic_exam_api. Делегировать outside удаление общему сервису с rating invalidation; новый UI использует v2 confirmation route.
- Не смешивать legacy SQL по exams.name/date с classic пустыми значениями.

Прямой FK CASCADE без application hook недостаточен: legacy delete должен инвалидировать rating. После nullable изменений ни один старый экран не должен воспринимать classic как незаполненный outside.

## 5. Пользователи, права и клиент

Все new mutation routes требуют Bearer JWT в Authorization; cookie-only fallback не использовать в новом namespace. Роль определяется сервером. Exam actor ID — ref_id role table; username — auth_users.username.

Новый FK RESTRICT на users может затронуть существующий delete_user: сейчас student_credentials удаляются и коммитятся раньше удаления student. В FEX-16 обязательно:

1. до любых побочных удалений проверить references exams/assignments/attempts/admin audit;
2. при наличии вернуть409 user_referenced_by_exam с безопасным summary;
3. исключить частичное удаление credentials; не менять пароль/авторизацию при отказе;
4. в admin users UI показать причину. Не предлагать замену участника активной комиссии.

Копии ФИО сохраняются в assignment/attempt/appeal. Проверять наличие действующего role/account при новых назначениях/start; отдельный флаг active без существующей модели не вводить.

CORS и ApiError changes из соглашений обязательны. Проверить OPTIONS с реальным origin, Idempotency-Key/X-Exam-Confirmation, multipart,401/403/409. Frontend AGENTS требует читать установленную Next.js документацию перед реализацией; версия не выбирается по памяти.

## 6. Feature flags

Runtime backend capabilities:
```text
EXAMS_V2_ENABLED
CLASSIC_EXAM_CREATION_ENABLED
CLASSIC_EXAM_COMMANDS_ENABLED
STUDENT_EXAM_RESULTS_V2_ENABLED
RATING_EXAMS_V2_ENABLED
```
Все default=false до rehearsal. Capabilities endpoint GET /api/exams/capabilities (authenticated) отдаёт только доступные actor функции `{apiVersion:"v2",canManageOutside,canCreateClassic,canConductClassic,canReadStudentResults}`.

Выключение creation не мешает уже начатым attempts. Commands выключаются только при реальной аварии; возвращается503 exam_temporarily_unavailable, прогресс сохранён. Student reads не выключать вслед за creation. До первого classic start рейтинг/студенческий read тоже должны быть переведены, чтобы публикация не пропадала.

## 7. Нагрузка и проверки выпуска

Профиль:50 commissions ×6 members, разные students, минимум6 main questions,30% конфликтных раундов,10% замен,20% extra, ручной refresh. Отдельно проверить 1000 extra/rounds и предельный размер банка16MiB.

Целевые критерии на задокументированном staging окружении:

- обычные read/vote/ready/next p95<=1s, p99<=2s;
- start с новым snapshot p95<=5s; warm definition p95<=1s;
- ноль потерянных/двойных votes, неправильных итогов и 5xx/pool exhaustion;
- response текущей сдачи не растёт с числом прошлых раундов;
- protocol query bounded по page size; DB rows/queries не N+1 по студентам.

Стартовые целевые значения — требования ревизии, не измеренный результат. Если окружение не проходит, оптимизация/ёмкость pool до release; не считать «все комиссии когда-нибудь завершились» достаточным SLA.

Test matrix:

- pure Decimal scoring/grade thresholds;
- property selection cycles/replacement/current-ID;
- six concurrent votes/ready, idempotency actor isolation, optimistic admin edits;
- MySQL FK/cascades/lock order/rollback/start snapshots;
- import session owner/versions/limits;
- appeals/same-grade/retake/history/delete replay;
- rating period/asOf/staging/lease/source-changed;
- roles unauthenticated/admin/examinator/student/supervisor/proctor для каждого route;
- mobile320px/desktop, deep links, no polling, stale response guard;
- regression legacy outside and account deletion precheck.

## 8. Rollout

1. Снять DDL и preflight; подготовить fixtures/rehearsal и backup restore.
2. На копии выполнить additive015–025/028, затем reviewed mappings и hardening026–027; counts/checksums/API contract checks.
3. Deploy backend с flags off и уже type-filtered legacy adapters.
4. Production backup, migrate/backfill/hardening, verify outside read.
5. Включить v2 outside UI/CRUD, safe error/CORS clients.
6. Включить rating source/atomic publish и выполнить первый полный расчёт выбранного admin периода.
7. Pilot classic + examiner + student result одновременно; проверить полный сценарий/рейтинговую инвалидацию.
8. После результатов pilot включить общий доступ; adapters остаются до отдельного удаления.

## 9. Rollback

До classic writes: выключить v2 flags, вернуть совместимый frontend/backend; additive schema оставить. После writes: creation off, reads сохранить; применять исправление вперёд либо последнюю совместимую версию. Не откатывать к backend, который видит classic через legacy SQL, удаляет snapshots или пишет старый rating поверх нового.

RATING_EXAMS_V2_ENABLED нельзя выключить так, чтобы classic исчез из рейтинга незаметно: UI показывает stale/maintenance до восстановления совместимого resolver. Down migration с потерей истории не входит в аварийный rollback.

## 10. Backend / frontend / QA задачи

Backend: preflight/migrator/mappings; adapters; user-delete precheck; flag/capability handling; role/CORS tests; metrics без contents; load runner.
Frontend: capability routing, ApiError/transport, maintenance/stale views, user-delete conflict, mobile/E2E.
QA/operations: rehearsal, backup/restore, sample dataset, latency report, rollout checklist и runbooks:
migration-rehearsal.md, classic-exam-incident.md, rating-recovery.md, rollback.md, read-only data-integrity.sql.
Эти runbooks создаются реализацией после проверки реального окружения, не выдумывают production команды сейчас.

## 11. Definition of Done

Все14–25 migrations проверены на реальной копии схемы; несовместимости DDL закрыты; legacy данные сохранены с reviewed исправлениями; role/privacy/concurrency tests прошли; workload измерен; rollback исполним другим инженером. Документ готов к разработке, но фича готова к production только после фактических доказательств этих проверок.
