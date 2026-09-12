"""Bounded expiry cleanup, never examination-history retention.

Only expired three import-preview tables and 72h administrative receipts are
eligible. Attempt commands, snapshots, questions, votes, appeals and results are
never deleted here. Expiry comes from server-owned expires_at: editable previews
72h, committed previews7days, admin receipts72h.
"""

import atexit
from datetime import datetime, timezone
import hashlib
import threading
import time

from .common import UnitOfWork, now, iso

IMPORT_TABLES = (
    "outside_exam_result_import_sessions",
    "classic_exam_question_import_sessions",
    "classic_exam_assignment_import_sessions",
)
RECEIPT_TABLE = "exam_admin_commands"
_start_lock = threading.Lock()


def _positive(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from1to{maximum}")
    return value


def _cutoff(value):
    if value is None:
        return now()
    if not isinstance(value, datetime):
        raise ValueError("as_of must be a UTC datetime")
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def sweep_expired(
    connection,
    *,
    batch_size=100,
    max_batches=12,
    max_seconds=10,
    as_of=None,
    dry_run=False,
):
    """Own a dedicated connection's transactions; caller owns/then closes it.

    Bounded by row-batch count and monotonic wall time. Import cleanup locks the
    parent exam before any child row. Busy exams/rows are skipped, not waited for.
    One advisory lock prevents duplicate workers across WSGI processes and CLI.
    """
    _positive(batch_size, "batch_size", 1000)
    _positive(max_batches, "max_batches", 100)
    _positive(max_seconds, "max_seconds", 30)
    cutoff = _cutoff(as_of)
    deadline = time.monotonic() + max_seconds
    result = {
        "dryRun": bool(dry_run),
        "asOf": iso(cutoff),
        "lockAcquired": False,
        "batches": 0,
        "deleted": {table: 0 for table in IMPORT_TABLES + (RECEIPT_TABLE,)},
        "skippedBusyExams": 0,
        "budgetExhausted": False,
    }
    db = UnitOfWork(connection)
    acquired = False
    lock_name = None
    try:
        # No app/global initialization; the caller passed a dedicated connection.
        connection.rollback()
        db.execute("SET SESSION time_zone='+00:00'")
        db.execute("SET SESSION lock_wait_timeout=2")
        db.execute("SET SESSION innodb_lock_wait_timeout=2")
        db.execute("SET SESSION MAX_EXECUTION_TIME=5000")
        schema = str(db.scalar("SELECT DATABASE()", default=""))
        lock_name = "exam-retention:" + hashlib.sha256(schema.encode()).hexdigest()[:32]
        if dry_run:
            connection.start_transaction(
                isolation_level="READ COMMITTED", readonly=True
            )
            result["expired"] = {
                table: db.scalar(
                    f"SELECT COUNT(*) FROM {table} WHERE expires_at<=%s", (cutoff,)
                )
                for table in IMPORT_TABLES + (RECEIPT_TABLE,)
            }
            connection.rollback()
            return result
        acquired = db.scalar("SELECT GET_LOCK(%s,0)", (lock_name,)) == 1
        result["lockAcquired"] = acquired
        if not acquired:
            return result
        connection.commit()

        def budget_available():
            return result["batches"] < max_batches and time.monotonic() < deadline

        for table in IMPORT_TABLES:
            while budget_available():
                # Discovery does not lock children and is committed before the
                # parent lock, so no pre-wait consistent snapshot survives.
                connection.start_transaction(isolation_level="READ COMMITTED")
                candidates = db.all(
                    f"""SELECT id,exam_id FROM {table}
                    WHERE expires_at<=%s ORDER BY expires_at,id LIMIT %s""",
                    (cutoff, batch_size),
                )
                connection.commit()
                if not candidates:
                    break
                result["batches"] += 1
                grouped = {}
                for row in candidates:
                    grouped.setdefault(row["exam_id"], []).append(row["id"])
                removed = 0
                for exam_id in sorted(grouped):
                    if time.monotonic() >= deadline:
                        break
                    connection.start_transaction(isolation_level="READ COMMITTED")
                    exam = db.one(
                        "SELECT id FROM exams WHERE id=%s FOR UPDATE SKIP LOCKED",
                        (exam_id,),
                    )
                    if not exam:
                        connection.rollback()
                        result["skippedBusyExams"] += 1
                        continue
                    ids = grouped[exam_id]
                    placeholders = ",".join(["%s"] * len(ids))
                    locked = db.all(
                        f"""SELECT id FROM {table} WHERE exam_id=%s
                        AND expires_at<=%s AND id IN ({placeholders}) ORDER BY id
                        FOR UPDATE SKIP LOCKED""",
                        [exam_id, cutoff] + ids,
                    )
                    if locked:
                        selected = [row["id"] for row in locked]
                        db.execute(
                            f"DELETE FROM {table} WHERE expires_at<=%s AND id IN ("
                            + ",".join(["%s"] * len(selected))
                            + ")",
                            [cutoff] + selected,
                        )
                        removed += db.cursor.rowcount
                    connection.commit()
                result["deleted"][table] += removed
                if not removed or len(candidates) < batch_size:
                    break

        while budget_available():
            connection.start_transaction(isolation_level="READ COMMITTED")
            selected = db.all(
                f"""SELECT id FROM {RECEIPT_TABLE} WHERE expires_at<=%s
                ORDER BY expires_at,id LIMIT %s FOR UPDATE SKIP LOCKED""",
                (cutoff, batch_size),
            )
            if not selected:
                connection.rollback()
                break
            result["batches"] += 1
            ids = [row["id"] for row in selected]
            db.execute(
                f"DELETE FROM {RECEIPT_TABLE} WHERE expires_at<=%s AND id IN ("
                + ",".join(["%s"] * len(ids))
                + ")",
                [cutoff] + ids,
            )
            result["deleted"][RECEIPT_TABLE] += db.cursor.rowcount
            connection.commit()
            if len(selected) < batch_size:
                break
        result["budgetExhausted"] = not budget_available()
        return result
    finally:
        connection.rollback()
        if acquired:
            db.scalar("SELECT RELEASE_LOCK(%s)", (lock_name,))
        db.cursor.close()


def start_retention_worker(
    app, *, interval_seconds=900, batch_size=100, max_batches=12
):
    """Start once for this app after pools initialize; disabled means no I/O.

    Returns an inspectable handle in app.extensions['exam_retention_worker'].
    No cron/automation is installed; the bounded worker belongs to the backend.
    """
    if not app.config.get("EXAMS_V2_ENABLED", False):
        return None
    _positive(interval_seconds, "interval_seconds", 86400)
    _positive(batch_size, "batch_size", 1000)
    _positive(max_batches, "max_batches", 100)
    with _start_lock:
        existing = app.extensions.get("exam_retention_worker")
        if existing and existing["thread"].is_alive():
            return existing
        stop_event = threading.Event()

        def run():
            from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

            while not stop_event.is_set():
                if app.config.get("EXAMS_V2_ENABLED", False):
                    connection = None
                    try:
                        connection = get_db_connection()
                        report = sweep_expired(
                            connection, batch_size=batch_size, max_batches=max_batches
                        )
                        deleted = sum(report["deleted"].values())
                        if deleted:
                            app.logger.info(
                                "exam retention deleted=%s batches=%s",
                                deleted,
                                report["batches"],
                            )
                    except Exception as error:
                        app.logger.error(
                            "exam retention failed exception=%s", type(error).__name__
                        )
                    finally:
                        if connection is not None:
                            close_db_connection(connection)
                stop_event.wait(interval_seconds)

        thread = threading.Thread(target=run, name="exam-retention", daemon=True)
        handle = {"thread": thread, "stop": stop_event}
        app.extensions["exam_retention_worker"] = handle
        atexit.register(stop_event.set)
        thread.start()
        return handle


def stop_retention_worker(app):
    handle = app.extensions.get("exam_retention_worker")
    if handle:
        handle["stop"].set()
    return handle
