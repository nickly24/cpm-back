"""Journalled MySQL8 classic-exam migrations; read-only check is the default.

--plan is completely offline. --apply requires --backup-verified. The additive
phase never deletes business data, coerces grades, adds legacy user restrictions,
or backfills directions. The hardening phase additionally requires an explicit
reviewed direction map and --legacy-compatible-code. DDL is NOT transactional:
every statement has a durable started/done journal and verified postcondition.

This tool does not run old migrations, restore backups, start Flask, touch Mongo,
or clean up invalid data. A rehearsal connection must be supplied explicitly via
--connection-json; without it the project-configured database is selected.
"""

import argparse
import hashlib
import json
import re
import runpy
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
JOURNAL = "exam_schema_migration_steps"
REQUIRED_BASE_TABLES = (
    "exams",
    "exam_sessions",
    "directions",
    "students",
    "examinators",
    "rating_recalc_jobs",
    "Allratings",
)
IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z_0-9]*$")


class MigrationError(RuntimeError):
    """A safe actionable migration refusal; never contains credentials/data rows."""


@dataclass(frozen=True)
class Step:
    migration: str
    file_checksum: str
    metadata: dict
    sql: str

    @property
    def id(self):
        return self.metadata["id"]

    @property
    def checksum(self):
        return hashlib.sha256(
            (json.dumps(self.metadata, sort_keys=True) + "\n" + self.sql).encode()
        ).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise MigrationError("Invalid migration-owned SQL identifier")
    return "`" + value + "`"


def load_migrations(phase="additive", directory=MIGRATIONS):
    """Parse trusted reviewed files, not arbitrary user-provided SQL."""
    result = []
    for path in sorted(Path(directory).glob("*.sql")):
        number = int(path.name.split("_", 1)[0])
        if (
            not 15 <= number <= 28
            or (phase == "additive" and number in (26, 27))
            or (phase == "hardening" and number not in (26, 27))
        ):
            continue
        content = path.read_text(encoding="utf-8")
        file_checksum = hashlib.sha256(content.encode()).hexdigest()
        chunks = re.split(r"^-- @step (.+)\n", content, flags=re.MULTILINE)
        if len(chunks) < 3:
            raise MigrationError("Migration has no journalled steps: " + path.name)
        seen = set()
        for index in range(1, len(chunks), 2):
            metadata = json.loads(chunks[index])
            sql = chunks[index + 1].strip()
            if not sql.endswith(";") or ";" in sql[:-1]:
                raise MigrationError(
                    "Each step must contain exactly one SQL statement: " + path.name
                )
            if metadata["id"] in seen:
                raise MigrationError("Duplicate step ID: " + path.name)
            seen.add(metadata["id"])
            identifier(metadata["table"])
            if metadata["kind"] not in (
                "create",
                "add_columns",
                "modify_columns",
                "indexes",
                "constraints",
                "seed",
            ):
                raise MigrationError("Unknown migration step kind")
            if re.search(
                r"\b(DROP|TRUNCATE|DELETE|REPLACE)\s+(?:TABLE|FROM|INTO)", sql, re.I
            ):
                raise MigrationError(
                    "Destructive data/schema operations are not supported"
                )
            result.append(Step(path.name, file_checksum, metadata, sql[:-1]))
    expected = 12 if phase == "additive" else 2 if phase == "hardening" else 14
    if len({step.migration for step in result}) != expected:
        raise MigrationError("Migration package is incomplete")
    return result


def rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def scalar(cursor, sql, params=()):
    data = rows(cursor, sql, params)
    if len(data) != 1 or len(data[0]) != 1:
        raise MigrationError("Guard query must return exactly one scalar")
    return next(iter(data[0].values()))


def table_exists(cursor, table):
    return bool(
        scalar(
            cursor,
            "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
            (table,),
        )
    )


