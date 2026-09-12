# FEX-03. Конфигурация классического экзамена — инженерная декомпозиция

## 1. Результат задачи

У экзамена типа `classic` появляется постепенно заполняемая конфигурация периода и спорного результата. Она сохраняется частями, не вводит статус черновика и выдаёт стабильный read/write contract будущим экранам и readiness validator.

## 2. Изменения БД

### 2.1. Миграция `016_classic_exam_settings.sql`

Создать one-to-one таблицу:

```sql
CREATE TABLE classic_exam_settings (
    exam_id BIGINT NOT NULL PRIMARY KEY,
    start_at DATETIME(6) NULL,
    end_at DATETIME(6) NULL,
    fractional_mode VARCHAR(32) NULL,
    tie_breaker_source_mode VARCHAR(32) NULL,
    tie_breaker_part_id BIGINT NULL,
    tie_breaker_half_mode VARCHAR(32) NULL,
    config_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_classic_settings_exam
        FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Типы FK выровнять с production PK. FK `tie_breaker_part_id` добавляется в FEX-04 после создания parts; до этого поле nullable и проверяется сервисом.

Allowlist:

- `fractional_mode`: `round_up | round_down | extra_question`;
- `tie_breaker_source_mode`: `specific_part | any_part`;
- `tie_breaker_half_mode`: `repeat | round_up | round_down`.

Settings row создаётся автоматически вместе с новым classic exam после включения FEX-03. Для уже созданных classic rows выполнить idempotent `INSERT ... SELECT ... WHERE NOT EXISTS`.

### 2.2. Хранение времени

- В БД `start_at/end_at` хранить как UTC в `DATETIME(6)`.
- На входе naive datetime запрещён; API требует ISO offset.
- В ответе сериализовать в `Europe/Moscow` с `+03:00`.
- Все сравнения «можно ли стартовать» выполняются с UTC server time.

## 3. Backend

### 3.1. Доменные типы

```text
ClassicExamConfig
- examId: int
- startAt: ISO datetime | null
- endAt: ISO datetime | null
- fractionalMode: round_up | round_down | extra_question | null
- tieBreakerSourceMode: specific_part | any_part | null
- tieBreakerPartId: int | null
- tieBreakerHalfMode: repeat | round_up | round_down | null
- configVersion: int
- updatedAt: ISO datetime
```

### 3.2. Локальная валидация update

Частичное состояние допустимо, но переданные значения валидируются:

- start/end — valid aware ISO;
- если обе даты присутствуют, `end > start`;
- enum values только allowlist;
- `specific_part` допускает nullable part до его последующего выбора, но readiness будет invalid;
- при `fractional_mode != extra_question` tie-breaker fields очищаются до null сервером;
- при source `any_part` `tie_breaker_part_id` очищается;
- update неизвестного поля возвращает 400, а не молча игнорируется.

### 3.3. Сервисы

`ClassicExamConfigService`:

- type guard `classic`;
- get settings без записи в GET; row создаётся только create/migration, отсутствие — диагностическая ошибка;
- convert timezone;
- partial update с exclusive exam lock и CAS config_version; ту же версию увеличивают parts/questions/scoring;
- normalise dependent fields;
- вернуть local validation warnings отдельно от сохранения.

`ClassicExamWindowPolicy`:

- `can_start(config, now_utc)`;
- возвращает structured result `allowed/code/startAt/endAt`;
- не знает о комиссиях, вопросах или попытках.

## 4. REST-контракты

### 4.1. Получение конфигурации

`GET /api/exams/<exam_id>/classic/config`

Auth: admin.

200:

```json
{
  "success": true,
  "data": {
    "config": {
      "examId": 41,
      "startAt": "2026-08-20T09:00:00+03:00",
      "endAt": null,
      "fractionalMode": "extra_question",
      "tieBreakerSourceMode": "any_part",
      "tieBreakerPartId": null,
      "tieBreakerHalfMode": "repeat",
      "configVersion": 4,
      "updatedAt": "2026-08-04T13:00:00+03:00"
    },
    "localValidation": [
      {"code": "end_at_required", "field": "endAt", "message": "Укажите окончание экзамена"}
    ]
  }
}
```

### 4.2. Частичное сохранение

`PATCH /api/exams/<exam_id>/classic/config`

Auth: admin.

```json
{
  "startAt": "2026-08-20T09:00:00+03:00",
  "endAt": "2026-08-20T18:00:00+03:00",
  "fractionalMode": "extra_question",
  "tieBreakerSourceMode": "specific_part",
  "tieBreakerPartId": 12,
  "tieBreakerHalfMode": "repeat",
  "expectedConfigVersion": 4
}
```

200 возвращает ту же форму, что GET, с новой version.

Ошибки:

- 404 `exam_not_found`;
- 422 `wrong_exam_type`;
- 400 `invalid_datetime`, `invalid_time_window`, `invalid_fractional_mode`, `invalid_tie_breaker_mode`, `unknown_field`;
- 404 `part_not_found` после FEX-04;
- 409 `config_modified` с актуальной version.

Частичный payload может сохранить incomplete config; 422 `exam_not_ready` здесь не используется.

### 4.3. Проверка окна для внутренних consumers

Отдельный публичный endpoint не нужен. FEX-08 вызывает policy на backend. Frontend отображает период из GET config.

## 5. Frontend

### 5.1. Типы/API

Создать discriminated unions для modes и отдельные `ClassicExamConfigDto`, `ClassicExamConfigForm`. Form хранит datetime-local strings; adapter преобразует их в ISO с московским offset и обратно.

API:

- `fetchClassicExamConfig(examId)`;
- `updateClassicExamConfig(examId, patch)`.

### 5.2. Admin config panel

- независимые секции «Период» и «Спорный результат»;
- сохранение по явной кнопке, не autosave;
- datetime-local поля с подписью «Московское время»;
- conditional source/half fields только для extra question;
- выбор части загружается из FEX-04, до него specific mode показывает empty selector;
- inline localValidation после ответа;
- 409 предлагает перезагрузить свежую конфигурацию, не перетирая молча.

### 5.3. Placeholder readiness

До FEX-07 localValidation отображается как предупреждение. Не вводить клиентский статус draft и не разрешать frontend самостоятельно объявлять экзамен ready.

## 6. Backend-задачи

1. Migration/table/backfill settings rows.
2. Enum/domain DTO/time conversion helpers.
3. Repository row-lock/version update.
4. Config service + window policy.
5. GET/PATCH endpoints и tests.
6. Интегрировать settings creation в ExamCoreService transaction.

## 7. Frontend-задачи

1. Config DTO/form types и timezone adapter.
2. API functions.
3. Period/scoring-rule panel.
4. Conditional fields и validation messages.
5. Conflict/loading/error/component tests.

## 8. Тесты

- partial states сохраняются;
- valid Moscow/UTC conversion and boundaries;
- reject naive/invalid/end<=start;
- changing fractional mode clears obsolete fields;
- any part clears part ID;
- classic type only;
- concurrent versions produce 409;
- start window policy inclusive at exact endpoints;
- active attempt finishing after end относится к FEX-08, но policy start correctly denies.

## 9. Definition of Done

- Settings schema применима поверх FEX-01.
- Создание classic atomically creates settings.
- GET/PATCH contracts реализованы и документированы.
- Partial configuration поддержана без draft status.
- UTC storage/Moscow API подтверждены tests.
- Admin panel сохраняет все rule combinations и обрабатывает conflict.
- Никакая часть этой задачи не запускает сдачу и не вычисляет global readiness.


## 10. Общая версия и период

Scoring PUT и config PATCH конкурируют по одной configVersion. PATCH merge выполняется до валидации start/end. Null очищает поле, missing оставляет прежнее. Продолжение active использует immutable definition. Изменение периода увеличивает rating revision; до start можно создать pending/ready, только start требует временного окна.
