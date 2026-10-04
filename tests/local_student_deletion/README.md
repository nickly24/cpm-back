# Socket-free MySQL 8 deletion verification

This opt-in harness executes synthetic fixtures through the real MySQL engine using
`mysqld --no-defaults --initialize-insecure --init-file=...`. Initialization exits
without running a database server. It does not import either application's config,
connect to a database, open a listening socket, start workers, or use real student
or storage data. Every run gets a new disposable data directory outside the repo.

## What it checks

- The checked-in backend migrations 030/031 and homework migrations 001/002; the
  additive backend/homework migrations are applied twice.
- All current dynamically generated SQL write guards, including JSON journals.
- Rejection of direct, indirect and multi-hop writes, old/new ownership changes,
  deleted-ID recreation, and immutable deletion barriers.
- Numeric/string ownership IDs in nested JSON, all supported singular and array
  keys, old snapshot reintroduction, and allowed NEW-only/atomic redaction.
- Actual persisted trigger/routine definitions match the readiness checker.
- The actual `enqueue_delete` function's generated SQL: outbox generation replay,
  active-lease preservation, retained receipts and idempotent queued requests.
- UTC barrier/job/purge timestamps and outbox deadlines with session time zone
  `+03:00`. Coordinator INSERT templates are extracted from current source.

The surrounding legacy/domain table shapes are intentionally minimal synthetic
fixtures. The homework/deletion migrations and generated trigger bodies are used
unchanged. Application code is not exercised through a live database client.
This is **not** proof of concurrent locking, a full production schema, HTTP API,
MongoDB or S3/provider integration. Run the opt-in live-service suites separately
in an authorized, isolated environment before enabling deletion.

## Reproduce

Use paired `cpm-back` and `cpm-homework-service` checkouts. Defaults expect them to
be sibling directories. Set `CPM_TEST_HOMEWORK_REPO` to another explicit checkout
path if needed. No service config or credentials are needed.

Install the backend test dependencies in a virtual environment using the repo's
`requirements.txt` and `requirements-test.txt`. The engine harness itself only
needs Python and Flask to load the standalone fence module.

Provide a locally installed official MySQL 8.0.46 distribution using
`CPM_TEST_MYSQL_BASE_DIR`. The verified Linux x86_64 minimal distribution was:

- Official download page: https://dev.mysql.com/downloads/mysql/8.0.html?os=2
- Official archive: https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-8.0.46-linux-glibc2.17-x86_64-minimal.tar.xz
- Published MD5: `d8ec6c48d02e88759eef61c51a6da07d`
- Checksum/signature guidance: https://dev.mysql.com/doc/refman/8.0/en/verifying-package-integrity.html
- Initialization reference: https://dev.mysql.com/doc/refman/8.0/en/data-directory-initialization.html

The MD5 checks corruption against the vendor's published value; use the vendor's
signature verification when authentication beyond the HTTPS vendor download is
required. No binaries are included or automatically downloaded/executed by this
harness.

Example local preparation and run, from the backend checkout:

```sh
R="$(mktemp -d /tmp/cpm-mysql-runtime.XXXXXX)"
curl -fL 'https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-8.0.46-linux-glibc2.17-x86_64-minimal.tar.xz' -o "$R/mysql.tar.xz"
printf 'd8ec6c48d02e88759eef61c51a6da07d  %s\n' "$R/mysql.tar.xz" | md5sum -c -
tar -xJf "$R/mysql.tar.xz" -C "$R"
export CPM_TEST_MYSQL_BASE_DIR="$R/mysql-8.0.46-linux-glibc2.17-x86_64-minimal"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/local_student_deletion/verify_mysql.py
```

If `ldd "$CPM_TEST_MYSQL_BASE_DIR/bin/mysqld"` reports `libaio.so.1` missing,
install that dependency through your operating system's official repository, or
extract an official compatible package into this temporary runtime directory.
For the Debian x86_64 environment used during implementation:

