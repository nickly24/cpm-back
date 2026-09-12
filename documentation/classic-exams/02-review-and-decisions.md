# Проверка пакета ТЗ — 2026-09-12

## Вердикт

Предыдущая версия была полезной функциональной заготовкой, но не готовым без противоречий контрактом разработки. Найдены ошибки в конкуренции, сохранении истории, рейтинговой формуле, старте/завершении, миграциях и UI-интеграции. Они исправлены в бизнес-ТЗ, функциональных задачах и инженерных постановках.

Текущая редакция пригодна для планирования и начала разработки с FEX-16 preflight и FEX-01. Проверка фактического production DDL, интеграционные/браузерные тесты и измерение нагрузки остаются работой реализации, а не уже выполненным подтверждением готовой системы.

## 1. Проверенные факты проекта

| Факт | Подтверждение | Последствие |
|---|---|---|
| val=баллы, points=оценка; frontend DTO уже нормализован | [get_exams.py](../../cpm_back/services/exam/get_exams.py#L8) | Не менять правильный маппинг, убрать /6 и фильтр2–5 |
| Экзамены усредняются по всем exam IDs | [calculate_ratings.py](../../cpm_back/services/exam/calculate_ratings.py#L107) | Не вводить среднее по направлениям |
| Итог использует HW×0.25 + exams×6 + tests×0.45 | [calculate_ratings.py](../../cpm_back/services/exam/calculate_ratings.py#L248) | Коэффициент6 сохранить: он не шкала «из6» |
| Пересчёт сначала удаляет сохранённые данные | [save_ratings.py](../../cpm_back/services/exam/save_ratings.py#L64) | Staging и атомарная публикация в FEX-15 |
| Job admission check и insert раздельны | [rating_recalc_jobs.py](../../cpm_back/services/exam/rating_recalc_jobs.py#L88) | DB lock/lease для одного active job |
| JWT ID — ID ролевой таблицы, username отдельно | [auth.py](../../cpm_back/auth/auth.py#L38) | Явное role/ref_id разрешение ID/login |
| Legacy exams routes не создают/не редактируют результаты | [exams_bp.py](../../cpm_back/blueprints/exams_bp.py) | Новые CRUD, не adapters к несуществующим create routes |
| Public legacy list выбирает все exams | [exams_bp.py](../../cpm_back/blueprints/exams_bp.py#L28) | Type guard до первых classic writes |
| CORS не разрешает Idempotency-Key | [create_app](../../cpm_back/__init__.py#L95) | Дополнить разрешённые заголовки |
| ApiError теряет code/details, multipart иначе разбирает ошибки | [client.ts](../../../cpm_front_new/lib/api/client.ts) | Исправить client для реального409 UX |
| User delete коммитит credentials раньше student | [delete_user.py](../../cpm_back/services/serv/delete_user.py) | Precheck references до побочных удалений |
| Исходные CREATE TABLE legacy отсутствуют | [migrations](../../migrations/) | Не выдавать BIGINT-FK/nullable предположения за production DDL |

## 2. Исправленные решения

| Область | Проблема предыдущей редакции | Принятое решение |
|---|---|---|
| Объём | Новые экспорты вопреки «не нужно экспортов» | Только импорт/просмотр; existing unrelated reports сохранены |
| Голоса/ready | Общая version отклоняла независимые действия | Vote scoped question/round/actor; ready own member |
| Повторы | Cached full response мог раскрыть чужой myVote | Actor-scoped компактные receipts + fresh role serialization |
| Банк | Только показанные snapshots; продолжение зависело от mutable банка | Immutable full definition при start, shared по configVersion |
| Замены | Replaced question оставлял open round | Empty round voided, новый round1 |
| Циклы | Per-part last ID противоречил any-source из singleton частей | Global last показ + циклы по частям; точные гарантии запаса |
| Readiness | Студент с большим лимитом блокировал всех | Проверка резерва по конкретному assignment |
| Завершение | Последняя кнопка Next могла оставить сданный экзамен без результата | Last consensus auto finalization/extra |
| Апелляция | Та же grade отклонялась, теряя факт | Same-grade appeal допустима, CAS+idempotency |
| Пересдача | «Новая комиссия» не определена | Отличие хотя бы одного ID, не обязательно непересекающийся состав |
| Удаление | Старый ensure мог вернуть удалённую сдачу | History generation + новый key; cleanup всех protocol derivatives |
| Протокол | Бесконечная история в каждом response | Paginated questions/rounds/appeals, compact current state |
| Версии форм | Два version counters на одни settings | Общая configVersion, числовые row versions |
| Импорт | Неоднозначность ID/login, client-supplied errors, нет CAS | Общий owner/TTL/paged-preview/commit контракт |
| Рейтинг | Ошибочная группировка, missing asOf, ненадёжные jobs | Текущая формула, exact start, persisted stale, atomic manual recalc |
| Миграции | Несуществующий registry, nullable classic поля не учтены | Preflight, registry runner, nullable date/name, отдельная025 hardening |
| Каскады | Циклы/RESTRICT не согласованы с delete | Ownership cascades + явный порядок, source IDs immutable scalar |
| Интерфейсы | Разные examType/type, выдуманное part name, нет result list | Единые поля, parts A–Z, отдельный current-results endpoint |

## 3. Границы решений

Подробности, которых нельзя надёжно восстановить из ответов «да/нет» без самих вопросов, не объявлены новыми подтверждёнными пожеланиями. В рамках делегированного проектирования явно приняты: отличие комиссии пересдачи хотя бы одним участником; same-grade appeal; конкретные capacity/latency targets; ручной запуск existing rating recalculation после инвалидации.

Сохраняются предыдущие продуктовые границы: два типа экзаменов; отдельные exam events одного направления допустимы;0..5; один retake; no drafts; REST/manual refresh; отсутствие участия student в проведении; одинаково полноценные mobile/desktop examiner UI; бессрочная готовность; без замены участника/кворума; физическое удаление.

## 4. Передача разработке

1. Прочитать бизнес-ТЗ и [сценарии](./03-acceptance-scenarios.md).
2. Принять [общие соглашения](./development/00-engineering-conventions.md) и [импорты](./development/01-contracts-and-imports.md).
3. Начать FEX-16 preflight параллельно уточнению schema fixtures, затем FEX-01 и порядок из функциональной карты.
4. Каждую FEX оценивать как backend+frontend+DB/тесты; инфраструктурные связи явно учтены.
5. Не включать classic writes до готовности lifecycle/votes/finalization/student/rating/legacy guards вместе.

Проверка документации не выполняла SQL миграции и не меняла код приложения. Исполняемая reference model и статические проверки документации не заменяют тестирование реализации.

## 5. Выполненная проверка этой редакции

`python3 documentation/classic-exams/qa/check_spec.py` завершился успешно:41 Markdown-файл,44 валидных JSON-примера,16+16 пар задач,64 сценария,1728 расчётных комбинаций,10 случаев результата/времени,5922 траектории выбора (322611 показов),2880 порядков голосования. Битых локальных ссылок и незакрытых code fences не обнаружено. Это проверки эталонной спецификации, не утверждение о прошедших API/DB/E2E тестах ещё не реализованной функции.
