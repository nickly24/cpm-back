# FEX-15. Рейтинг и существующие отчёты

## 1. Результат и фактический код

`calculate_exams_rating` сейчас усредняет оценки всех экзаменов периода, а `calculate_final_rating` использует:
```text
final = homeworkAverage * 0.25 + examAverage * 6 + testsAverage * 0.45
examAverage = sum(effectiveGrades) / numberOfExams
```
При пустом наборе examAverage=0. Усреднять экзамены сначала по направлениям нельзя: это меняет существующую формулу. `direction_id` нужен для имени/фильтра, не нового веса.

Сохранённый рейтинг сейчас находится в MySQL Allratings, детали в Mongo rate_rec; `save_all_ratings` сначала удаляет оба набора. Для новой функции этот способ публикации заменить атомарным обновлением готового расчёта — ошибка midway не должна оставлять половину студентов без рейтинга.

## 2. Точный набор экзаменов

В начале расчёта зафиксировать `asOf` UTC и московские calendar dateFrom/dateTo. Загрузить одну согласованную MySQL read snapshot revision/экзаменов/результатов/апелляций, затем освободить transaction. Загружать sparse result maps, не материализовывать декартово произведение students×exams; отсутствующие значения подставляет calculator.

- Outside: date в dateFrom..dateTo включительно.
- Classic: start_at НЕ NULL; start_at<=asOf; московская date(start_at) в периоде. SQL фильтр по UTC-переведённым границам дня и <=asOf, без timezone function на indexed column.
- Classic без полного определения, но с наступившим start_at тоже входит с0: обязательность не зависит от готовности/назначения.
- Один exams.id даёт ровно одну колонку и значение каждому студенту.
- Outside grade=exam_sessions.points; val только информационные баллы.
- Classic effective resolver FEX-11: completed2 иначе completed1, затем latest appeal этой попытки.
- Отсутствие записи, неявка, pending/in_progress first — grade0.
- Pending/in_progress retake — первая остаётся current.
- Отсутствие assignment не исключает экзамен.
- Если start переносится, следующий расчёт использует актуальный start; старые attempt snapshots при этом не меняются.

Пример: направления A: exam5+exam0, B:exam5 → examAverage=10/3, а не(2.5+5)/2. При homework80/tests60 итог20+20+27=67.

## 3. Миграция `024_rating_exam_invalidation.sql`

```text
rating_source_state
- id TINYINT PK (1)
- source_revision BIGINT UNSIGNED DEFAULT 0
- calculated_revision BIGINT UNSIGNED NULL
- date_from,date_to DATE NULL
- calculated_at,as_of DATETIME(6) NULL
- next_exam_start_at DATETIME(6) NULL
- active_job_id [rating_recalc_jobs PK type] NULL
- updated_at DATETIME(6)

Allratings (расширение, без смены прежних типов)
- details_json JSON NULL
- calculation_job_id [rating_recalc_jobs PK type] NULL
- UNIQUE(student_id), только после preflight дублей

rating_recalc_staging
- job_id FK rating_recalc_jobs ON DELETE CASCADE
- student_id [students PK type] (scalar)
- exams,homework,tests,final (точно совместимые числовые типы Allratings)
- details_json JSON NOT NULL
- PRIMARY KEY(job_id,student_id)

rating_recalc_jobs (расширение)
- exam_source_revision BIGINT UNSIGNED NULL
- as_of DATETIME(6) NULL
- worker_token CHAR(36) NULL
- heartbeat_at DATETIME(6) NULL
```

details_json хранит существующую форму детализации, не тексты/голоса classic. rating_id сериализуется из Allratings.id. Новые детали/суммы публикуются в одной MySQL transaction. Mongo rate_rec остаётся read fallback только для legacy row с details_json=NULL, до первого полного успешного v2 расчёта. Его наличие не делает отсутствующую новую строку действующим рейтингом.

## 4. Инвалидация

В той же доменной transaction увеличить source_revision при:

- создании/удалении экзамена, смене направления/даты/start;
- outside create/update/delete/import результатов;
- classic completion, current appeal, удалении истории;
- других изменениях, влияющих на экзаменационный набор.

Один import/command — одно увеличение, replay/no-op — ни одного. Работа с ready/vote до finalization не меняет рейтинг. Revision singleton блокируется последним по общему lock order.

Наступление future start не требует write event: при публикации сохранить ближайший future start рассматриваемого периода как next_exam_start_at; при GET now>=него рейтинг stale. Изменение/удаление start увеличивает revision. Если next start=NULL и revision равна, экзаменационный источник актуален.

Отдельную очередь автоматического полного пересчёта в v1 не создавать. Администратор использует существующий запуск фоновой задачи. В UI:

- stale без job: «Есть изменения. Требуется пересчёт»;
- active job: «Рейтинг пересчитывается»;
- failed job: «Пересчёт не выполнен; показан предыдущий результат».
Студенческий результат экзамена публикуется сразу независимо от rating job.

