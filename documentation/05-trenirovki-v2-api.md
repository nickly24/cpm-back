# Тренировки v2 — API для фронта

## Терминология

| Термин | Описание |
|--------|----------|
| **Направление** | `directions` (предмет) |
| **Раздел** | manual: `card_themes`; test: проекция теста |
| **Карточка** | Q/A единица заучивания |

## Критическое правило: test-разделы

Раздел из теста показывается **только** если `tests.visible === true`.

Проверка на всех слоях: дерево, study-view, batch, mark-learned.

## Эндпоинты

### `GET /get-training-tree/:studentId`

Направления с разделами (manual + test с `visible=true`).

```json
{
  "success": true,
  "directions": [
    {
      "id": 1,
      "name": "Государство и право",
      "sections": [
        {
          "kind": "manual",
          "refId": "12",
          "name": "Формулы",
          "stats": { "total": 10, "learned": 3, "answer_changed": 1, "unlearned": 6 },
          "total_cards": 10,
          "learned_cards": 3,
          "progress_percent": 30
        },
        {
          "kind": "test",
          "refId": "674abc...",
          "name": "Контрольная 3",
          "sourceTestTitle": "Контрольная 3",
          "stats": { "total": 20, "learned": 0, "answer_changed": 0, "unlearned": 20 }
        }
      ]
    }
  ]
}
```

### `GET /section-study/:studentId/:kind/:refId`

`kind`: `manual` | `test`

Экран раздела: карточки со статусами, батчи, настройки.

Статусы карточки: `unlearned` | `learned` | `answer_changed` (вычисляется по fingerprint).

### `PUT /section-study-settings/:studentId/:kind/:refId`

```json
{
  "batch_size": 10,
  "study_mode": "unlearned",
  "last_batch_index": 0
}
```

`batch_size`: 10, 20 или 30.

`study_mode`: `all` | `unlearned` | `learned` | `stale`

### `GET /section-batch/:studentId/:kind/:refId/:batchIndex?study_mode=unlearned`

Карточки батча с фильтром.

### `POST /mark-card-learned`

```json
{
  "student_id": 1,
  "section_kind": "manual",
  "section_ref_id": "12",
  "card_ref": "card:45",
  "content_fingerprint": "sha256..."
}
```

### `DELETE /mark-card-learned/:studentId/:cardRef`

Сброс прогресса по карточке.

## Админка manual-разделов

- `GET /get-admin-training-catalog` — направления → разделы → счётчики
- `POST /create-training-theme` — `{ name, direction_id }`
- `PUT /training-theme/:id` — `{ name?, direction_id? }`
- CRUD карточек без изменений URL

Направления управляются через справочник тестов (`/directions`), не через training API.

## Миграция

1. `python3 scripts/wipe_training_legacy_data.py --apply`
2. `python3 scripts/apply_migration_013_training_unification.py`

На прод — только после согласования.