def actual_columns(cursor, table):
    return {
        row["COLUMN_NAME"]: row
        for row in rows(
            cursor,
            """
        SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,CHARACTER_SET_NAME,COLLATION_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
    """,
            (table,),
        )
    }


def normalize_type(value):
    # MySQL8 may display historical integer widths on untouched legacy columns.
    return re.sub(r"\b(bigint|int|smallint|tinyint)\(\d+\)", r"\1", value.lower())


def columns_match(actual, expected):
    def charset(value):
        return "utf8mb3" if value == "utf8" else value

    return all(
        name in actual
        and normalize_type(actual[name]["COLUMN_TYPE"]) == normalize_type(want["type"])
        and (actual[name]["IS_NULLABLE"] == "YES") == want["nullable"]
        and (
            "charset" not in want
            or charset(actual[name]["CHARACTER_SET_NAME"]) == charset(want["charset"])
        )
        and (
            "collation" not in want
            or actual[name]["COLLATION_NAME"] == want["collation"]
        )
        for name, want in expected.items()
    )


def declared_indexes(sql):
    result = {}
    for match in re.finditer(r"\b(UNIQUE\s+)?KEY\s+(\w+)\s*\(([^)]+)\)", sql, re.I):
        result[match[2]] = (
            not bool(match[1]),
            [item.strip().strip("`") for item in match[3].split(",")],
        )
    composite = re.search(r"\bPRIMARY KEY\s*\(([^)]+)\)", sql, re.I)
    if composite:
        result["PRIMARY"] = (
            False,
            [item.strip().strip("`") for item in composite[1].split(",")],
        )
    else:
        inline = re.search(
            r"\b(\w+)\s+(?:BIGINT|INT|TINYINT)[^,\n]*\bPRIMARY KEY", sql, re.I
        )
        if inline:
            result["PRIMARY"] = (False, [inline[1]])
    return result


def indexes_match(cursor, step):
    wanted = declared_indexes(step.sql)
    actual = {}
    for row in rows(
        cursor,
        """
        SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX
    """,
        (step.metadata["table"],),
    ):
        actual.setdefault(row["INDEX_NAME"], (bool(row["NON_UNIQUE"]), []))[1].append(
            row["COLUMN_NAME"]
        )
    return all(actual.get(name) == details for name, details in wanted.items())


def constraints_match(cursor, step):
    table = step.metadata["table"]
    actual = {
        row["CONSTRAINT_NAME"]: row["CONSTRAINT_TYPE"]
        for row in rows(
            cursor,
            """
        SELECT CONSTRAINT_NAME,CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s
    """,
            (table,),
        )
    }
    if not set(step.metadata.get("constraints", ())).issubset(actual):
        return False
    for match in re.finditer(
        r"CONSTRAINT\s+(\w+)\s+FOREIGN KEY\s*\((\w+)\)\s+REFERENCES\s+(\w+)\s*\((\w+)\)\s+ON DELETE\s+(CASCADE|RESTRICT|SET NULL)",
        step.sql,
        re.I,
    ):
        name, column, target, target_column, delete_rule = match.groups()
        found = rows(
            cursor,
            """
            SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.DELETE_RULE
            FROM information_schema.KEY_COLUMN_USAGE k
            JOIN information_schema.REFERENTIAL_CONSTRAINTS r
                ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME
            WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME=%s AND k.CONSTRAINT_NAME=%s
        """,
            (table, name),
        )
        if found != [
            {
                "COLUMN_NAME": column,
                "REFERENCED_TABLE_NAME": target,
                "REFERENCED_COLUMN_NAME": target_column,
                "DELETE_RULE": delete_rule.upper(),
            }
        ]:
            return False
    return True