## 5. Job и публикация

Manual POST /calculate-all-ratings сохраняет текущую форму body `{date_from,date_to}`. Под lock singleton проверяет active_job_id и создаёт ровно одну job атомарно. Существующий check-then-insert не считать защитой от гонки.

Worker:

1. claim queued job через CAS worker_token; захватить exam revision/asOf и materialize exam input одной snapshot;
2. рассчитать всех students, писать staging; Allratings и старые детали пока не менять;
3. при любой ошибке студента/job — status failed, previous published rating сохраняется; partial success не публиковать;
4. в короткой transaction lock singleton, проверить ownership/job/revision и отсутствие пересечённого next_start. При изменении источника пометить job failed со stable code rating_source_changed, сохранить предыдущий рейтинг и предложить перезапуск;
5. atomically upsert Allratings всех студентов из staging, включая details_json/job ID, сохраняя существующие Allratings.id; отсутствующих в актуальном student snapshot убрать по действующей политике;
6. metadata calculatedRevision/dates/asOf/nextStart, job completed и освобождение active_job_id — в той же transaction;
7. staging очистить после успешной публикации/разбора failed job (TTL7d).

Рестарт веб-процесса не должен объявлять все running jobs чужих workers упавшими. Lease heartbeat каждые15s, timeout120s, recovery только просроченной lease; worker проверяет token перед публикацией. Повтор worker с тем же job не публикует дважды. Это backend heartbeat существующей job, не polling экзаменационных экранов.

## 6. Контракты чтения

Существующие routes/roles/поля сохранить: /get-all-ratings, /get-all-rating, /get-rating-details, /my-rating, /student-rating, /ratings-report, /rating-recalc-jobs. Все consumers используют один repository выбора Allratings.details_json или legacy fallback. Не читать «последний Mongo документ студента» независимо от опубликованной MySQL строки.

Добавить top-level:
```json
{
  "ratingFreshness":{
    "isStale":true,"sourceRevision":106,"calculatedRevision":105,
    "dateFrom":"2026-01-01","dateTo":"2026-09-30",
    "calculatedAt":"2026-09-12T12:00:00+03:00",
    "asOf":"2026-09-12T11:59:00+03:00",
    "activeJobId":null,"reason":"exam_data_changed"
  }
}
```
reason null/never_calculated/exam_data_changed/exam_started/recalculation_failed. До первого v2 расчёта legacy metadata unknown→stale; не выдумывать прошлый период. Не менять ответы unrelated APIs.

Stored exam detail сохраняет текущие snake_case keys `exam_id,exam_name,exam_date,score,status` и добавляет `exam_type,direction_id,attempt_no,has_appeal,missing_result`. Reports по-прежнему ключуются exam_id, не attempt_id. Текущий directionName подставляется на чтении по ID; rename отражается без ожидания job. Нет новых Excel/PDF exports.

## 7. Backend-задачи

1. Batch effective grade repository и asOf-period policy.
2. Точная формула без группировки по directions.
3. Migration/invalidation service.
4. Staging + atomic publish + lease/CAS job admission.
5. Общий read repository для всех рейтинговых routes/ratings_report.
6. Details mapping без classic protocol.
7. Integration/contract/period/concurrency tests.

## 8. Frontend-задачи

1. Freshness DTO, понятные stale/running/failed сообщения.
2. Сохранить текущую кнопку пересчёта/журнал; запрет дубля active job.
3. Ratings report получает одну колонку/exam, имена directions актуальные.
4. Студент не видит stale значение как только что пересчитанное; свой экзамен видит сразу.
5. Не добавлять новые exports; existing unrelated export reports сохраняют совместимость.

## 9. Definition of Done и тесты

- Пример A5,A0,B5 даёт10/3, итог67 при указанных остальных компонентах.
- Нет assignment/result→0; пустой exam set→0.
- За1µs до start classic отсутствует; в start присутствует; Московский конец дня включён.
- Future start делает ранее рассчитанный snapshot stale без mutation.
- Retake worse/latest appeal/историческая appeal/delete дают ровно один grade.
- Failed student/Mongo unavailable при расчёте тестов/worker crash сохраняют весь предыдущий опубликованный rating.
- Два manual start создают одну active job; другой worker restart не сбивает живую lease.
- Event во время job не позволяет объявить старые данные fresh.
- Старые поля read/report не потеряны; новые детали не дублируют попытки.
- Существующий коэффициент6 в формуле сохранён: это не ошибочная подпись «из6».

## 10. Область актуальности

Новый sourceRevision отслеживает экзаменационный домен. Он не утверждает транзакционную согласованность изменения тестов Mongo и ДЗ в других процессах; их существующие правила расчёта сохраняются. Freshness DTO дополнительно содержит scope="exams"; UI уточняет, что неактуальны экзаменационные данные. После первой v2 публикации все readers используют MySQL details_json/Allratings вместе; Mongo fallback доступен только для ещё не пересчитанного legacy набора.
