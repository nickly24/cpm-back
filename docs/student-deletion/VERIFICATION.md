# Verification — audited-schema revision 2, 2026-10-03

This revision supersedes the earlier simplified-schema migration package. No
production connection, migration, deletion, commit or push was performed.

## Current checks

- Backend exact pinned test environment: **353 discovered, 277 passed, 76 explicitly
  skipped live-service tests**, zero failures. Command:
  `python scripts/run_local_tests.py discover -s tests -v`.
- Homework exact pinned environment: **97/97 passed**, including owner consistency,
  durable receipts, storage failures, late uploads and four pointer-before-delete
  writer regressions.
- Frontend rerun with loopback-only API configuration: **124/124 tests** across
  23 files, production build and TypeScript passed. Frontend sources are unchanged
  from the earlier lint review: global lint has 88 errors/23 warnings reproduced
  exactly on the clean baseline; changed-file lint passed. Global lint is not a pass.
- Both real **Oracle MySQL Community 8.0.22 and 8.0.46**: **1,443 SQL statements and
  398 uniquely named checks passed per engine**. Eight structural SQL-bundle tests
  passed. Detailed source/runtime hashes and test names are in
  `migrations/student-deletion-schema-audit-v2/validation-results.json`.
- The actual metadata-only 79-table DDL is used: 579 columns, 66 FK column rows,
  193 indexes. Preparation produces87 tables/650 columns. Activation has100
  triggers (97 generated plus3 barriers) and23 new guard procedures. The operator
  postcheck additionally requires the4 unchanged original pivot procedures.
- Independent source/module review found no remaining must-fix among the reviewed
  findings. All repository diff whitespace checks pass.

## Corrected coverage

All8 retired chat/realtime/notification/push tables are handled without restoring
those features. Role-qualified ownership preserves colliding staff/student IDs;
known typed event references use durable identifiers and unknown/conflicting
variants fail with aggregate diagnostics. Generic closure checks all actual FKs
and blocks unselected CASCADE/SET NULL/RESTRICT descendants and conflicting
personal parents. Additional no-FK question/file pointers are validated.

Command keys survive as exact sanitized idempotency tombstones, including expiry
maintenance and replay protection. Shared known exam/import receipts are preserved;
unknown mixed personal formats are not generically erased. Actual persisted import
previews include privilegeBaselineStudentId, with immutable-ID precedence, renamed
login and zero-padded-ID tests. Login-only preview persistence is checked using the
current role-qualified account under the same guard transaction, including excluded
and invalid rows; historical aliases only trigger an ambiguity diagnostic.

The real-engine tests cover byte-exact mixed-collation key/UUID handling, retired
ownership guards, cross-student FKs/pointers, post-unlink key adoption, normalized
JSON IDs, actual current homework writer/purge SQL, repeated preparation, invalid
historical-key rejection under non-strict SQL mode and negative schema/body drift.

## Explicit limits

The tests use socket-free engine initialization, isolated Python fixtures and
synthetic object storage. They do not establish concurrent multi-client locking,
live connector/HTTP/MongoDB/S3 behavior, deployed writer drain, provider upload
expiry/retention/versioning, or real data distributions and metadata privileges.
The exact reported server distribution **8.0.22-13** was not tested; upstream
Oracle8.0.22 testing does not certify another vendor build.

The four historical pivot procedures are compared as metadata, never installed
or executed locally. Their intentional absence is the only accepted procedure
mismatch in the isolated fixture; the operator's real postcheck must show zero.
The76 opt-in backend tests were skipped, not passed.

## Delivery and activation

Canonical delivery is complete ordinary SQL under
`migrations/student-deletion-schema-audit-v2/`, with no Python installer required.
Start with `000_preflight_read_only.sql` only. Preparation030/002 and activation031
are distinct approved maintenance phases.031 immediately changes SQL write rules,
even with the feature flag false; old homework code's delete-before-clear sequence
is not compatible. Keep old writers stopped until the corrected code and all
checks are ready. Publishing remains paused pending the user's agreed sequence,
phase results and explicit go-ahead. No real student deletion is authorized here.
