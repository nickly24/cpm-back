# Приёмочные и граничные сценарии

Редакция2026-09-12. Это обязательная матрица QA для FEX; соответствующие задачи реализуют тесты. Наличие таблицы не означает, что API/БД уже протестированы. [Проверка модели правил](./qa/check_spec.py) отдельно проверяет арифметику/выбор, ссылки и JSON-примеры документов.

## 1. Основные сценарии

| ID | Условия / действие | Ожидаемое поведение | Владелец |
|---|---|---|---|
| EX-01 | Создать classic с типом/направлением | Запись есть, date/name не нужны; incomplete без draft | FEX-01/03 |
| EX-02 | Rename направления | Актуальное имя в admin/examiner/student/rating report; ID/оценки прежние | FEX-01/15 |
| EX-03 | Outside points87.75, grade0 или1 | Сохраняется/фильтруется, нет знаменателя6 | FEX-02/14 |
| EX-04 | Legacy grade6 / val с3 знаками | Preflight блокирует конверсию, значение не исправляется молча | FEX-02/16 |
| EX-05 | Delete partB, renameC→D | Остальные коды прежние, ID сохранён; active definition прежний | FEX-04/08 |
| EX-06 | Settings PATCH и scoring PUT из одной configVersion | Один succeeds, второй409; rules не теряются | FEX-03/05 |
| EX-07 | Части изменились после открытой scoring формы | ConfigVersion меняется, stale save409 | FEX-04/05 |
| EX-08 | Max4 при шкале0..5 | Readiness invalid; фиктивные пороги не создаются | FEX-05/07 |
| EX-09 | Raw2.5, thresholds0,1,2,3,4,5 | Down→2/grade2, up→3/grade3 | FEX-05 |
| EX-10 | Raw2.5, extra .5 repeat, затем1 | Raw остаётся2.5, rounded3; extra даёт0 начисленных баллов | FEX-05/11 |
| EX-11 | Floor/ceil попадают в одну grade | Если настроен extra, он всё равно проводится | FEX-05/11 |
| EX-12 | У S1 лимит10, у S2 лимит0, маленький банк | Неготовность S1 не блокирует S2 | FEX-07 |
| EX-13 | Six simultaneous ready из одной страницы | Все ready приняты, без серии stale_state | FEX-08 |
| EX-14 | Two simultaneous ensure/start | Одна attempt, один first question/round | FEX-08/09 |
| EX-15 | Start за1µs до/в/после end | До/в end допустимо при остальных условиях; после нет | FEX-03/08 |
| EX-16 | Active завершается после end | Итог публикуется без таймера/повторного window check | FEX-08/11 |
| EX-17 | Удалить source bank после start | Следующие вопросы из immutable snapshot; протокол воспроизводим | FEX-08/09 |
| EX-18 | Six simultaneous votes текущего round | Все шесть записаны; одна ветвь consensus/dispute | FEX-10 |
| EX-19 | Vote timeout, retry same key | Тот же эффект, не второй vote/новый round | FEX-08/10 |
| EX-20 | Два examiner случайно используют одинаковый key | Разные actor scopes, нет чужого myVote/receipt | FEX-08/10 |
| EX-21 | После dispute доставлен старый vote |409 round_not_current, не голос нового раунда | FEX-10 |
| EX-22 | Replace race с первым vote | Побеждает первый допустимый transition; второй409, лимит не тратится дважды | FEX-09/10 |
| EX-23 | Replace до votes | Старый show replaced + пустой round voided, новый round1 | FEX-09/10 |
| EX-24 | Основной questionQ оценён, Q повторён в extra и заменён | Старые regular points сохраняются, будущий Q запрещён | FEX-09/11 |
| EX-25 | Any source: две части по1 question, R0 | Бесконечные extra чередуются без повторов подряд | FEX-07/09 |
| EX-26 | Specific source: два question, R0 | Непрерывный цикл без повторов подряд | FEX-07/09 |
| EX-27 | Specific: quota1/R1/bank2 | Readiness invalid: после замены остался бы один вопрос | FEX-07 |
| EX-28 | Any: quotas1+1/R1/banks2+2 | Запас достаточен; same-part replacement всегда возможна | FEX-07/09 |
| EX-29 | Last regular consensus | Автоматически published либо extra, отдельный next не нужен | FEX-11 |
| EX-30 | 1000 dispute rounds/extra | Вся история сохранена, текущий response не растёт | FEX-10/11/14 |
| EX-31 | Назначить retake до first completed |422, нового результата/assignment2 нет | FEX-11 |
| EX-32 | Новая комиссия — те же IDs в другом порядке |422 retake_commission_unchanged; заменить одного достаточно | FEX-11 |
| EX-33 | Retake pending, затем completed хуже first | До completed действует first, затем retake; история не удалена | FEX-11/14 |
| EX-34 | First израсходовала replacements | Retake снова получает полный exam/student лимит | FEX-06/11 |
| EX-35 | Appeal grade совпадает с текущей | Факт/marker сохранён; retry не дублирует запись | FEX-11 |
| EX-36 | Two appeals из resultVersion1 | Один succeeds, другой409; previousGrade корректна | FEX-11 |
| EX-37 | Appeal исторической first после completed retake | First history меняется, current/rating остаются retake | FEX-11/15 |
| EX-38 | Delete всей student history | Обе attempts/protocols удалены; assignment1/лимит сохранены, retake assignment удалён | FEX-11 |
| EX-39 | После delete приходит старый ensure |409 generation_changed; новая попытка не воскресает | FEX-08/11 |
| EX-40 | Vote между delete preview и confirm |409 preview_changed; нужен новый preview, частичного удаления нет | FEX-11 |
| EX-41 | Whole exam delete | Никаких attempt/definition/preview/protocol data; users/direction живы | FEX-11 |
| EX-42 | User delete при referenced student |409 ДО удаления credentials/авторизации, account цел | FEX-16 |
| EX-43 | Import numeric login и ID у разных пользователей | Разрешаются по явным колонкам, mismatch row error | FEX-02/06 |
| EX-44 | Preview editable row скрыла errors/action | Commit пересчитывает на сервере и отклоняет конфликт | FEX-02/04/06 |
| EX-45 | Two import commits создают одного assignment/student | Один commit succeeds, второй rollback целиком с409 | FEX-06 |
| EX-46 | Username/privilege изменены после preview | Повторная проверка/CAS; никакой скрытой перезаписи | FEX-06 |
| EX-47 | Expired preview / ошибка row на другой странице |410 expired / commit blocked errors, других записей нет | FEX-02/04/06 |
| EX-48 | Student пытается читать чужой protocol |404, нет hints/персональных голосов | FEX-14 |
| EX-49 | Старый examiner открывает retake другой комиссии |404, first history доступна только по своим membership | FEX-13 |
| EX-50 | Старый GET завершился после mutation/смены аккаунта | UI не откатывается/не показывает предыдущего студента | FEX-12/13/14 |

