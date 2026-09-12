#!/usr/bin/env python3
"""Explicit local-only exam reliability benchmark; never reads DB credentials.

Creates/drops one random cpm_exam_bench_* schema on the supplied Unix socket.
Exercises real authenticated Flask dispatch and MySQL transactions, not a WSGI
network server. Run with the project's Python 3.13 requirements environment.
"""

from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import timedelta
import argparse
import json
import logging
from pathlib import Path
import platform
import sys
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool
from flask import Flask
import exams_migrations as migration
from cpm_back.auth.jwt_auth import generate_token
from cpm_back.blueprints.examiner_exams_bp import examiner_exams_bp
from cpm_back.services.exams import common


def percentile(values, percentage):
    if not values:
        return None
    values = sorted(values)
    return round(values[max(0, int((len(values) - 1) * percentage))], 3)


class Benchmark:
    def __init__(self, args):
        self.args = args
        self.database = "cpm_exam_bench_" + uuid.uuid4().hex[:16]
        self.raw = mysql.connector.connect(
            unix_socket=args.socket, user="root", password="", autocommit=False
        )
        cursor = self.raw.cursor()
        cursor.execute(
            "CREATE DATABASE "
            + migration.identifier(self.database)
            + " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.execute("USE " + migration.identifier(self.database))
        schema = json.loads(
            (
                ROOT
                / "docs/classic-exams-implementation/2026-09-12-prod-preflight.json"
            ).read_text()
        )["schema"]
        for table in dict.fromkeys([*migration.REQUIRED_BASE_TABLES, "auth_users"]):
            cursor.execute(schema[table]["ddl"])
        cursor.close()
        migration.run_migrations(self.raw, apply=True)
        self.pool = MySQLConnectionPool(
            pool_name="bench_" + uuid.uuid4().hex[:12],
            pool_size=args.pool,
            pool_reset_session=True,
            unix_socket=args.socket,
            user="root",
            password="",
            database=self.database,
            autocommit=False,
        )
        self.pool_patch = patch(
            "cpm_back.db.mysql_pool.get_db_connection",
            side_effect=self.pool.get_connection,
        )
        self.pool_patch.start()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            JWT_SECRET_KEY="synthetic-local-load-benchmark-only",
        )
        self.app.register_blueprint(examiner_exams_bp)
        self.app.logger.setLevel(logging.CRITICAL)
        self.rows = defaultdict(list)
        self.lock = threading.Lock()
        self.local = threading.local()
        self.sql_max_bytes = 0
        self.sql_calls = 0
        original = common.UnitOfWork.execute

        def measure(db, sql, params=()):
            # Byte count includes serialized parameters, never their contents.
            size = len(sql.encode()) + len(common.dumps(params).encode())
            with self.lock:
                self.sql_max_bytes = max(self.sql_max_bytes, size)
                self.sql_calls += 1
            return original(db, sql, params)

        self.sql_patch = patch.object(common.UnitOfWork, "execute", measure)
        self.sql_patch.start()
        self.db = common.UnitOfWork(self.raw)
        self.direction = self.db.insert(
            "directions", {"name": "Synthetic load direction"}
        )
        self.raw.commit()
        self.pool_executor = ThreadPoolExecutor(max_workers=args.workers)

    def close(self):
        self.pool_executor.shutdown(wait=True)
        self.sql_patch.stop()
        self.pool_patch.stop()
        self.db.cursor.close()
        self.raw.rollback()
        assert self.database.startswith("cpm_exam_bench_") and len(self.database) == 31
        cursor = self.raw.cursor()
        cursor.execute("DROP DATABASE " + migration.identifier(self.database))
        cursor.close()
        self.raw.close()

    def seed(self, count=50, members=6, quota=6, max_bank=False):
        exam = self.db.insert(
            "exams",
            {
                "exam_type": "classic",
                "direction_id": self.direction,
                "name": None,
                "date": None,
            },
        )
        self.db.insert(
            "classic_exam_settings",
            {
                "exam_id": exam,
                "start_at": common.now() - timedelta(hours=1),
                "end_at": common.now() + timedelta(hours=3),
                "fractional_mode": "extra_question",
                "tie_breaker_source_mode": "any_part",
                "tie_breaker_half_mode": "repeat",
            },
        )
        part = self.db.insert(
            "classic_exam_parts",
            {
                "exam_id": exam,
                "code": "A",
                "question_weight": 5 if quota == 1 else 1,
                "question_count": quota,
                "sort_order": 1,
            },
        )
        for grade in range(6):
            self.db.insert(
                "classic_exam_grade_thresholds",
                {"exam_id": exam, "grade": grade, "min_score": grade},
            )
        bank_count = 5000 if max_bank else quota + 3
        remaining = 16 * 1024 * 1024
        bank_rows = []
        for index in range(bank_count):
            if max_bank:
                size = 122880 if index == 0 else remaining // (bank_count - index)
                qsize = size // 2
                prefix = f"Question {index}: "
                question = prefix + "q" * (qsize - len(prefix))
                answer = "a" * (size - qsize)
                remaining -= size
            else:
                question, answer = f"Question {index}", f"Answer {index}"
            bank_rows.append(
                (
                    exam,
                    part,
                    question,
                    answer,
                    common.digest([question, answer]),
                    index + 1,
                )
            )
        # Bounded fixture inserts; definition creation itself must remain INSERT SELECT.
        for start in range(0, bank_count, 100):
            self.db.cursor.executemany(
                "INSERT INTO classic_exam_questions(exam_id,part_id,question_text,answer_text,content_hash,sort_order) VALUES(%s,%s,%s,%s,%s,%s)",
                bank_rows[start : start + 100],
            )
        groups = []
        for number in range(count):
            student = self.db.insert(
                "students",
                {
                    "full_name": f"Synthetic student {exam}:{number}",
                    "class": 10,
                    "tg_name": "",
                },
            )
            self.db.insert(
                "auth_users",
                {
                    "role": "student",
                    "ref_id": student,
                    "username": f"bench-student-{student}",
                },
            )
            assignment = self.db.insert(
                "classic_exam_assignments",
                {
                    "exam_id": exam,
                    "student_id": student,
                    "attempt_no": 1,
                    "created_by_role": "admin",
                    "created_by": 1,
                    "commission_name_snapshot": f"Synthetic commission {number}",
                },
            )
            self.db.insert(
                "classic_exam_student_privileges",
                {"exam_id": exam, "student_id": student, "replacement_limit": 1},
            )
            actors = []
            for position in range(members):
                ident = self.db.insert(
                    "examinators",
                    {"full_name": f"Synthetic examiner {exam}:{number}:{position}"},
                )
                self.db.insert(
                    "auth_users",
                    {
                        "role": "examinator",
                        "ref_id": ident,
                        "username": f"bench-examiner-{ident}",
                    },
                )
                self.db.insert(
                    "classic_exam_assignment_members",
                    {
                        "assignment_id": assignment,
                        "examinator_id": ident,
                        "position": position + 1,
                        "full_name_snapshot": f"Synthetic examiner {position}",
                    },
                )
                with self.app.app_context():
                    actors.append(
                        generate_token(
                            {
                                "role": "examinator",
                                "id": ident,
                                "full_name": f"Synthetic examiner {position}",
                            }
                        )
                    )
            groups.append(
                {
                    "number": number,
                    "examId": exam,
                    "assignmentId": assignment,
                    "actors": actors,
                }
            )
        self.raw.commit()
        return groups

    def call(
        self,
        group,
        actor,
        command,
        payload=None,
        profile="main",
        key=None,
        tolerate=False,
        scheduled_at=None,
    ):
        if command == "ensure":
            path = f'/api/examiner/exams/{group["examId"]}/assignments/{group["assignmentId"]}/attempts/ensure'
        else:
            path = f'/api/examiner/attempts/{group["state"]["id"]}' + (
                "" if command == "refresh" else "/" + command
            )
        started = time.perf_counter()
        with self.app.test_client() as client:
            response = client.open(
                path,
                method="GET" if command == "refresh" else "POST",
                json=payload,
                headers={
                    "Authorization": "Bearer " + group["actors"][actor],
                    "Idempotency-Key": key or str(uuid.uuid4()),
                },
            )
        duration = (time.perf_counter() - started) * 1000
        queued_duration = (time.perf_counter() - (scheduled_at or started)) * 1000
        data = response.get_json()
        with self.lock:
            self.rows[profile].append(
                {
                    "command": command,
                    "ms": duration,
                    "queuedMs": queued_duration,
                    "bytes": len(response.data),
                    "status": response.status_code,
                    "error": data.get("error") if data else None,
                }
            )
        if response.status_code >= 400:
            if tolerate:
                return None
            raise RuntimeError(
                f'{profile}:{command}:HTTP{response.status_code}:{data.get("error")}'
            )
        return data["data"]["attempt"]

    def batch(self, jobs):
        futures = [
            (
                group,
                self.pool_executor.submit(
                    self.call, group, *args, scheduled_at=time.perf_counter()
                ),
            )
            for group, *args in jobs
        ]
        for group, future in futures:
            state = future.result()
            if state and (
                "state" not in group
                or state["stateVersion"] >= group["state"]["stateVersion"]
            ):
                group["state"] = state

    def prepare(self, groups, profile):
        self.batch(
            [
                (g, 0, "ensure", {"expectedHistoryGeneration": 1}, profile)
                for g in groups
            ]
        )
        self.batch(
            [
                (g, i, "ready", {}, profile)
                for g in groups
                for i in range(len(g["actors"]))
            ]
        )
        self.batch(
            [
                (
                    g,
                    0,
                    "start",
                    {"expectedStateVersion": g["state"]["stateVersion"]},
                    profile,
                )
                for g in groups
            ]
        )

    def votes(self, groups, value, profile, dispute=False):
        jobs = []
        for g in groups:
            question = g["state"]["currentQuestion"]
            for i in range(len(g["actors"])):
                vote = value(g) if callable(value) else value
                if dispute and i == len(g["actors"]) - 1:
                    vote = 0 if vote else 1
                jobs.append(
                    (
                        g,
                        i,
                        "vote",
                        {
                            "presentedQuestionId": question["id"],
                            "roundId": question["round"]["id"],
                            "value": vote,
                        },
                        profile,
                    )
                )
        self.batch(jobs)

    def main_profile(self):
        groups = self.seed(self.args.commissions)
        self.prepare(groups, "main")
        disputed_count = round(len(groups) * 0.3)
        extra_count = round(len(groups) * 0.2)
        replacement_count = round(len(groups) * 6 * 0.1)
        for question_no in range(6):
            if question_no == 0:
                self.batch(
                    [
                        (
                            g,
                            0,
                            "replace-question",
                            {
                                "presentedQuestionId": g["state"]["currentQuestion"][
                                    "id"
                                ],
                                "expectedStateVersion": g["state"]["stateVersion"],
                            },
                            "main",
                        )
                        for g in groups[:replacement_count]
                    ]
                )
            value = lambda g: (
                0.5 if question_no == 0 and g["number"] < extra_count else 1
            )
            self.votes(groups[:disputed_count], value, "main", dispute=True)
            self.votes(groups[disputed_count:], value, "main")
            self.votes(groups[:disputed_count], value, "main")
            if question_no < 5:
                self.batch(
                    [
                        (
                            g,
                            0,
                            "next-question",
                            {
                                "presentedQuestionId": g["state"]["currentQuestion"][
                                    "id"
                                ],
                                "expectedStateVersion": g["state"]["stateVersion"],
                            },
                            "main",
                        )
                        for g in groups
                    ]
                )
        extra = [g for g in groups if g["state"]["phase"] == "tie_breaker"]
        assert len(extra) == extra_count
        self.votes(extra, 1, "main")
        self.batch(
            [
                (g, i, "refresh", None, "main")
                for g in groups
                for i in range(len(g["actors"]))
            ]
        )
        assert all(g["state"]["status"] == "completed" for g in groups)
        for group in groups:
            result = group["state"]["result"]
            assert result["rawTotal"] == (5.5 if group["number"] < extra_count else 6)
            assert result["roundedTotal"] == 6 and result["grade"] == 5
        self.raw.rollback()
        eid = groups[0]["examId"]
        invariants = self.db.one(
            """SELECT COUNT(DISTINCT a.id) attempts,COUNT(DISTINCT p.id) presented,
            COUNT(DISTINCT v.id) votes,COUNT(DISTINCT CASE WHEN r.status='disputed' THEN r.id END) disputed_rounds,
            COUNT(DISTINCT CASE WHEN p.status='replaced' THEN p.id END) replacements
            FROM classic_exam_attempts a JOIN classic_exam_presented_questions p ON p.attempt_id=a.id
            LEFT JOIN classic_exam_vote_rounds r ON r.presented_question_id=p.id
            LEFT JOIN classic_exam_votes v ON v.round_id=r.id WHERE a.exam_id=%s""",
            (eid,),
        )
        expected_votes = (len(groups) * 6 + disputed_count * 6 + extra_count) * 6
        assert invariants["votes"] == expected_votes, (invariants, expected_votes)
        assert invariants["replacements"] == replacement_count
        assert invariants["disputed_rounds"] == disputed_count * 6
        invariants["allFinalScoresChecked"] = True
        self.raw.rollback()
        return groups, invariants

    def saturation(self, groups):
        with ThreadPoolExecutor(max_workers=self.args.saturation_workers) as executor:
            jobs = [
                executor.submit(
                    self.call,
                    g,
                    i,
                    "refresh",
                    None,
                    "saturation",
                    None,
                    True,
                    scheduled_at=time.perf_counter(),
                )
                for g in groups
                for i in range(len(g["actors"]))
            ]
            for job in jobs:
                job.result()

    def long_history(self):
        group = self.seed(1, 6, 1)[0]
        self.prepare([group], "long_history_setup")
        self.votes([group], 0.5, "long_history_setup")
        before = len(json.dumps(group["state"]).encode())
        last = None
        for index in range(self.args.extra_rounds):
            current = group["state"]["currentQuestion"]["questionText"]
            assert current != last, "Extra questions must never immediately repeat"
            last = current
            self.votes([group], 0.5, "long_history")
            if (index + 1) % 100 == 0:
                print(
                    f"Extra rounds: {index+1}/{self.args.extra_rounds}",
                    file=sys.stderr,
                    flush=True,
                )
        after = len(json.dumps(group["state"]).encode())
        self.votes([group], 1, "long_history_finish")
        self.raw.rollback()
        counts = self.db.one(
            """SELECT COUNT(DISTINCT p.id) questions,COUNT(v.id) votes
            FROM classic_exam_presented_questions p JOIN classic_exam_vote_rounds r ON r.presented_question_id=p.id
            JOIN classic_exam_votes v ON v.round_id=r.id WHERE p.attempt_id=%s""",
            (group["state"]["id"],),
        )
        assert counts["questions"] == self.args.extra_rounds + 2
        assert counts["votes"] == (self.args.extra_rounds + 2) * 6
        assert after - before < 200, (before, after)
        self.raw.rollback()
        return dict(counts, stateBytesBefore=before, stateBytesAfter=after)

    def maximum_bank(self):
        group = self.seed(1, 6, 1, max_bank=True)[0]

        class FirstQuestion:
            def randrange(self, total):
                return 0

            def choice(self, items):
                return items[0]

        sql_before = self.sql_calls
        sql_max_before = self.sql_max_bytes
        self.sql_max_bytes = 0
        started = time.perf_counter()
        with patch(
            "cpm_back.services.exams.selection.random.SystemRandom",
            return_value=FirstQuestion(),
        ):
            self.prepare([group], "maximum_bank")
        elapsed = (time.perf_counter() - started) * 1000
        command_max = self.sql_max_bytes
        self.sql_max_bytes = max(command_max, sql_max_before)
        state = group["state"]
        assert len(state["currentQuestion"]["questionText"].encode()) == 61440
        assert len(state["currentQuestion"]["answerText"].encode()) == 61440
        self.raw.rollback()
        bank = self.db.one(
            """SELECT COUNT(*) questions,SUM(OCTET_LENGTH(question_text)+OCTET_LENGTH(answer_text)) bytes
            FROM classic_exam_definition_questions WHERE definition_id=(SELECT definition_id FROM classic_exam_attempts WHERE id=%s)""",
            (state["id"],),
        )
        assert bank["questions"] == 5000 and int(bank["bytes"]) == 16 * 1024 * 1024
        self.raw.rollback()
        return dict(
            questions=bank["questions"],
            bankBytes=int(bank["bytes"]),
            prepareIncludingSixReadyMs=round(elapsed, 3),
            sqlCalls=self.sql_calls - sql_before,
            maxParameterizedStatementBytes=command_max,
            selectedQuestionTextBytes=122880,
            note="Forced first random choice only in this profile, to measure the largest allowed current-question response.",
        )

    def summary(self):
        result = {}
        for profile, rows in self.rows.items():
            durations = [r["ms"] for r in rows]
            result[profile] = {
                "requests": len(rows),
                "errors": sum(r["status"] >= 400 for r in rows),
                "statusCounts": dict(Counter(str(r["status"]) for r in rows)),
                "errorCodes": dict(Counter(r["error"] for r in rows if r["error"])),
                "p50Ms": percentile(durations, 0.5),
                "p95Ms": percentile(durations, 0.95),
                "p99Ms": percentile(durations, 0.99),
                "queuedP50Ms": percentile([r["queuedMs"] for r in rows], 0.5),
                "queuedP95Ms": percentile([r["queuedMs"] for r in rows], 0.95),
                "queuedP99Ms": percentile([r["queuedMs"] for r in rows], 0.99),
                "maxMs": round(max(durations), 3),
                "maxPayloadBytes": max(r["bytes"] for r in rows),
                "commands": {},
            }
            for command in sorted({r["command"] for r in rows}):
                selected = [r for r in rows if r["command"] == command]
                result[profile]["commands"][command] = {
                    "count": len(selected),
                    "p50Ms": percentile([r["ms"] for r in selected], 0.5),
                    "p95Ms": percentile([r["ms"] for r in selected], 0.95),
                    "p99Ms": percentile([r["ms"] for r in selected], 0.99),
                }
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--pool", type=int, default=32)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--saturation-workers", type=int, default=64)
    parser.add_argument("--commissions", type=int, default=50)
    parser.add_argument("--extra-rounds", type=int, default=1000)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not Path(args.socket).is_absolute() or not Path(args.socket).is_socket():
        parser.error(
            "An existing absolute local Unix socket is required; TCP/remote hosts are not supported"
        )
    if (
        not 1 <= args.workers <= args.pool <= 32
        or not args.pool < args.saturation_workers <= 64
        or not 1 <= args.commissions <= 50
        or not 0 <= args.extra_rounds <= 1000
    ):
        parser.error(
            "Require 1 <= workers <= pool <= 32; pool < saturation-workers <= 64; commissions <= 50; extra-rounds <= 1000"
        )
    started = time.perf_counter()
    bench = Benchmark(args)
    report = {
        "profile": {
            "commissions": args.commissions,
            "membersPerCommission": 6,
            "regularQuestions": 6,
            "disputedQuestionsFraction": 0.3,
            "replacedQuestionsFraction": 0.1,
            "extraAttemptsFraction": 0.2,
            "workers": args.workers,
            "poolConnections": args.pool,
            "saturationWorkers": args.saturation_workers,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "transport": "Authenticated Flask test-client dispatch; real local Unix-socket MySQL; no HTTP proxy/WSGI/network RTT",
            "latency": "pXXMs includes dispatch and pool admission; queuedPXXMs additionally includes waiting for a benchmark executor worker",
        }
    }
    try:
        report["database"] = bench.db.one(
            "SELECT VERSION() mysqlVersion,@@max_allowed_packet maxAllowedPacket"
        )
        bench.raw.rollback()
        groups, report["mainInvariants"] = bench.main_profile()
        print("Commission workflow complete", file=sys.stderr, flush=True)
        bench.saturation(groups)
        report["longHistoryInvariants"] = bench.long_history()
        report["maximumBank"] = bench.maximum_bank()
        report["success"] = True
    except Exception as error:
        report["success"] = False
        report["failureType"] = type(error).__name__
        report["failure"] = str(error)
        raise
    finally:
        report["measurements"] = bench.summary()
        report["elapsedSeconds"] = round(time.perf_counter() - started, 3)
        bench.close()
        report["disposableSchemaDropped"] = True
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        )
        print(
            json.dumps(
                {
                    "success": report["success"],
                    "elapsedSeconds": report["elapsedSeconds"],
                    "output": args.output,
                }
            )
        )


if __name__ == "__main__":
    main()
