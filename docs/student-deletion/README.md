# Durable student deletion

## Audited-schema revision 2

The original simplified-schema package is superseded. The canonical complete
ordinary-SQL delivery is [student-deletion-schema-audit-v2](../../migrations/student-deletion-schema-audit-v2/README.md), with a
[Russian runbook](../../migrations/student-deletion-schema-audit-v2/RUNBOOK_RU.md).
It is generated against the supplied79-table snapshot, includes every literal
write guard, and has read-only exact drift/ownership checks. Python is optional
for reproducing the artifact, not required to apply that SQL.

Retired chat, reads, followers, presence, typed outbox, notifications and push
subscriptions are included without restoring their removed application features.
Unknown event/ownership variants block deletion. All actual incoming FK edges
must remain inside the authorized manifest; cross-owner cascades, SET NULL side
effects, and same-attempt pointer inconsistencies are rejected before deletion.
Exam command rows are sanitized into retained idempotency tombstones, never
wholesale deleted. Minimal captured command IDs survive in the completed job.

Read the current [verification report](VERIFICATION.md) for exact pass/skip counts
and limitations.031 is not compatible with every old write sequence; stop old
writers and coordinate code delivery before activation, even if deletion is off.

This feature is **disabled for new operations by default**. It is an active-store,
student-scoped deletion workflow, not a shared exam/homework/test deletion and not
a promise to erase backups or every historical, unattributed storage object.
No migration or production data operation is performed at application startup.

## API and authority

Every endpoint requires a currently authenticated `admin` or `staff_admin` with
`users/edit`, including preview, status, history and retry:

- `GET /api/students/{id}/deletion-preview`
- `POST /api/students/{id}/deletion-jobs` with only `confirmation_token` and a UUID `idempotency_key`
- `GET /api/students/{id}/deletion-jobs`
- `GET /api/student-deletion-jobs/{uuid}`
- `POST /api/student-deletion-jobs/{uuid}/retry`

The old student `delete-user` service always returns409
`student_deletion_workflow_required`; other roles keep their existing behavior.
The preview is signed for one actor, student, complete data fingerprint and five
minutes. A changed dataset requires a fresh confirmation. A repeated identical
request returns the same durable job even after its response was lost. The
browser supplies no SQL names, Mongo selectors or S3 paths.

Unavailable required sources return `available=false`, null counts and no token.
An absent table outside the required deployed modules is not treated as a failed
connection, but an unknown student ownership column, incoming FK, Mongo ownership
layout, missing primary key or ambiguous personal import snapshot blocks deletion.
The homework capability must be fresh, storage-ready and meet its explicit
operator-confirmed completion policy. No amount of UI confirmation overrides this.

## Scope and counts

`inventory.py` is the executable allowlist. It covers:

- Legacy homework grades, file-service submissions, drafts, registered files,
  upload jobs, staging keys and durably reserved processed-output keys
- Outside exam results, classic assignments, privileges, attempts, results,
  attempt-local questions, votes, appeals and commission snapshots
- MySQL external-test results; Mongo `test_attempts`, `test_sessions`, `rate_rec`
  using both numeric and string values of their supported student-ID fields
- Attendance, leave requests/dates/SQL image blobs, training progress/settings,
  ratings and rating staging, student-linked Telegram records
- Credentials, authentication rows, student profile
- Attributable personal rows inside shared import previews/reports/receipts,
  without deleting other students' rows or whole shared batches

Shared homework definitions, tests, questions, exams, commissions, groups,
proctors and other students are preserved. Actual information_schema FK order is
combined with the known logical child relationships; deletion does not assume
CASCADE. A cyclic/unknown relationship blocks rollout or the preview.

Headline homework/exam/test totals count distinct learning objects, never an
attempt and its resulting session as two tests. External SQL tests and internal
Mongo tests use separate namespaces. Submissions, attempts and results remain
separate counts. Homework file count is the number of distinct **known registered
storage keys** across file metadata, uploads and ownership history, including keys
that may already be absent. Details separately show PDF metadata records, upload
jobs and known keys. It is not a live bucket-size or object-count measurement.

