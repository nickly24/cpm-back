# Validation results — 2026-10-03

## Passed against frozen sources

Two isolated, real engines completed the same **1,443 SQL statements** and
**398 uniquely named checks each**, with exit status 0, a success marker for every
statement, and no error-log errors:

- Oracle MySQL Community Server **8.0.22**, Linux x86_64
- Oracle MySQL Community Server **8.0.46**, Linux x86_64

Both use initialization-only `mysqld --initialize-insecure --skip-networking
--mysqlx=0 --init-file=...`. No database listener or production connection is
started. No application configuration, database credentials, or real personal
rows are loaded. Object storage is an in-memory synthetic stub.

The complete **79-table** CREATE TABLE baseline is created with real foreign keys
and constraints enabled. Original metadata comparison passes for 579 columns,
66 FK column rows, 193 indexes / 294 index-column rows. Column defaults, character
sets/collations, comments and generated expressions are reconstructed from that
same reviewed DDL. Historical scripts and all four original procedures are never
installed or executed.

The final schema has **87 tables, 650 columns, 100 triggers, and 23 new guard
procedures**. The production postcheck additionally expects the four unchanged
original procedures, for 27 total. The 100 triggers include all 97 concrete
schema-specific triggers plus three immutable-barrier triggers.

The delivered read-only preflight and postcheck SQL is itself executed. All
metadata and aggregate ownership counts are zero except the **four intentionally
absent historical procedures** in the isolated fixture. Production must report
zero for that check too. This is an explicit omission, not evidence that those
procedures were installed or tested.

The application's readiness checker accepts the actual persisted engine metadata.
Changing a role literal's case is rejected by readiness and by the literal SQL
body-hash checks. Added-column and missing-FK drift probes are also detected and
restored within the isolated test.

Eight structural tests pass for read-only defaults, explicit preparation and
activation acknowledgement gates, complete concrete triggers, exact body hashing,
source fingerprints, the full baseline, safe DDL admission and complete numbered
SQL chunks under 80,000 characters. No structural test failed.

## Covered behavior

- Backend030/homework002 repeatability, including backfill with existing content
- Preparation rejection under non-strict SQL mode of non-ASCII, empty/blank,
  oversized, conflicting-owner and target-collation-alias object keys
- Exact binary, case-distinct historical-key backfill and repeated preparation
- Strict direct, indirect and OLD/NEW ownership guards, composite primary keys,
  no-FK orphans, and same-numeric-ID student/staff collisions
- Six typed realtime event kinds, stale snapshots, wrong thread/message links,
  unknown/malformed events and role case
- Numeric, string and zero-padded ownership IDs in explicit JSON scalar, list and
  nested student paths; unrelated generic staff IDs remain permitted
- Authorized command sanitation and retained tombstones; altered fields, replay,
  and tombstone DELETE are rejected
- Cross-submission file pointers, cross-owner exact keys, late adoption after
  SQL unlink and after ownership-ledger removal, with retained purge receipts
- Classic assignment/question/replacement/round/vote and attendance/zap links
- The actual current submit, remove-draft, resubmit and finish-job SQL, including
  clearing/repointing references before file deletion, in rolled-back scenarios
- The actual current purge inventory, unlink, exact-delete receipt and completion
  SQL; every captured aggregate-read assumption is checked by the engine
- Other students, role-colliding staff, shared homework/exams and durable deletion
  barriers preserved

## Limits: not run / not established

- The exact reported **8.0.22-13** distribution/vendor build. The tested binaries
  are Oracle Community builds; they do not certify Percona or another vendor.
- Concurrent multi-client locking, stale transactions across clients, actual
  process crashes, or live mysql connector behavior
- HTTP endpoint integration, live MongoDB, S3/provider calls, upload capability
  expiry against an actual provider, or deployed legacy-writer drain
- Existing historical pivot procedure execution, real data distributions,
  permissions/metadata visibility in the target deployment, or old-application
  compatibility

The captured SQL tests verify real SQL syntax, constraints, guard behavior and
state transitions. Python result assumptions are checked against SQL, while
storage effects remain synthetic. They are not an end-to-end production run.

## Runtime provenance and reproducibility

The archives were downloaded from official MySQL HTTPS URLs and matched vendor-
published MD5 values. This checks corruption against the official download;
independent GPG signature verification was not performed.

- 8.0.22: https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.22-linux-glibc2.17-x86_64-minimal.tar.xz
  - MD5: `784fcd37010a05d85a18b4b779b52105`
  - mysqld SHA-256: `1a47cf76a1fa8665438a3e89cf1e0ed847ed7e163ea853efd006eec85df3148b`
- 8.0.46: https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-8.0.46-linux-glibc2.17-x86_64-minimal.tar.xz
  - MD5: `d8ec6c48d02e88759eef61c51a6da07d`
  - mysqld SHA-256: `c6e4decadffa6ed9e44f4adb927c42f3bc5acf341774c178a4e8b70d7ae38dc7`

`validation-results.json` records exact runtime strings, all 398 named checks,
source/harness hashes and untested areas. `manifest.json` records audit-input and
migration/generator hashes and every complete SQL chunk's hash/length. Both engine
runs have identical source and harness fingerprints.

Use the commands in `tests/local_student_deletion/README.md` to reproduce with a
local official engine. The operator's delivered SQL requires no Python.