def postcondition(cursor, step):
    meta = step.metadata
    if not table_exists(cursor, meta["table"]):
        return False
    if meta["kind"] == "seed":
        return bool(scalar(cursor, meta["post_sql"]))
    if not columns_match(
        actual_columns(cursor, meta["table"]), meta.get("columns", {})
    ):
        return False
    if meta["kind"] == "create":
        engine = scalar(
            cursor,
            "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
            (meta["table"],),
        )
        if engine != "InnoDB":
            return False
        actual = actual_columns(cursor, meta["table"])
        for name, want in meta.get("columns", {}).items():
            if (
                want["type"].startswith(("varchar", "char", "text"))
                and actual[name]["CHARACTER_SET_NAME"] != "utf8mb4"
            ):
                return False
    return indexes_match(cursor, step) and constraints_match(cursor, step)


def precondition(cursor, step):
    meta = step.metadata
    exists = table_exists(cursor, meta["table"])
    if meta["kind"] == "create":
        return not exists
    if not exists:
        return False
    actual = actual_columns(cursor, meta["table"])
    if meta["kind"] == "add_columns":
        return not (set(actual) & set(meta["columns"]))
    if meta["kind"] == "modify_columns":
        return columns_match(actual, meta["before"])
    if meta["kind"] == "indexes":
        existing = {
            row["INDEX_NAME"]
            for row in rows(
                cursor,
                "SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                (meta["table"],),
            )
        }
        return not existing.intersection(meta["indexes"])
    if meta["kind"] == "constraints":
        existing = {
            row["CONSTRAINT_NAME"]
            for row in rows(
                cursor,
                "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                (meta["table"],),
            )
        }
        return not existing.intersection(meta["constraints"])
    return (
        meta["kind"] == "seed"
        and scalar(cursor, "SELECT COUNT(*) FROM " + identifier(meta["table"])) == 0
    )


def verify_server(cursor):
    version = str(scalar(cursor, "SELECT VERSION()"))
    parsed = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if (
        "mariadb" in version.lower()
        or not parsed
        or tuple(map(int, parsed.groups())) < (8, 0, 22)
    ):
        raise MigrationError("Rehearsed MySQL8.0.22 or newer is required")
    for table in REQUIRED_BASE_TABLES:
        if not table_exists(cursor, table):
            raise MigrationError("Required legacy table missing: " + table)
        if not columns_match(
            actual_columns(cursor, table), {"id": {"type": "int", "nullable": False}}
        ):
            raise MigrationError("Legacy PK must be signed INT: " + table)
        if (
            scalar(
                cursor,
                "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                (table,),
            )
            != "InnoDB"
        ):
            raise MigrationError("Legacy table must use InnoDB: " + table)
    return version