Old unattributed processed objects and outbox-only keys cannot safely be assigned
to a student. They require separate inventory reconciliation; no prefix is erased.
Existing backups, logs and external exports have separate retention policies.
The UI must not describe this operation as erasing every physical copy everywhere.

## Ordering, races and recovery

1. All cooperating services use MySQL `GET_LOCK('cpm:student-write-fence')` before
   mutation/domain locks. The conservative global fence also protects legacy GET
   handlers that mutate test state. It trades throughput for a simple shared
   boundary; long file operations can cause retryable busy responses.
2. SQL INSERT/UPDATE triggers lock an InnoDB singleton in shared mode. Barrier
   insertion obtains it exclusively, draining SQL transactions already in flight.
   After this drain, a READ COMMITTED rescan validates the signed fingerprint.
   A mismatch rolls back the barrier before any destructive work.
3. `student_deletion_barriers` is permanent, immutable revocation state, independent
   of the feature toggle for accepting new jobs. Student login/old JWT, target
   writes, imports, ratings, Mongo writes and homework finalization consult it.
4. Job and homework purge request are committed with the barrier. The homework
   worker commits its exact-key manifest before unlinking ownership rows. Output
   keys are persisted before any new processed upload, closing future orphan gaps.
5. Homework runs its final exact-key delete after known presign expiry plus the
   verified provider drain bound, verifies absence, and reports durable receipts.
   Backend waits, then removes Mongo documents, SQL personal data, and finally the
   account, after a final rescan. Account failure never marks the job completed.
6. Each backend tick takes a durable lease and commits its checkpoint. A crash after
   Mongo/S3 delete but before checkpoint safely repeats a student/key-scoped delete.
   SQL deletion and its checkpoint share a transaction. Expired leases are resumed
   by the persisted queue on the next process; manual retry continues the same job.

There is no distributed rollback. On partial failure the account stays blocked.
Closing/reloading the dialog cannot cancel an accepted operation. Progress counts
committed manifest units;100% is emitted only after final verification. The final
backend journal drops the name, group and per-record manifest, retaining only verified status and captured idempotency-command IDs; minimum operation
IDs, actor, counters and immutable student-ID tombstone remain. The homework
service retains opaque key receipts needed for safe repeat cleanup, no PDF bytes.

## Deployment gates (operator work; not executed by this change)

Start with read-only preflight. Migration031 activates database-level validation
immediately, even while new deletion acceptance is disabled. Never promise it is
safe for old running writers.030/002 preparation and031 activation are separate
CLI phases. If the requested publication order would require activating031 before
coordinated code delivery, keep writers stopped throughout or obtain approval for
a revised phased rollout. Do not silently switch the user's sequence.

1. Back up and read-only inspect the **actual** production schema, FK graph,
   MySQL privileges/trigger definitions, Mongo legacy owner formats and bucket
   versioning/retention/provider upload-completion behavior. Resolve every reported
   unknown table/fragment safely. Rehearse on a synthetic staging copy first.
2. Stop all old backend/homework/import/rating/Telegram writers. No advisory lock
   can fence an old uninstrumented Mongo process. Do not do a mixed-version rollout.
3. Explicitly apply backend030; homework001 if needed and002; backend031 and
   generated per-table guards. The guarded installer validates actual schema:

   ```sh
   # MYSQL_PASSWORD must be explicitly set. No repository connection defaults load.
   python scripts/student_deletion_schema.py preflight --database cpm --user operator --host 127.0.0.1
   python scripts/student_deletion_schema.py base --database cpm --user operator \
     --host 127.0.0.1 --confirm APPLY_STUDENT_DELETION_SCHEMA
   # Existing homework001 must already be installed; preflight checks prerequisites.
   python scripts/student_deletion_schema.py homework --database cpm --user operator \
     --host 127.0.0.1 --confirm APPLY_STUDENT_DELETION_SCHEMA
   # Keep every writer stopped until the following apply and check succeed.
   python scripts/student_deletion_schema.py apply --database cpm --user operator \
     --host 127.0.0.1 --confirm APPLY_STUDENT_DELETION_SCHEMA
   python scripts/student_deletion_schema.py check --database cpm --user operator --host 127.0.0.1
   ```

   Install backend030 with `base` before homework002; run backend `apply` after002 to
   install/verify guards on its added ownership table. SQL DDL implicitly commits;
   a partial failure is a maintenance blocker, not a rollback. The installer is
   repeatable while writers are stopped. Remote hosts require explicit `--allow-remote --ssl-ca /trusted/provider-ca.pem` with verified TLS (or use an already configured secure local tunnel).