```sh
curl -fL 'https://deb.debian.org/debian/pool/main/liba/libaio/libaio1_0.3.113-4_amd64.deb' -o "$R/libaio.deb"
dpkg-deb -x "$R/libaio.deb" "$R/libaio"
export LD_LIBRARY_PATH="$R/libaio/usr/lib/x86_64-linux-gnu:$R/libaio/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/local_student_deletion/verify_mysql.py
```

The harness leaves a report, exact executed SQL, engine log, metadata exports and
statement-success markers under a unique directory in
`$CPM_SQL_TEST_OUTPUT` (default: the system temporary directory under
`cpm-student-deletion-sql`). The report includes source SHA-256 digests. It fails
if the engine returns an error, any statement lacks its success marker, any
assertion fails, or stored metadata differs. Logs/data directories are never
written into the checkout. Do not commit generated data, binaries or logs.

Each run needs roughly 150–200 MiB of temporary space. Remove only the disposable
runtime/results you created when finished; the harness does not remove existing
files or databases.

## Actual audited schema and literal SQL bundle (v2)

`verify_actual_schema.py` constructs all **79** reviewed CREATE TABLE statements
from the metadata-only audit, in FK dependency order without disabling foreign
keys. It verifies the 579 original columns, 66 FK column rows and 193 indexes,
then applies the complete literal preparation twice and the literal activation.
This supplements the earlier deliberately minimal `verify_mysql.py` fixture.

Only the supplied InnoDB CREATE TABLE shapes are admitted. Historical Python and
SQL files, and the four original dynamic pivot procedures, are never installed or
executed. They are hashed/compared as audit data only. The operator preflight and
postcheck are executed against the isolated schema too; their sole expected
nonzero result is four absent historical procedures. All other drift and ownership
counts must be zero. The actual persisted trigger/routine definitions are also
fed to the application's readiness checker; altered role-literal case must fail.

The actual-schema suite covers role-qualified collisions, composite PKs, no-FK
orphans, all six typed realtime events, padded/string/numeric JSON ownership,
command tombstone sanitation/replay denial, old/new file and classic-exam pointer
ownership, exact-key adoption after unlink and after ledger removal, and unrelated
student/shared-parent preservation. Current purge functions are AST-loaded without
app/config; their captured SQL and result assumptions are executed/asserted in the
engine. Storage is a synthetic in-memory stub, never an S3 client. This exercises
the actual generated purge SQL, not a live mysql connector or provider integration.

Both official **Oracle MySQL Community 8.0.22 and 8.0.46** are supported by the
harness. 8.0.22 uses `--innodb-log-file-size=8M`; 8.0.46 uses
`--innodb-redo-log-capacity=16M`. Neither is a claim of matching the reported
`8.0.22-13` vendor/build. For the 8.0.22 official archive:

- Archive page: https://downloads.mysql.com/archives/community/?version=8.0.22&os=2
- Download: https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.22-linux-glibc2.17-x86_64-minimal.tar.xz
- Published MD5: `784fcd37010a05d85a18b4b779b52105`

Run from the backend checkout with Flask installed and a verified local engine:

```sh
export CPM_TEST_MYSQL_BASE_DIR=/path/to/official/mysql-8.0.22-linux-glibc2.17-x86_64-minimal
python tests/local_student_deletion/verify_actual_schema.py --audit /path/to/reviewed-audit
python -m pytest -q tests/local_student_deletion/test_sql_bundle.py
```

`--compile-only` is for capturing new post-migration metadata before a regeneration;
it does not claim the synthetic suite passed. Regenerate with the successful
engine's `out/baseline_metadata.json` and `out/post_metadata.json` only after
reviewing any changed definitions:

```sh
python scripts/generate_student_deletion_sql.py --audit /path/to/reviewed-audit \
  --baseline-metadata /path/to/run/out/baseline_metadata.json \
  --post-metadata /path/to/run/out/post_metadata.json
```

Then rerun the full suite on both engines against frozen source hashes. The
standalone user bundle is `migrations/student-deletion-schema-audit-v2/`; its SQL
files require no Python. `user-sql/` contains complete numbered chat-sized parts.
See its `VALIDATION.md` for the final source-specific results and explicit limits.
