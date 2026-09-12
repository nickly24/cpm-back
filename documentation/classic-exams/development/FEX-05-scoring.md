# FEX-05. Подсчёт, шкала и спорный результат — инженерная декомпозиция

## 1. Результат задачи

Система получает чистый серверный scoring engine и атомарно сохраняемую шкалу 0–5. Админ видит min/max и диапазоны, а последующие задачи используют один и тот же движок для завершения, пересдачи, протокола и рейтинга.

## 2. Изменения БД

### 2.1. Миграция `018_classic_exam_scoring.sql`

```text
classic_exam_grade_thresholds
- exam_id BIGINT NOT NULL FK exams ON DELETE CASCADE
- grade TINYINT UNSIGNED NOT NULL
- min_score INT UNSIGNED NOT NULL
- created_at, updated_at DATETIME(6)
- PRIMARY KEY(exam_id, grade)
```

Для каждого classic exam должны существовать ровно шесть строк grade 0..5 после первого сохранения. Grade 0 всегда `min_score=0`. Приложение валидирует range/strict ordering; если MySQL version позволяет, добавить CHECK grade between 0 and 5.

Использовать единую config_version FEX-03. Read/write возвращает configVersion; full update проверяет expectedConfigVersion и увеличивает её. Банк/parts/settings инвалидируют открытую scoring форму; отдельной версии scoring нет.

Для уже созданных classic exams thresholds не генерировать фиктивно: отсутствие строк является readiness error до сохранения шкалы.

## 3. Backend scoring engine

Создать pure module `services/exams/scoring.py`, не выполняющий SQL и не читающий current time.

### 3.1. Value objects

```text
VoteValue = Decimal('0') | Decimal('0.5') | Decimal('1')
QuestionScore = vote * positive integer weight
RawTotal = sum QuestionScore
RoundedTotal = non-negative integer
Grade = integer 0..5
```

### 3.2. Functions

- `calculate_min_score() -> 0`;
- `calculate_max_score(parts) -> int`;
- `calculate_question_score(vote, weight) -> Decimal`;
- `calculate_raw_total(consensus_items) -> Decimal`;
- `is_fractional_half(total) -> bool`;
- `round_total(total, direction) -> int` using Decimal floor/ceiling;
- `resolve_extra_vote(vote, half_mode) -> up | down | repeat`;
- `grade_for_score(rounded_score, thresholds) -> 0..5`;
- `build_grade_ranges(max_score, thresholds)` for UI/read model;
- `validate_thresholds(max_score, rows)` returning all structured errors.

Ни одна функция не принимает float. API number преобразуется через `Decimal(str(value))` и проверяется against exact allowlist.

### 3.3. Threshold validation

- exactly grades 0..5;
- grade 0 threshold exactly 0;
- grades 1..5 integer;
- strictly increasing;
- each <= max;
- max должен быть >=5, иначе невозможно выделить шесть строго возрастающих уровней;
- ranges continuous because grade is selected as highest threshold <= score.

### 3.4. Service

`ClassicExamScoringService`:

- loads parts/settings/thresholds;
- builds preview even for incomplete config;
- atomically saves full threshold set + fractional settings;
- optimistic lock by configVersion под exclusive exam lock;
- never recalculates completed attempts: presented snapshots/results immutable except appeal.

## 4. REST-контракты

### 4.1. Получение scoring profile

`GET /api/exams/<exam_id>/classic/scoring`

Auth: admin.

```json
{
  "success": true,
  "data": {
    "scoring": {
      "minScore": 0,
      "maxScore": 24,
      "thresholds": [
        {"grade":0,"minScore":0},
        {"grade":1,"minScore":4},
        {"grade":2,"minScore":8},
        {"grade":3,"minScore":12},
        {"grade":4,"minScore":17},
        {"grade":5,"minScore":22}
      ],
      "ranges": [
        {"grade":0,"from":0,"to":3},
        {"grade":1,"from":4,"to":7},
        {"grade":2,"from":8,"to":11},
        {"grade":3,"from":12,"to":16},
        {"grade":4,"from":17,"to":21},
        {"grade":5,"from":22,"to":24}
      ],
      "fractionalMode":"extra_question",
      "tieBreakerSourceMode":"specific_part",
      "tieBreakerPartId":12,
      "tieBreakerHalfMode":"repeat",
      "configVersion":3
    },
    "validation": []
  }
}
```

Если thresholds отсутствуют, вернуть empty array и validation `grade_thresholds_required`, не 404.

