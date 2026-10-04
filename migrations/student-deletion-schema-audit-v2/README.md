# Student deletion: audited-schema SQL bundle

This bundle targets the metadata-only snapshot collected 2026-10-03: **79 tables,
579 columns, 66 foreign-key column rows, 193 indexes (294 index-column rows), four
existing procedures, and no existing triggers/events**. The source reports server
version `8.0.22-13`; it does not establish the server vendor. No production database
or object storage was accessed to produce or test this bundle.

**Default action: run only `000_preflight_read_only.sql`.** It reads metadata and
aggregate counts. No student names, messages, credentials, object keys, or row
identifiers are returned. Read-only checks do not certify that unobserved external
writers, another schema, MongoDB, or object storage are safe.

## Files

- `000_preflight_read_only.sql`: exact baseline table/column/FK/index and procedure
  definition checks, plus aggregate ownership/orphan checks
- `030_002_prepare.sql`: full backend 030 and homework 002 preparation; the audited
  schema already includes homework 001 tables. It creates durable deletion and
  ownership records and backfills attributable file keys. It deletes no students.
- `031_activate_all_write_guards.sql`: full activation, including **all concrete
  triggers**, routines, and barrier guards; no Python installer is needed
- `032_verify_and_detect_drift.sql`: read-only exact post-install checks
- `expected-baseline.json`, `expected-post-activation.json`, `manifest.json`:
  metadata expectations and reproducible source hashes, not personal records

## Run with an ordinary mysql client

Use a reviewed operator account and your normal secure connection settings. Select
one intended database explicitly. Do not put passwords in command arguments.
Replace `DATABASE_NAME` below with that database. These commands do not create a
connection automatically; execute them only in the environment you intend.

```sh
mysql --batch --show-warnings DATABASE_NAME < 000_preflight_read_only.sql
```

Every `mismatch_count` must be zero, or have an explicitly reviewed explanation
before any migration. An error or unavailable metadata is a blocker. Do not waive
unknown tables, changed columns/FKs, orphan ownership, or changed routines. The
expected original procedure bodies are compared, **never installed or invoked**.
Metadata visibility can omit inaccessible schemas; have an appropriately
privileged operator establish both incoming and outgoing cross-schema references.

After reviewing the baseline, coordinate deployment of the matching backend,
homework service, and all workers. Installing the barrier table is detectable by
new application code even with deletion acceptance disabled. Preparation is not a
promise of compatibility with an old application or a zero-downtime rollout.

```sh
mysql --batch --show-warnings \
  --init-command='SET @cpm_deletion_prepare_confirmed=1' \
  DATABASE_NAME < 030_002_prepare.sql
```

Before activation, stop and drain **every writer and dispatcher**: backend web and
job processes, homework uploads/workers, imports/rating workers, legacy chat,
Socket.IO/realtime/push workers, maintenance scripts, and direct SQL/Mongo writers.
Account for already-issued object-upload capabilities and in-flight storage work.
An advisory database lock does not stop an old or unrelated writer.

Only after the maintenance window is confirmed:

```sh
mysql --batch --show-warnings \
  --init-command='SET @cpm_deletion_maintenance_confirmed=1' \
  DATABASE_NAME < 031_activate_all_write_guards.sql
mysql --batch --show-warnings DATABASE_NAME < 032_verify_and_detect_drift.sql
```

The mutating files stop before persistent work when their session acknowledgement
is absent. Use **batch input redirection**, not interactive `SOURCE`, and never use
`--force` or a client configured to continue after SQL errors. Their guards depend
on normal stop-on-error client behavior. Review and securely retain the complete
client output and return code.

DDL auto-commits. Any partial failure leaves deletion disabled and all writers
stopped. Inspect the failure; repair with this exact bundle and repeat verification.
Do not drop tombstones/barriers to unblock traffic. Preparation can be repeated;
activation deliberately replaces the matching routine/trigger definitions.

Resume only the matching, fully fenced application versions after all checks pass.
Enable deletion acceptance only after coordinated readiness, worker capability,
MongoDB/provider integration, and concurrency checks have separately passed. The
bundle does not enable an application feature flag, run a deletion, or certify
those integrations.

## Exactness and drift

Column names, order, type, nullability, key classification extra attributes, defaults, column character sets/collations and generated expressions;
FK names, columns, parents and actions; index names, column order, prefixes,
uniqueness, visibility and expressions; and all table engines/collations are
compared with the recorded expectations. Defaults and column character metadata are reconstructed from the supplied full
CREATE TABLE DDL in the isolated engine; every original column attribute is
matched back to the supplied audit before that enriched baseline is accepted.

Routine/trigger definitions are hashed exactly after trimming outer whitespace
and normalizing CRLF to LF. Literal case and internal spacing are significant.
The generated activation is formatted to match its verified persisted bodies.
Installation through another formatter can therefore fail closed even if it is
semantically equivalent. Existing audit procedures are unchanged. The checks
also reject unexpected procedures, triggers, tables, columns, FKs, and indexes.

`utf8mb3` and legacy `utf8` metadata aliases are normalized for Oracle 8.0.46.
FK metadata CTEs use `DISTINCT` to avoid an observed Oracle 8.0.22 optimizer
miscomparison when derived information-schema rows are merged.

## Reproduction and limitations

Operator execution requires only mysql with `DELIMITER` support. Python is needed
only to regenerate the delivered files or rerun the isolated developer harness:

```sh
python scripts/generate_student_deletion_sql.py --audit /path/to/reviewed-audit
CPM_TEST_MYSQL_BASE_DIR=/path/to/official/mysql \
  python tests/local_student_deletion/verify_actual_schema.py \
  --audit /path/to/reviewed-audit
```

See `tests/local_student_deletion/README.md` and `VALIDATION.md` for exact engine
versions, successful assertions, remaining untested areas, and runtime provenance.
Never execute the supplied historical Python/SQL scripts or historical procedures
as part of the harness. It admits only individually validated InnoDB CREATE TABLE
shapes and orders their FK dependencies without disabling FK checks.

## Complete chat-sized SQL parts

`user-sql/` contains the same complete literal statements split into numbered
parts, each under 80,000 characters. No trigger or dynamic fragment is omitted.
Run every part of a phase in numerical order. Read-only parts are independent.
Current preparation and activation phases each fit in one part. If a later
regeneration splits a mutating phase, concatenate its numbered parts into one
batch input so the acknowledgement/session variables remain in the same session.
The mysql CLI processes each part's explicit `DELIMITER` commands.

New migration tables explicitly choose `utf8mb4_0900_ai_ci` rather than inheriting
an unreported database default. Object-key columns explicitly retain `ascii_bin`.
No existing table is converted or assigned a different collation by this bundle.

Preparation now checks historical object keys before any ownership backfill, even
under non-strict SQL mode. It aborts on non-ASCII, empty/blank or over-512-byte keys,
unattributable owners, exact-key owner/submission conflicts, and distinct byte
strings that alias under the registry's `ascii_bin` collation (including trailing
spaces). It never repairs or merges content. Additive tables/columns and the
feature-owned preflight procedure may remain after an abort; keep deletion
disabled and review the data before retrying. The three ownership backfill joins
compare keys as binary bytes, preserving case-distinct object keys.
