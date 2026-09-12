"""Rating publication tests on uniquely created disposable local MySQL schemas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import os
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
from tests import test_exams_migrations as migration_fixture
from cpm_back.services.exams import rating, rating_jobs
from cpm_back.services.exams.common import UnitOfWork, transaction, now, dumps, loads

migration = migration_fixture.migration


@unittest.skipUnless(
    os.environ.get("EXAM_MIGRATION_TEST_SOCKET"),
    "Explicit disposable local MySQL socket required",
)
class ClassicExamRatingTests(unittest.TestCase):
    query = migration_fixture.LocalMySQLMigrationTests.query
    tearDown = migration_fixture.LocalMySQLMigrationTests.tearDown

    def setUp(self):
        migration_fixture.LocalMySQLMigrationTests.setUp(self)
        migration.run_migrations(self.connection, apply=True)
        self.db = UnitOfWork(self.connection)
        self.addCleanup(self.db.cursor.close)
        self.patch = patch(
            "cpm_back.db.mysql_pool.get_db_connection", side_effect=self.connect
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def connect(self):
        return mysql.connector.connect(
            unix_socket=os.environ["EXAM_MIGRATION_TEST_SOCKET"],
            user="root",
            password="",
            database=self.database,
            autocommit=False,
        )

    def exam(self, start=None, outside=False):
        ident = self.db.insert(
            "exams",
            {
                "exam_type": "outside_lms" if outside else "classic",
                "direction_id": 6,
                "date": "2026-09-12" if outside else None,
                "name": None,
            },
        )
        if not outside:
            self.db.insert(
                "classic_exam_settings",
                {
                    "exam_id": ident,
                    "start_at": start,
                    "end_at": start + timedelta(hours=10) if start else None,
                },
            )
        self.connection.commit()
        return ident

    def attempt(self, exam_id, number, grade, status="completed"):
        assignment = self.db.insert(
            "classic_exam_assignments",
            {
                "exam_id": exam_id,
                "student_id": 2081,
                "attempt_no": number,
                "created_by_role": "admin",
                "created_by": 1,
                "commission_name_snapshot": "Synthetic",
            },
        )
        attempt = self.db.insert(
            "classic_exam_attempts",
            {
                "exam_id": exam_id,
                "student_id": 2081,
                "assignment_id": assignment,
                "attempt_no": number,
                "status": status,
                "phase": "completed" if status == "completed" else "preparation",
                "student_name_snapshot": "Synthetic",
                "history_generation": 1,
                "calculated_grade": grade if status == "completed" else None,
                "raw_total": Decimal(grade),
                "rounded_total": grade,
                "max_score": 5,
                "completed_at": now() if status == "completed" else None,
            },
        )
        self.connection.commit()
        return attempt

    def snapshot(self, start="2026-09-01", end="2026-09-30", as_of=None):
        with transaction(write=False) as db:
            return rating.capture_exam_input(db, start, end, as_of)

    def prepared_job(self):
        result = rating_jobs.create_job(
            "2026-09-01", "2026-09-30", 1, "Synthetic admin"
        )
        claimed = rating_jobs.claim(result["id"])
        self.assertIsNotNone(claimed)
        return claimed, self.snapshot()

    def stage(self, job_id, student=2081, grade=3, final=69):
        with transaction(write=True) as db:
            db.insert(
                "rating_recalc_staging",
                {
                    "job_id": job_id,
                    "student_id": student,
                    "exams": grade,
                    "homework": 60,
                    "tests": 80,
                    "final": final,
                    "details_json": dumps(
                        {
                            "student_id": student,
                            "exams": {"rating": grade, "details": []},
                            "final_rating": final,
                        }
                    ),
                },
            )

    def test_moscow_inclusion_start_boundary_and_missing_counts_per_exam(self):
        outside = self.exam(outside=True)
        self.db.insert(
            "exam_sessions",
            {
                "exam_id": outside,
                "student_id": 2081,
                "points": 4,
                "val": Decimal("87.50"),
                "examinator": "Synthetic",
            },
        )
        due = self.exam(datetime(2026, 9, 12, 10))
        future = self.exam(datetime(2026, 9, 12, 10, 0, 1))
        previous = self.exam(datetime(2026, 9, 11, 20, 59, 59))
        snapshot = self.snapshot("2026-09-12", "2026-09-12", datetime(2026, 9, 12, 10))
        self.assertEqual({e["id"] for e in snapshot["exams"]}, {outside, due})
        self.assertEqual(snapshot["nextExamStartAt"], datetime(2026, 9, 12, 10, 0, 1))
        score = rating.calculate_from_snapshot(snapshot, 2081)
        self.assertEqual(score["total_count"], 2)
        self.assertEqual(score["average"], 2)
        self.assertEqual([e["score"] for e in score["details"]], [4, 0])
        self.assertEqual(rating.calculate_from_snapshot(snapshot, 999)["average"], 0)

    def test_unfinished_retake_preserves_first_then_worse_current_and_appeal(self):
        exam = self.exam(datetime(2026, 9, 12, 9))
        first = self.attempt(exam, 1, 5)
        second = self.attempt(exam, 2, 1, "pending_ready")
        snapshot = self.snapshot(as_of=datetime(2026, 9, 12, 12))
        self.assertEqual(snapshot["values"][(exam, 2081)]["grade"], 5)
        self.db.update(
            "classic_exam_attempts",
            {"status": "completed", "phase": "completed", "calculated_grade": 1},
            "id=%s",
            (second,),
        )
        self.db.insert(
            "classic_exam_appeals",
            {
                "attempt_id": first,
                "previous_grade": 5,
                "new_grade": 0,
                "changed_by_role": "admin",
                "changed_by_admin_id": 1,
                "admin_name_snapshot": "Synthetic",
            },
        )
        self.connection.commit()
        snapshot = self.snapshot(as_of=datetime(2026, 9, 12, 12))
        self.assertEqual(snapshot["values"][(exam, 2081)]["grade"], 1)
        self.db.insert(
            "classic_exam_appeals",
            {
                "attempt_id": second,
                "previous_grade": 1,
                "new_grade": 0,
                "changed_by_role": "staff_admin",
                "changed_by_admin_id": 1,
                "admin_name_snapshot": "Synthetic",
            },
        )
        self.connection.commit()
        value = self.snapshot(as_of=datetime(2026, 9, 12, 12))["values"][(exam, 2081)]
        self.assertEqual(value, {"grade": 0, "attempt_no": 2, "has_appeal": True})

    def test_publication_is_atomic_and_preserves_rating_id(self):
        original = self.db.one("SELECT * FROM Allratings WHERE student_id=2081")
        self.connection.commit()
        job, snapshot = self.prepared_job()
        with self.assertRaisesRegex(RuntimeError, "rating_staging_incomplete"):
            rating_jobs.publish(job, snapshot)
        self.assertEqual(
            self.query("SELECT final,details_json FROM Allratings"),
            [{"final": 75.0, "details_json": None}],
        )
        self.stage(job["id"])
        rating_jobs.publish(job, snapshot)
        after = self.query("SELECT * FROM Allratings WHERE student_id=2081")[0]
        self.assertEqual(after["id"], original["id"])
        self.assertEqual(after["exams"], 3)
        self.assertEqual(after["final"], 69)
        self.assertEqual(loads(after["details_json"])["final_rating"], 69)
        self.assertEqual(
            self.query("SELECT COUNT(*) n FROM rating_recalc_staging"), [{"n": 0}]
        )
        self.assertEqual(
            self.query(
                "SELECT status FROM rating_recalc_jobs WHERE id=%s", (job["id"],)
            )[0]["status"],
            "completed",
        )

    def test_revision_or_elapsed_future_start_rejects_publication(self):
        job, snapshot = self.prepared_job()
        self.stage(job["id"])
        self.query(
            "UPDATE rating_source_state SET source_revision=source_revision+1 WHERE id=1"
        )
        with self.assertRaisesRegex(RuntimeError, "rating_source_changed"):
            rating_jobs.publish(job, snapshot)
        self.assertEqual(self.query("SELECT final FROM Allratings"), [{"final": 75.0}])
        snapshot["sourceRevision"] += 1
        snapshot["nextExamStartAt"] = now() - timedelta(seconds=1)
        with self.assertRaisesRegex(RuntimeError, "rating_source_changed"):
            rating_jobs.publish(job, snapshot)
        self.assertEqual(
            self.query("SELECT details_json FROM Allratings"), [{"details_json": None}]
        )

    def test_mid_publication_failure_rolls_back_all_values_and_metadata(self):
        job, snapshot = self.prepared_job()
        self.stage(job["id"])
        execute = UnitOfWork.execute

        def fail_after_update(db, sql, params=()):
            if sql.startswith("INSERT INTO Allratings"):
                raise RuntimeError("Synthetic mid-publication failure")
            return execute(db, sql, params)

        with patch.object(UnitOfWork, "execute", fail_after_update):
            with self.assertRaisesRegex(RuntimeError, "Synthetic mid-publication"):
                rating_jobs.publish(job, snapshot)
        self.assertEqual(
            self.query("SELECT final,details_json FROM Allratings"),
            [{"final": 75.0, "details_json": None}],
        )
        self.assertIsNone(
            self.query("SELECT calculated_revision FROM rating_source_state")[0][
                "calculated_revision"
            ]
        )
        self.assertEqual(
            self.query(
                "SELECT status FROM rating_recalc_jobs WHERE id=%s", (job["id"],)
            )[0]["status"],
            "running",
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) n FROM rating_recalc_staging"), [{"n": 1}]
        )

    def test_detail_and_freshness_share_snapshot_during_concurrent_publication(self):
        self.query(
            "UPDATE Allratings SET details_json=%s",
            (dumps({"final_rating": 75, "exams": {"details": []}}),),
        )
        self.query(
            "UPDATE rating_source_state SET source_revision=7,calculated_revision=7"
        )
        original = rating.published_details

        def publish_after_read(db, **filters):
            details = original(db, **filters)
            with transaction(write=True) as writer:
                writer.execute(
                    "UPDATE Allratings SET details_json=%s",
                    (dumps({"final_rating": 42, "exams": {"details": []}}),),
                )
                writer.execute(
                    "UPDATE rating_source_state SET source_revision=8,calculated_revision=8"
                )
            return details

        with patch.object(rating, "published_details", publish_after_read):
            details, metadata = rating.read_published_bundle(student_id=2081)
        self.assertEqual(next(iter(details.values()))["final_rating"], 75)
        self.assertEqual(metadata["ratingFreshness"]["calculatedRevision"], 7)
        current, metadata = rating.read_published_bundle(student_id=2081)
        self.assertEqual(next(iter(current.values()))["final_rating"], 42)
        self.assertEqual(metadata["ratingFreshness"]["calculatedRevision"], 8)

    def test_cross_worker_claim_and_recovery_do_not_steal_live_jobs(self):
        result = rating_jobs.create_job("2026-09-01", "2026-09-30")
        with ThreadPoolExecutor(max_workers=4) as executor:
            claims = list(executor.map(rating_jobs.claim, [result["id"]] * 4))
        self.assertEqual(sum(c is not None for c in claims), 1)
        job = next(c for c in claims if c)
        rating_jobs.recover()
        self.assertEqual(
            self.query(
                "SELECT status FROM rating_recalc_jobs WHERE id=%s", (job["id"],)
            )[0]["status"],
            "running",
        )
        self.query(
            "UPDATE rating_recalc_jobs SET heartbeat_at=%s WHERE id=%s",
            (now() - timedelta(seconds=121), job["id"]),
        )
        rating_jobs.recover()
        self.assertEqual(
            self.query(
                "SELECT status FROM rating_recalc_jobs WHERE id=%s", (job["id"],)
            )[0]["status"],
            "failed",
        )
        self.assertIsNone(
            self.query("SELECT active_job_id FROM rating_source_state")[0][
                "active_job_id"
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "rating_worker_lease_lost"):
            rating_jobs.touch(job["id"], job["worker_token"])

    def test_failed_calculation_does_not_clear_published_rating(self):
        job = rating_jobs.create_job("2026-09-01", "2026-09-30")
        with patch("cpm_back.db.mongo.get_mongo_db", return_value=object()), patch(
            "cpm_back.services.exam.calculate_ratings.calculate_homework_rating",
            side_effect=ValueError("Synthetic failure"),
        ):
            rating_jobs.run(job["id"])
        self.assertEqual(
            self.query("SELECT final,details_json FROM Allratings"),
            [{"final": 75.0, "details_json": None}],
        )
        self.assertEqual(
            self.query(
                "SELECT status FROM rating_recalc_jobs WHERE id=%s", (job["id"],)
            )[0]["status"],
            "failed",
        )

    def test_run_publishes_existing_formula_with_mysql_details_only(self):
        job = rating_jobs.create_job("2026-09-01", "2026-09-30")
        hw = {"average": 60, "details": []}
        tests = {"average": 80, "details": [], "directions": {}}
        with patch("cpm_back.db.mongo.get_mongo_db", return_value=object()), patch(
            "cpm_back.services.exam.calculate_ratings.calculate_homework_rating",
            return_value=hw,
        ), patch(
            "cpm_back.services.exam.calculate_ratings.calculate_tests_rating",
            return_value=tests,
        ):
            rating_jobs.run(job["id"])
        result = self.query("SELECT * FROM Allratings")[0]
        self.assertEqual(result["exams"], 4)
        self.assertEqual(result["final"], 75)
        self.assertIsNotNone(result["details_json"])
        with transaction(write=False) as db:
            mongo = MagicMock()
            mongo.rate_rec.find.side_effect = AssertionError(
                "Published MySQL must not consult Mongo"
            )
            detail = rating.published_details(db, student_id=2081, mongo_db=mongo)[
                result["id"]
            ]
            self.assertEqual(detail["exams"]["rating"], 4)
            self.assertEqual(detail["rating_id"], result["id"])
            self.assertFalse(rating.freshness(db)["isStale"])

    def test_freshness_changes_when_time_passes_without_database_write(self):
        timestamp = now()
        self.query(
            "UPDATE rating_source_state SET calculated_revision=source_revision,next_exam_start_at=%s,as_of=%s WHERE id=1",
            (timestamp + timedelta(seconds=1), timestamp),
        )
        with transaction(write=False) as db:
            self.assertFalse(rating.freshness(db, timestamp)["isStale"])
            stale = rating.freshness(db, timestamp + timedelta(seconds=1))
            self.assertTrue(stale["isStale"])
            self.assertEqual(stale["reason"], "exam_started")
