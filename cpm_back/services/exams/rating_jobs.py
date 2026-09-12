"""Existing manual rating jobs, staged publication and cross-worker leases."""

from datetime import timedelta
import threading
import uuid

from .common import transaction, now, dumps
from .rating import period_bounds, capture_exam_input, calculate_from_snapshot


def create_job(date_from, date_to, created_by=None, created_by_name=None):
    period_bounds(date_from, date_to)
    recover()
    with transaction(write=True) as db:
        state = db.one("SELECT * FROM rating_source_state WHERE id=1 FOR UPDATE")
        if db.one(
            "SELECT id FROM rating_recalc_jobs WHERE status IN ('queued','running') LIMIT 1"
        ):
            raise ValueError("Уже выполняется пересчёт рейтинга")
        job_id = db.insert(
            "rating_recalc_jobs",
            {
                "status": "queued",
                "date_from": date_from,
                "date_to": date_to,
                "created_by": created_by,
                "created_by_name": created_by_name,
                "message": "Ожидает запуска",
                "exam_source_revision": state["source_revision"],
                "heartbeat_at": now(),
            },
        )
        db.update("rating_source_state", {"active_job_id": job_id}, "id=1", ())
        from cpm_back.services.exam.rating_recalc_jobs import _serialize_job

        return _serialize_job(
            db.one("SELECT * FROM rating_recalc_jobs WHERE id=%s", (job_id,))
        )


def recover():
    with transaction(write=True) as db:
        state = db.one("SELECT * FROM rating_source_state WHERE id=1 FOR UPDATE")
        # Only expired leases (including never-started queued jobs) may be failed.
        db.execute(
            """UPDATE rating_recalc_jobs SET status='failed',message='Истёк срок ожидания рабочего процесса',
            completed_at=%s WHERE status IN ('queued','running')
            AND COALESCE(heartbeat_at,created_at)<%s""",
            (now(), now() - timedelta(seconds=120)),
        )
        if state and state["active_job_id"]:
            job = db.one(
                "SELECT status FROM rating_recalc_jobs WHERE id=%s",
                (state["active_job_id"],),
            )
            if not job or job["status"] not in ("queued", "running"):
                db.update("rating_source_state", {"active_job_id": None}, "id=1", ())
        db.execute(
            """DELETE s FROM rating_recalc_staging s JOIN rating_recalc_jobs j ON j.id=s.job_id
            WHERE j.status='failed' AND j.completed_at<%s""",
            (now() - timedelta(days=7),),
        )


def claim(job_id):
    token = str(uuid.uuid4())
    with transaction(write=True) as db:
        state = db.one("SELECT * FROM rating_source_state WHERE id=1 FOR UPDATE")
        job = db.one(
            "SELECT * FROM rating_recalc_jobs WHERE id=%s FOR UPDATE", (job_id,)
        )
        if not job or job["status"] != "queued" or state["active_job_id"] != job_id:
            return None
        db.update(
            "rating_recalc_jobs",
            {
                "status": "running",
                "worker_token": token,
                "heartbeat_at": now(),
                "started_at": now(),
                "message": "Пересчёт выполняется; прежний рейтинг остаётся доступным",
            },
            "id=%s",
            (job_id,),
        )
        return dict(job, worker_token=token)


def touch(job_id, token, progress=None):
    with transaction(write=True) as db:
        data = {"heartbeat_at": now()}
        if progress is not None:
            data.update(
                processed_count=progress,
                successful=progress,
                message=f"Рассчитано студентов: {progress}",
            )
        db.update(
            "rating_recalc_jobs",
            data,
            "id=%s AND worker_token=%s AND status='running'",
            (job_id, token),
        )
        if db.cursor.rowcount != 1:
            raise RuntimeError("rating_worker_lease_lost")


def fail_job(job_id, token, code):
    with transaction(write=True) as db:
        state = db.one("SELECT * FROM rating_source_state WHERE id=1 FOR UPDATE")
        db.execute(
            """UPDATE rating_recalc_jobs SET status='failed',failed=1,completed_at=%s,message=%s,errors=%s
            WHERE id=%s AND worker_token=%s AND status='running'""",
            (now(), code, dumps([{"code": code}]), job_id, token),
        )
        if state["active_job_id"] == job_id and db.cursor.rowcount:
            db.update("rating_source_state", {"active_job_id": None}, "id=1", ())


