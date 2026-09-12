"""Examination preview/receipt expiry: read-only report by default.

--apply deletes only expired administrative receipts and import-preview sessions.
It never touches exam attempts, questions, votes, appeals or results. This CLI
does not start Flask, Mongo, workers, or install cron. Prefer --connection-json
for a disposable local rehearsal. Project config is loaded only after arguments.
"""

import argparse
import importlib
import json
from pathlib import Path
import sys
import types

from exams_migrations import connection_parameters


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--connection-json", type=Path)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=12)
    parser.add_argument("--max-seconds", type=int, default=10)
    args = parser.parse_args(argv)
    # Load the domain package without cpm_back/__init__ (legacy blueprint import
    # side effects/configuration are unnecessary for retention).
    name = "_exam_retention_cli_domain"
    package = types.ModuleType(name)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "cpm_back/services/exams")
    ]
    sys.modules[name] = package
    maintenance = importlib.import_module(name + ".maintenance")
    try:
        maintenance._positive(args.batch_size, "batch-size", 1000)
        maintenance._positive(args.max_batches, "max-batches", 100)
        maintenance._positive(args.max_seconds, "max-seconds", 30)
        import mysql.connector

        connection = mysql.connector.connect(
            **connection_parameters(args.connection_json),
            connection_timeout=10,
            autocommit=False
        )
        try:
            report = maintenance.sweep_expired(
                connection,
                batch_size=args.batch_size,
                max_batches=args.max_batches,
                max_seconds=args.max_seconds,
                dry_run=not args.apply,
            )
        finally:
            connection.close()
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except ValueError as error:
        print(
            json.dumps({"success": False, "message": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        # Never reveal database endpoint, credentials, filenames, or raw rows.
        print(
            json.dumps(
                {
                    "success": False,
                    "exceptionType": type(error).__name__,
                    "message": "Retention failed; inspect secured operator logs",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