def bootstrap_journal(connection, cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version VARCHAR(128) NOT NULL PRIMARY KEY,
        checksum CHAR(64) NOT NULL,
        applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS exam_schema_migration_steps (
        migration VARCHAR(128) NOT NULL,
        step_id VARCHAR(128) NOT NULL,
        file_checksum CHAR(64) NOT NULL,
        step_checksum CHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        completed_at DATETIME(6) NULL,
        PRIMARY KEY(migration,step_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    expected = {"version", "checksum", "applied_at"}
    if not expected.issubset(actual_columns(cursor, "schema_migrations")):
        raise MigrationError("Existing schema_migrations has incompatible structure")
    expected = {
        "migration",
        "step_id",
        "file_checksum",
        "step_checksum",
        "status",
        "started_at",
        "completed_at",
    }
    if not expected.issubset(actual_columns(cursor, JOURNAL)):
        raise MigrationError("Existing migration journal has incompatible structure")
    connection.commit()


def validate_journal(cursor, all_steps):
    by_file = {step.migration: step.file_checksum for step in all_steps}
    by_step = {(step.migration, step.id): step for step in all_steps}
    complete, journal = {}, {}
    if table_exists(cursor, "schema_migrations"):
        complete = {
            row["version"]: row["checksum"]
            for row in rows(cursor, "SELECT version,checksum FROM schema_migrations")
            if row["version"] in by_file
        }
        if any(by_file[name] != digest for name, digest in complete.items()):
            raise MigrationError(
                "Previously applied migration was edited; restore its checksum"
            )
    if table_exists(cursor, JOURNAL):
        journal = {
            (row["migration"], row["step_id"]): row
            for row in rows(cursor, "SELECT * FROM exam_schema_migration_steps")
        }
        for key, row in journal.items():
            if (
                key not in by_step
                or row["file_checksum"] != by_step[key].file_checksum
                or row["step_checksum"] != by_step[key].checksum
            ):
                raise MigrationError("Migration journal checksum mismatch")
            if row["status"] not in ("started", "done"):
                raise MigrationError("Unrecognized migration journal status")
    return complete, journal


def load_direction_map(path):
    if path is None:
        raise MigrationError(
            "Hardening requires --direction-map with explicitly reviewed IDs"
        )
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(data) != {"examDirectionMap"} or not isinstance(
        data["examDirectionMap"], dict
    ):
        raise MigrationError("Map format is {examDirectionMap: {examId: directionId}}")
    result = {}
    for key, value in data["examDirectionMap"].items():
        if (
            not isinstance(key, str)
            or not key.isdigit()
            or str(int(key)) != key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or int(key) <= 0
        ):
            raise MigrationError("Direction map IDs must be positive integers")
        result[int(key)] = value
    return result


def apply_direction_map(connection, cursor, mapping, apply=False):
    """Explicit idempotent backfill, never infer/rename directions or overwrite IDs."""
    cursor.execute(
        "SELECT id,direction_id FROM exams ORDER BY id"
        + (" FOR UPDATE" if apply else "")
    )
    current = {row["id"]: row["direction_id"] for row in cursor.fetchall()}
    if not set(mapping).issubset(current):
        raise MigrationError("Direction map contains absent exam IDs")
    missing = {exam for exam, direction in current.items() if direction is None}
    if not missing.issubset(mapping):
        raise MigrationError("Direction map does not cover every unlinked legacy exam")
    directions = {row["id"] for row in rows(cursor, "SELECT id FROM directions")}
    if not set(mapping.values()).issubset(directions):
        raise MigrationError("Direction map refers to absent direction IDs")
    if any(
        current[exam] not in (None, direction) for exam, direction in mapping.items()
    ):
        raise MigrationError("Direction map would overwrite an already linked exam")
    changed = 0
    if apply:
        for exam, direction in sorted(mapping.items()):
            if current[exam] is None:
                cursor.execute(
                    "UPDATE exams SET direction_id=%s WHERE id=%s AND direction_id IS NULL",
                    (direction, exam),
                )
                changed += cursor.rowcount
        # Direction backfill affects the authoritative exam source, exactly once.
        if changed and table_exists(cursor, "rating_source_state"):
            cursor.execute(
                "UPDATE rating_source_state SET source_revision=source_revision+1 WHERE id=1"
            )
        connection.commit()
    return changed


def verify_legacy_values(cursor):
    for row in rows(cursor, "SELECT CAST(val AS CHAR) AS points FROM exam_sessions"):
        value = Decimal(row["points"])
        if (
            not value.is_finite()
            or not Decimal(0) <= value <= Decimal("9999999999.99")
            or value != value.quantize(Decimal("0.01"))
        ):
            raise MigrationError(
                "Legacy points cannot be represented exactly by DECIMAL(12,2)"
            )


def run_migrations(connection, phase="additive", apply=False, direction_map=None):
    all_steps = load_migrations("all")
    steps = load_migrations(phase)
    cursor = connection.cursor(dictionary=True)
    acquired = False
    report = {
        "mode": "apply" if apply else "check",
        "phase": phase,
        "steps": [],
        "businessRowsDeleted": 0,
    }
    try:
        cursor.execute("SET SESSION time_zone='+00:00'")
        cursor.execute(
            "SET SESSION sql_mode='STRICT_ALL_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'"
        )
        cursor.execute("SET SESSION lock_wait_timeout=10")
        cursor.execute("SET SESSION innodb_lock_wait_timeout=10")
        report["serverVersion"] = verify_server(cursor)
        schema = str(scalar(cursor, "SELECT DATABASE()"))
        lock_name = (
            "classic-exams-migration:"
            + hashlib.sha256(schema.encode()).hexdigest()[:32]
        )
        if apply:
            acquired = scalar(cursor, "SELECT GET_LOCK(%s,0)", (lock_name,)) == 1
            if not acquired:
                raise MigrationError("Another exam migrator holds the database lock")
            bootstrap_journal(connection, cursor)
        complete, journal = validate_journal(cursor, all_steps)
        if phase in ("hardening", "all"):
            if phase == "hardening" and not all(
                step.migration in complete
                for step in all_steps
                if int(step.migration[:3]) < 26
            ):
                raise MigrationError(
                    "Apply and verify every additive migration before hardening"
                )
            if direction_map is None:
                raise MigrationError("Hardening requires a reviewed direction map")
        hardening_prepared = False
        for step in steps:
            if step.migration.startswith("026_") and not hardening_prepared:
                verify_legacy_values(cursor)
                report["directionsLinked"] = apply_direction_map(
                    connection, cursor, direction_map, apply
                )
                hardening_prepared = True
            known = journal.get((step.migration, step.id))
            ready = postcondition(cursor, step)
            # Completed CREATE/ADD steps may be intentionally superseded by the
            # hardening MODIFYs; check their cumulative final target instead.
            if known and known["status"] == "done" and not ready:
                ready = superseded_postcondition(cursor, step, all_steps, journal)
            if ready:
                if not known and step.migration not in complete:
                    raise MigrationError(
                        "Unjournalled pre-existing target; manual schema review required: "
                        + step.id
                    )
                if apply and known and known["status"] == "started":
                    cursor.execute(
                        "UPDATE exam_schema_migration_steps SET status='done',completed_at=CURRENT_TIMESTAMP(6) WHERE migration=%s AND step_id=%s",
                        (step.migration, step.id),
                    )
                    connection.commit()
                report["steps"].append(
                    {"migration": step.migration, "step": step.id, "status": "verified"}
                )
                continue
            if known and known["status"] == "done":
                raise MigrationError(
                    "Applied step no longer satisfies its schema contract: " + step.id
                )
            if not precondition(cursor, step):
                if (
                    not apply
                    and not known
                    and not table_exists(cursor, step.metadata["table"])
                ):
                    report["steps"].append(
                        {
                            "migration": step.migration,
                            "step": step.id,
                            "status": "pending_dependency",
                        }
                    )
                    continue
                raise MigrationError(
                    "Unsafe partial schema or unexpected precondition: " + step.id
                )
            for requirement in step.metadata.get("requires", ()):
                if scalar(cursor, requirement) != 0:
                    raise MigrationError(
                        "Data precondition failed; no cleanup/coercion permitted: "
                        + step.id
                    )
            if not apply:
                report["steps"].append(
                    {"migration": step.migration, "step": step.id, "status": "pending"}
                )
                continue
            if not known:
                cursor.execute(
                    """INSERT INTO exam_schema_migration_steps
                    (migration,step_id,file_checksum,step_checksum,status)
                    VALUES(%s,%s,%s,%s,'started')""",
                    (step.migration, step.id, step.file_checksum, step.checksum),
                )
                connection.commit()
            cursor.execute(step.sql)
            connection.commit()
            if not postcondition(cursor, step):
                raise MigrationError(
                    "DDL ran but postcondition failed; leave journal started: "
                    + step.id
                )
            cursor.execute(
                "UPDATE exam_schema_migration_steps SET status='done',completed_at=CURRENT_TIMESTAMP(6) WHERE migration=%s AND step_id=%s",
                (step.migration, step.id),
            )
            connection.commit()
            report["steps"].append(
                {"migration": step.migration, "step": step.id, "status": "applied"}
            )
        if apply:
            for migration, digest in sorted(
                {step.migration: step.file_checksum for step in steps}.items()
            ):
                cursor.execute(
                    "INSERT INTO schema_migrations(version,checksum) VALUES(%s,%s) ON DUPLICATE KEY UPDATE checksum=VALUES(checksum)",
                    (migration, digest),
                )
            connection.commit()
        report["fullyApplied"] = all(
            item["status"] in ("verified", "applied") for item in report["steps"]
        )
        return report
    finally:
        connection.rollback()
        if acquired:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            cursor.fetchall()
        cursor.close()


def superseded_postcondition(cursor, step, all_steps, journal):
    """Respect only journalled later MODIFYs, not arbitrary production drift."""
    adjusted = dict(step.metadata)
    adjusted["columns"] = dict(step.metadata.get("columns", {}))
    for later in all_steps:
        if (
            later.migration <= step.migration
            or later.metadata["table"] != step.metadata["table"]
            or later.metadata["kind"] != "modify_columns"
        ):
            continue
        done = journal.get((later.migration, later.id), {}).get("status") == "done"
        if done:
            for column, specification in later.metadata["columns"].items():
                if column in adjusted["columns"]:
                    adjusted["columns"][column] = specification
    return postcondition(
        cursor, Step(step.migration, step.file_checksum, adjusted, step.sql)
    )


def connection_parameters(path=None):
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        required = {"host", "port", "user", "password", "database"}
        if set(data) != required:
            raise MigrationError(
                "Connection JSON requires exactly host,port,user,password,database"
            )
        return data
    config = runpy.run_path(str(ROOT / "cpm_back/config.py"))["config"]
    return {
        "host": config.MYSQL_HOST,
        "port": config.MYSQL_PORT,
        "user": config.MYSQL_USER,
        "password": config.MYSQL_PASSWORD,
        "database": config.MYSQL_DATABASE,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("additive", "hardening", "all"), default="additive"
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Offline file/checksum validation; no database connection",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup-verified",
        action="store_true",
        help="Operator attests a restorable backup exists",
    )
    parser.add_argument(
        "--legacy-compatible-code",
        action="store_true",
        help="Operator attests guarded legacy/account-delete code is deployed",
    )
    parser.add_argument("--direction-map", type=Path)
    parser.add_argument(
        "--connection-json",
        type=Path,
        help="Explicit test/rehearsal connection; never printed",
    )
    args = parser.parse_args(argv)
    try:
        steps = load_migrations(args.phase)
        if args.plan:
            print(
                json.dumps(
                    {
                        "mode": "offline_plan",
                        "phase": args.phase,
                        "databaseConnected": False,
                        "migrations": {
                            step.migration: step.file_checksum for step in steps
                        },
                        "stepCount": len(steps),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.apply and not args.backup_verified:
            raise MigrationError(
                "Writes require --backup-verified after a restorable backup is verified"
            )
        if args.apply and args.phase != "additive" and not args.legacy_compatible_code:
            raise MigrationError("Hardening requires --legacy-compatible-code")
        mapping = (
            load_direction_map(args.direction_map) if args.phase != "additive" else None
        )
        import mysql.connector

        connection = mysql.connector.connect(
            **connection_parameters(args.connection_json),
            autocommit=False,
            connection_timeout=10
        )
        try:
            report = run_migrations(connection, args.phase, args.apply, mapping)
        finally:
            connection.close()
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except MigrationError as error:
        print(
            json.dumps({"success": False, "message": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        # Connector errors can contain server/user addresses: print only class.
        print(
            json.dumps(
                {
                    "success": False,
                    "message": "Migration failed; investigate secured operator logs",
                    "exceptionType": type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