def publish(job, snapshot):
    job_id, token = job["id"], job["worker_token"]
    with transaction(write=True) as db:
        state = db.one("SELECT * FROM rating_source_state WHERE id=1 FOR UPDATE")
        current = db.one(
            "SELECT * FROM rating_recalc_jobs WHERE id=%s FOR UPDATE", (job_id,)
        )
        if (
            state["active_job_id"] != job_id
            or current["worker_token"] != token
            or current["status"] != "running"
        ):
            raise RuntimeError("rating_worker_lease_lost")
        if state["source_revision"] != snapshot["sourceRevision"] or (
            snapshot["nextExamStartAt"] and snapshot["nextExamStartAt"] <= now()
        ):
            raise RuntimeError("rating_source_changed")
        if db.scalar(
            "SELECT COUNT(*) FROM rating_recalc_staging WHERE job_id=%s", (job_id,)
        ) != len(snapshot["students"]):
            raise RuntimeError("rating_staging_incomplete")
        # No delete-then-recreate: stable Allratings.id and details change together.
        # Explicit UPDATE+INSERT also supports the additive rollout before026 UNIQUE.
        db.execute(
            """UPDATE Allratings a JOIN rating_recalc_staging s ON s.student_id=a.student_id AND s.job_id=%s
            SET a.exams=s.exams,a.homework=s.homework,a.tests=s.tests,a.final=s.final,
                a.details_json=s.details_json,a.calculation_job_id=s.job_id""",
            (job_id,),
        )
        db.execute(
            """INSERT INTO Allratings(student_id,exams,homework,tests,final,details_json,calculation_job_id)
            SELECT s.student_id,s.exams,s.homework,s.tests,s.final,s.details_json,s.job_id FROM rating_recalc_staging s
            LEFT JOIN Allratings a ON a.student_id=s.student_id WHERE s.job_id=%s AND a.id IS NULL""",
            (job_id,),
        )
        db.execute(
            """DELETE a FROM Allratings a LEFT JOIN rating_recalc_staging s
            ON s.student_id=a.student_id AND s.job_id=%s WHERE s.student_id IS NULL""",
            (job_id,),
        )
        db.update(
            "rating_source_state",
            {
                "calculated_revision": snapshot["sourceRevision"],
                "date_from": job["date_from"],
                "date_to": job["date_to"],
                "calculated_at": now(),
                "as_of": snapshot["asOf"],
                "next_exam_start_at": snapshot["nextExamStartAt"],
                "active_job_id": None,
            },
            "id=1",
            (),
        )
        db.update(
            "rating_recalc_jobs",
            {
                "status": "completed",
                "completed_at": now(),
                "heartbeat_at": now(),
                "processed_count": len(snapshot["students"]),
                "successful": len(snapshot["students"]),
                "failed": 0,
                "message": "Рейтинг опубликован целиком",
            },
            "id=%s",
            (job_id,),
        )
        db.execute("DELETE FROM rating_recalc_staging WHERE job_id=%s", (job_id,))


def run(job_id):
    job = claim(job_id)
    if not job:
        return
    token = job["worker_token"]
    stop = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stop.wait(15):
            try:
                touch(job_id, token)
            except Exception:
                lost.set()
                return

    worker = threading.Thread(
        target=heartbeat, name=f"rating-lease-{job_id}", daemon=True
    )
    worker.start()
    connection = None
    try:
        with transaction(write=False) as db:
            snapshot = capture_exam_input(db, job["date_from"], job["date_to"])
        with transaction(write=True) as db:
            db.update(
                "rating_recalc_jobs",
                {
                    "exam_source_revision": snapshot["sourceRevision"],
                    "as_of": snapshot["asOf"],
                    "total_students": len(snapshot["students"]),
                },
                "id=%s AND worker_token=%s AND status='running'",
                (job_id, token),
            )
        from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
        from cpm_back.db.mongo import get_mongo_db
        from cpm_back.services.exam.calculate_ratings import (
            calculate_homework_rating,
            calculate_tests_rating,
            calculate_final_rating,
        )

        connection = get_db_connection()
        mongo = get_mongo_db()
        for index, student_id in enumerate(snapshot["students"], 1):
            if lost.is_set():
                raise RuntimeError("rating_worker_lease_lost")
            hw = calculate_homework_rating(
                connection, student_id, job["date_from"], job["date_to"]
            )
            tests = calculate_tests_rating(
                connection, mongo, student_id, job["date_from"], job["date_to"]
            )
            exams = calculate_from_snapshot(snapshot, student_id)
            final = calculate_final_rating(hw, exams, tests)
            connection.rollback()
            document = {
                "student_id": student_id,
                "date_from": str(job["date_from"]),
                "date_to": str(job["date_to"]),
                "calculated_at": iso_utc(snapshot["asOf"]),
                "homework": {"rating": hw["average"], "details": hw["details"]},
                "exams": {"rating": exams["average"], "details": exams["details"]},
                "tests": {
                    "rating": tests["average"],
                    "details": tests["details"],
                    "directions": tests["directions"],
                },
                "final_rating": final,
            }
            with transaction(write=True) as db:
                db.insert(
                    "rating_recalc_staging",
                    {
                        "job_id": job_id,
                        "student_id": student_id,
                        "exams": exams["average"],
                        "homework": hw["average"],
                        "tests": tests["average"],
                        "final": final,
                        "details_json": dumps(document),
                    },
                )
            if index % 3 == 0 or index == len(snapshot["students"]):
                touch(job_id, token, index)
        publish(job, snapshot)
    except Exception as error:
        # Never persist SQL/data-bearing error messages in a user-visible job.
        code = (
            str(error)
            if type(error) is RuntimeError
            and str(error)
            in (
                "rating_worker_lease_lost",
                "rating_source_changed",
                "rating_staging_incomplete",
            )
            else "rating_calculation_failed"
        )
        fail_job(job_id, token, code)
    finally:
        stop.set()
        worker.join(timeout=2)
        if connection:
            from cpm_back.db.mysql_pool import close_db_connection

            close_db_connection(connection)


def iso_utc(value):
    return value.isoformat() + "Z"