4. Deploy both new services and UI. Verify schema checks, service capability,
   local/staging synthetic acceptance cases, storage-policy gate and lease recovery.
   Set `STUDENT_DELETION_ENABLED=true` only after the required homework policy is
   explicitly verified (`STUDENT_PURGE_STORAGE_POLICY_VERIFIED=true` only after the
   provider review). Unknown storage guarantees must keep the capability false.
5. On disable/rollback, preserve barriers and their schema and keep revocation-aware
   code serving traffic. Never restore an older authentication/Mongo/file writer.
   A restore from backup must reapply the tombstone journal before admitting traffic.

New coordinator barrier/job/purge creation and updates use explicit UTC timestamps;
server session time zone must not shorten a legacy presigned-upload waiting period.

## Local verification

Install test-only dependencies in an isolated environment and use the guarded
runner, which substitutes a synthetic config before imports and refuses production
configuration reads and non-loopback Python connections:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python scripts/run_local_tests.py discover -s tests -v
```

`tests/test_student_deletion.py` uses a local synthetic SQL-driver fake backed by
SQLite plus mongomock. It exercises the actual Flask routes and coordinator code
with a populated target, empty target, and another student sharing all definitions.
It covers authority, bound/expired/stale confirmation, unavailable dependencies,
idempotency, crash-before-checkpoint, lease recovery, exact preview counters,
shared import redaction, final verification and the blocked legacy route.

`tests/test_student_deletion_fence.py` covers revocation and mutation safety.
Homework has separate isolated fake-S3 protocol tests; frontend has DOM interaction
and pure API/state tests. These are **not** evidence of live MySQL/Mongo/S3 distributed
concurrency. Real MySQL single-engine initialization checks validate actual DDL,
generated trigger bodies and denial assertions without starting a network server.
Concurrent multi-process service tests, real Mongo transactions/behavior and provider
storage guarantees remain mandatory staging/release gates when unsupported locally.

### Prior-version compatibility and rollback boundaries

030 and002 add independent tables, nullable upload fields, wider retry counters and
ownership backfill; they do not remove or rename existing application columns.
They are intended as additive changes, but backfill requires verified ownership
and stopped writers.031 drops/recreates only this feature's `cpm_assert_student_*`
routines and `cpm_sd_*` / `cpm_sdb_*` triggers; it adds strict validation to old
writers too. Do not claim backward compatibility from syntax tests alone: exercise
existing imports, grading, ratings, account creation and malformed-preview handling
against the actual preflighted schema before reopening old code.

The installer role needs the relevant CREATE/ALTER, TRIGGER, CREATE ROUTINE,
ALTER ROUTINE and EXECUTE privileges. Runtime verification needs metadata visibility
for table engines/FKs and full trigger/routine definitions, plus execution of guard
routines through triggers. Check existing privileges read-only first; this change
does not create users, grant permissions, or store new credentials.

Before any deletion is accepted, an operator may leave additive tables/columns in
place and keep new operations disabled. Removing guards is not a safe rollback
once a barrier exists. After a deletion begins, recovery is forward-only via the
same job; reverting code, dropping tombstones, or restoring an old backup without
replaying them can restore unauthorized access and is prohibited by this protocol.
Production schema migration, deployment and content deletion are separate actions;
none was performed by local implementation or tests.
