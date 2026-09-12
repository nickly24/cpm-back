# Общие контракты импортов и DTO

Обязательное дополнение FEX-01/02/04/06/12. Новых экспортов нет. Примеры таблиц/заголовков показываются в UI, скачивание заполненного листа не требуется.

## 1. Входной Excel

Только .xlsx, один непустой лист, первая строка — заголовки. Суммарный нормализованный preview JSON <=16MiB; сохранять utf8 без ASCII escaping, превышение413. Список возвращается страницами, не16MiB в каждом response. Максимум10MiB compressed file, 64MiB суммарный decompressed ZIP, 5000 data rows, 32 колонки. Проверять до/во время потокового чтения. Formulas не исполняются и не принимаются как значения; cached formula values не использовать. Пустые строки игнорируются; cell types date/bool/error там, где нужен ID/число/text, дают ошибку. Числовой0 отличается от пустой ячейки.

| Импорт | Обязательные данные строки | Разрешённые заголовки |
|---|---|---|
| Вопросы | буква, question/answer | Часть, Вопрос, Эталонный ответ; aliases part_code,question_text,answer_text |
| Outside results | студент, баллы, grade, examiner | student_id,student_login,points,grade,examinator |
| Назначения | студент,1..6 examiner, optional replacements | student_id,student_login,examinator_1_id..examinator_6_id,examinator_1_login..examinator_6_login,replacement_limit |

ID/login не угадывать по виду строки. Для совместимости с ранее описанными `Экзаменатор N`/`examinator_N` считать значение только login; числовой ID указывается в _id колонке. На UI это объяснить до загрузки. Если ID и login заполнены оба, они должны разрешиться в одного role-specific пользователя, иначе conflict. Numeric login не трактовать как ID. ФИО не ключ.

Unknown/duplicate semantic headers —400; неизвестные users/дубли данных — preview row errors. Canonical names/aliases case-insensitive после trim. Login сравнивается по существующей auth semantics, не делать произвольный casefold идентификатора.

## 2. Общая модель sessions

Три type-specific таблицы остаются во владельцах FEX-02/04/06. Каждая содержит:
```text
id BIGINT AUTO_INCREMENT PK
exam_id [exams PK type] FK exams ON DELETE CASCADE
created_by [admins PK type] FK admins ON DELETE RESTRICT
source_filename VARCHAR(255)
preview_payload JSON (raw editable rows + stable rowId)
preview_version BIGINT UNSIGNED DEFAULT 1
status VARCHAR(16): editable/committed
commit_result JSON NULL
created_at,updated_at,expires_at DATETIME(6)
committed_at DATETIME(6) NULL
INDEX(exam_id,created_by,expires_at)
```
Сессия доступна только создавшему admin и выбранному exam/type. TTL72h для editable; expired→410 import_session_expired. После commit редактировать нельзя; повтор commit возвращает commit_result, пока committed session хранится (7 дней после commit), даже если первоначальный editable TTL истёк. Удаление exam удаляет сессию и её raw rows.

Preview DTO:
```json
{
  "id":81,"examId":41,"previewVersion":1,"status":"editable",
  "expiresAt":"2026-09-15T12:00:00+03:00",
  "rows":[
    {"rowId":"row-2","sourceRow":2,"excluded":false,
     "input":{"studentId":125,"commissionMemberIds":[12],"replacementLimit":1},
     "resolved":{"student":{"id":125,"fullName":"Петров Пётр"}},
     "action":"create","errors":[],"warnings":[]}
  ],
  "summary":{"total":1,"included":1,"excluded":0,"creatable":1,"conflicts":0,"errors":0}
}
```
errors/warnings: `{code,field,message,details}`. resolved/action/errors считаются только backend и не доверяются клиенту. Raw input type-specific; текст/числа нормализуются общими validators CRUD. Duplicate check выполняется среди included rows и против БД после редактирования.

## 3. Полные routes

| Тип | Prefix |
|---|---|
| Outside | /api/outside-exam-results-import |
| Questions | /api/exams/{examId}/imports/questions |
| Assignments | /api/exams/{examId}/imports/assignments |

Для каждого prefix (POST/PUT требуют Idempotency-Key; hash parse включает файл/exam):

- POST /parse: multipart file; outside дополнительно exam_id. 201 `data.session` с metadata, первой страницей rows и summary всего preview.
- GET /sessions/{sessionId}?page=1&limit=20&onlyErrors=false:200 `data.session` с текущей страницей rows, pagination и summary всего preview.
- PUT /sessions/{sessionId}: `{expectedPreviewVersion:1,changes:[{rowId,input,excluded}]}`;200 metadata/summary/new previewVersion и изменённые rows.
- POST /sessions/{sessionId}/commit: Idempotency-Key + `{expectedPreviewVersion:2}`;200 `data.result`.

PUT применяет только changes к существующим rowId; omitted row остаётся прежним, новые произвольные rowIds запрещены. Metadata/action/resolved/errors не являются writable полями. Показывать исключённые строки с undo до commit. Только error-free included rows разрешены; пустой included set →422 import_no_rows. Commit нельзя выполнять частично.

## 4. Commit transaction

Parse/preview не создают parts/users/commissions/assignments/results. Commit:

1. exam exclusive lock, затем session lock;
2. ownership/type/TTL/status/version;
3. заново разрешить все accounts/parts/дубли/привилегии из текущей БД;
4. проверить server-stored baselineVersion существующих изменяемых privilege rows; FEX-06 явно показывает old/new value;
5. при любом конфликте rollback и409 import_conflict со списком rowIds; UI просит обновить/пересохранить preview, не применяет остальные строки;
6. атомарно создать rows, новые parts/commissions, увеличить configVersion/ratingRevision соответствующего типа один раз за весь import;
7. сохранить committed status/result/receipt в той же transaction.

Existing result/assignment никогда не перезаписывается импортом. Questions — только create. Template reuse детерминирован FEX-06. Смена name/role/account между preview и commit проверяется повторно; не создавать accounts из Excel.

Commit result:
```json
{"sessionId":81,"committed":true,"counts":{"rows":20,"partsCreated":0,"questionsCreated":0,"commissionsCreated":2,"assignmentsCreated":20,"resultsCreated":0,"privilegesChanged":20},"replayed":false}
```

## 5. Frontend

Использовать apiFormRequest; единый ImportPreviewEditor с type-specific columns, inline edit, exclude/undo, errors per row, save preview before commit. Dirty preview не отправлять на commit — сначала PUT. Commit uncertain: GET session или replay того же key; не запускать новый parse как retry. 409 не стирает введённое; показать conflicts, сохранить локальную форму для ручного исправления.

Два открытых preview tabs —CAS; expired не предлагает повторить commit, только новый импорт. Credentials из full users API не загружать.

## 6. Проверка

Атомарность/повтор, owner/exam mismatch, TTL, numeric login, both ID/login mismatch, formula/ZIP bomb limits, unicode TEXT byte limits, duplicate after edit, hidden row mutation, concurrent commit одинаковых users, stale privilege, empty excluded set. Проверить CORS Idempotency-Key и multipart error mapping.