### 4.2. Полное сохранение профиля

`PUT /api/exams/<exam_id>/classic/scoring`

```json
{
  "thresholds": [
    {"grade":0,"minScore":0},
    {"grade":1,"minScore":4},
    {"grade":2,"minScore":8},
    {"grade":3,"minScore":12},
    {"grade":4,"minScore":17},
    {"grade":5,"minScore":22}
  ],
  "fractionalMode":"extra_question",
  "tieBreakerSourceMode":"specific_part",
  "tieBreakerPartId":12,
  "tieBreakerHalfMode":"repeat",
  "expectedConfigVersion":3
}
```

200 возвращает GET shape с version+1.

Errors:

- 404 exam/part not found;
- 422 wrong type;
- 400 `invalid_threshold_set`, `max_score_too_low`, `threshold_not_increasing`, `threshold_above_max`, invalid mode/source/half mode;
- 409 `config_modified` (единый код CAS определения).

Полное сохранение требует валидную шкалу и взаимосогласованные mode fields; partial draft правил остаётся доступен через FEX-03 config PATCH, но scoring PUT атомарен.

### 4.3. Внутренний contract для FEX-11

Не делать публичный calculate endpoint. FEX-11 вызывает:

```text
finalize_score(exam_snapshot, consensus_items, tie_breaker_resolution?)
→ rawTotal, roundedTotal, grade, maxScore, resolutionTrace
```

`exam_snapshot` должен содержать сохранённые при старте/показе weights, quotas и thresholds, чтобы админское редактирование не меняло текущую сдачу.

## 5. Frontend

### 5.1. Types/API

Создать exact unions modes, `GradeThreshold`, `GradeRange`, `ScoringProfile`, validation codes. API: fetch/put.

### 5.2. Scoring editor

- read-only cards min/max;
- поля минимального балла для grades 1–5, grade0 fixed;
- live client preview ranges для UX, но server response authoritative;
- controls fractional mode/source/part/half behavior;
- save whole form atomically;
- show all backend validation errors near fields;
- if max changes due parts, mark form stale and refetch before save;
- 409 requires reload/merge decision, never silent overwrite.

### 5.3. Shared presentation

Создать formatters для Decimal .5 без лишних `.0`, grade labels 0–5 и resolution mode labels. Не использовать старые helpers, предполагающие 2–5 или `/6`.

## 6. Backend-задачи

1. Migration thresholds; существующую config_version не добавлять повторно.
2. Pure Decimal scoring module.
3. Threshold repository and service transaction.
4. GET/PUT endpoints.
5. Snapshot-facing interface for FEX-11.
6. Exhaustive unit/property tests.

## 7. Frontend-задачи

1. DTO/API.
2. Scoring editor/ranges preview.
3. Mode dependent controls.
4. Validation/conflict behavior.
5. Shared score/grade formatters and tests.

## 8. Тесты

- every vote×weight combination;
- totals integer/.5 only;
- 17.5 floor=17, ceil=18;
- extra vote 0/1/0.5 for all half modes;
- exact six thresholds, grade0 fixed;
- max<5 and threshold boundaries;
- grade selection at every from/to boundary;
- Decimal serialization;
- atomic rollback threshold/settings update;
- concurrent scoring versions;
- UI preview matches server fixtures.

## 9. Definition of Done

- Scoring is pure, Decimal-based and independent of Flask/DB.
- GET/PUT profile contracts stable and tested.
- Admin can configure every agreed dispute combination.
- Ranges 0–5 visible and valid relative to current max.
- FEX-07 can consume structured validation; FEX-11 can finalize via public service interface.
- No completed result is implicitly recalculated by config edit.
- Backend/frontend CI green.


## 10. Границы и примеры

weight1..1000/quota1..100 и максимум26 частей дают maxScore<=2600000, Decimal storage достаточен. Input boolean не является целым весом/grade. Дополнительный вопрос имеет awardedPoints=0 при любом consensus и не изменяет исходный rawTotal. Даже если floor/ceil попадают в одну grade, configured extra всё равно проводится: правило разрешает дробный балл, не только неоднозначность grade.

Шкала0,1,2,3,4,5 при max5: raw2.5 → down2/grade2, up3/grade3; extra .5/repeat → raw по-прежнему2.5; затем1 →3. При max6 и порогах0,1,2,3,4,6 raw4.5 round-up5 даёт grade4. Изменение max после сохранения шкалы делает readiness invalid, не меняет thresholds автоматически.
