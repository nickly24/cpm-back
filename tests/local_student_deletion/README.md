# Локальная проверка удаления без новых миграций

`test_simple_live.py` запускает реальный координатор удаления и файловый worker
на отдельных синтетических базах и S3 HTTP-эмуляторе. Приложение и production
конфигурация не запускаются. `scripts/run_local_tests.py` подменяет настройки
тестовыми, запрещает чтение .env/config и любые подключения вне loopback.

Для каждого теста создаются MySQL и Mongo базы с уникальным именем
`cpm_simple_synthetic_<uuid>`; только эти базы удаляются при завершении.
`audited-schema.sql` содержит 79 CREATE TABLE из ранее полученного аудита —
только структуру, без данных. `synthetic-seed.json` содержит вымышленные записи.
DDL выполняется по зависимостям FK без отключения FOREIGN_KEY_CHECKS. Новых
защитных таблиц или триггеров в стенде нет. До и после удаления сверяются
79 таблиц и отсутствие триггеров. Этот SQL нужен только тестовому стенду:
он не является миграцией или инструкцией для production.

## Требования

- Отдельный disposable MySQL 8 с пустым тестовым паролем root, UNIX socket внутри
  `CPM_LOCAL_TEST_RUNTIME` и, для других opt-in тестов проекта, loopback 33077.
- Отдельный MongoDB на `127.0.0.1:27077` с вымышленными данными.
- Backend и файловый сервис рядом: `cpm-back/` и `cpm-homework-service/`.
- Изолированный Python virtualenv с `requirements-live-test.txt`.

Проверено на Oracle MySQL Community 8.0.46 и MongoDB Community 8.0.30.
S3 поднимается и останавливается самим тестом: Moto 5.2.3 на случайном loopback
порту, настоящий `HomeworkStorage` и boto3 1.40.40. Передаются только короткие
синтетические байты; реальные документы не используются.

## Запуск

Из backend, после запуска этих локальных серверов:

```sh
.venv/bin/python -m pip install -r requirements-live-test.txt
export CPM_LOCAL_TEST_RUNTIME=/absolute/path/to/disposable/runtime
export EXAM_MIGRATION_TEST_SOCKET="$CPM_LOCAL_TEST_RUNTIME/mysql.sock"
export CPM_SIMPLE_DELETION_LIVE=1
.venv/bin/python scripts/run_local_tests.py discover   -s tests/local_student_deletion -p test_simple_live.py -v
```

Обычный тестовый прогон, не требующий работающих серверов:

```sh
.venv/bin/python scripts/run_local_tests.py discover -s tests -v
```

У других real-MySQL тестов есть отдельные opt-in флаги и исходные тестовые схемы;
они не создаются этим файлом. Для `test_classic_exam_mysql.py` нужна только
локальная синтетическая схема `cpm_exam_rehearsal`, подготовленная существующим
rehearsal-инструментом. Не используйте дамп персональных данных для этих тестов.

## Сценарии

Девять интеграционных тестов покрывают полный путь API → очередь → MySQL → Mongo
→ S3; сохранение другого ученика, сотрудника с совпадающим числовым ID и общих
заданий; полный SQL rollback; ошибку S3 и повторную очистку; восстановление после
COMMIT до записи статуса; отказ старому student JWT; защиту файла, переданного
другому ученику; фоновый worker с доступным статусом и промежуточным прогрессом;
различие регистрозависимых S3-ключей при существующем регистронезависимом SQL
индексе очереди; отказ от удаления, если личные данные находятся в таблице
без транзакционного отката (MyISAM).

Moto подтверждает работу кода с S3 HTTP API, но не проверяет права, политику,
версионирование, хранение или поздние загрузки настоящего провайдера. Проверка
не подключается к production MySQL/Mongo/S3 и не является production preflight.