## 2. Рейтинг и совместимость

| ID | Условия / действие | Ожидаемое поведение | Владелец |
|---|---|---|---|
| EX-51 | A имеет grades5,0; B grade5 | Exam average10/3; не3.75 | FEX-15 |
| EX-52 | Нет result/assignment на наступивший exam | В знаменателе есть exam, значение0 | FEX-15 |
| EX-53 | Start ещё не наступил, но date в периоде | Classic отсутствует в знаменателе до точного start | FEX-15 |
| EX-54 | Сохранённый рейтинг, затем наступил start без API writes | Freshness=stale по next_start | FEX-15 |
| EX-55 | Start NULL / пустой exam set | Нет classic в периоде / average0, без деления на0 | FEX-15 |
| EX-56 | Grade/raw mismatch после appeal | Rating использует effective grade, raw не меняет | FEX-11/15 |
| EX-57 | Worker crash/ошибка одного student | Все прежние рейтинги сохранены, job failed | FEX-15 |
| EX-58 | Изменение exam result в ходе job | Старый расчёт не публикуется как fresh, source_changed | FEX-15 |
| EX-59 | Два запуска rating job / restart другого worker | Одна active job; живая lease не отменяется | FEX-15 |
| EX-60 | Legacy /get-all-exams или delete получает classic ID | Classic не виден/не удаляется legacy маршрутом | FEX-01/16 |
| EX-61 | Browser CORS OPTIONS с idempotency/confirmation headers | Разрешённый origin получает нужные headers, команда работает | FEX-01/16 |
| EX-62 | Browser получает409 JSON/multipart | ApiError сохраняет code/details, UI показывает коррекцию | FEX-01/12 |
| EX-63 | Пересчёт и /my-rating/ratings-report одновременно | Одна опубликованная версия totals/details, не смесь Mongo/MySQL | FEX-15 |
| EX-64 |50 комиссий по6 пользователей | Нагрузка и latency по FEX-16, все инварианты соблюдены | FEX-16 |

## 3. Что проверяется сейчас

Скрипт qa/check_spec.py — исполняемый эталон математики/выборки, а не реализованный backend. Он проверяет точные half-scores, grade boundaries, формулу без изменения весов, допустимые траектории замен/циклов, ссылки и валидность JSON-примеров. API, реальные MySQL locks/migrations, браузер и нагрузка проверяются разработчиками после реализации, по таблице выше.
