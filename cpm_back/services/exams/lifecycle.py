"""Commission workflow. Every public mutation is one caller-owned SQL transaction."""

from decimal import Decimal
from . import admin
from .common import (
    fail,
    fields,
    integer,
    check_version,
    now,
    iso,
    loads,
    dumps,
    digest,
    lock_exam,
    invalidate_rating,
    page_args,
    pagination,
    enum_value,
    search_value,
)
from .scoring import (
    vote_value,
    calculate_question_score,
    calculate_max_score,
    round_total,
    grade_for_score,
    resolve_extra_vote,
)
from .selection import select_question


def get_attempt_row(db, attempt_id, actor=None, for_update=False, exam_id=None):
    row = db.one(
        "SELECT * FROM classic_exam_attempts WHERE id=%s"
        + (" FOR UPDATE" if for_update else ""),
        (attempt_id,),
    )
    if not row or (exam_id is not None and row["exam_id"] != exam_id):
        fail("attempt_not_found", "Сдача не найдена", 404)
    if (
        actor
        and actor["role"] == "examinator"
        and not db.one(
            "SELECT attempt_id FROM classic_exam_attempt_members WHERE attempt_id=%s AND examinator_id=%s",
            (attempt_id, actor["id"]),
        )
    ):
        fail("attempt_not_found", "Сдача не найдена", 404)
    return row


def lock_attempt(db, attempt_id, actor, exclusive_exam=False):
    # Initial lookup is discovery only; recheck ownership/existence after locks.
    initial = get_attempt_row(db, attempt_id, actor)
    lock_exam(db, initial["exam_id"], "classic", not exclusive_exam)
    if exclusive_exam:
        db.one(
            "SELECT id FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s AND attempt_no=1 FOR UPDATE",
            (initial["exam_id"], initial["student_id"]),
        )
    return get_attempt_row(db, attempt_id, actor, True)


def member_rows(db, attempt_id):
    return db.all(
        "SELECT * FROM classic_exam_attempt_members WHERE attempt_id=%s ORDER BY position",
        (attempt_id,),
    )


def progress_rows(db, attempt_id):
    return db.all(
        "SELECT * FROM classic_exam_attempt_part_progress WHERE attempt_id=%s ORDER BY part_code,source_part_id",
        (attempt_id,),
    )


def definition_config(db, attempt):
    row = db.one(
        "SELECT config_json FROM classic_exam_definition_versions WHERE id=%s AND exam_id=%s",
        (attempt["definition_id"], attempt["exam_id"]),
    )
    if not row:
        fail("definition_missing", status=500)
    return loads(row["config_json"])


def round_dto(db, round_row, actor, members_count, include_votes=False):
    rows = db.all(
        """SELECT v.examinator_id,v.value,v.created_at,m.full_name_snapshot FROM classic_exam_votes v
        JOIN classic_exam_attempt_members m ON m.attempt_id=%s AND m.examinator_id=v.examinator_id
        WHERE v.round_id=%s ORDER BY m.position""",
        (round_row["attempt_id"], round_row["id"]),
    )
    own = (
        next((r for r in rows if r["examinator_id"] == actor["id"]), None)
        if actor["role"] == "examinator"
        else None
    )
    result = {
        "id": round_row["id"],
        "roundNo": round_row["round_no"],
        "status": round_row["status"],
        "votesReceived": len(rows),
        "votesRequired": members_count,
        "hasVoted": own is not None,
        "myVote": own["value"] if own else None,
        "consensus": round_row["consensus_value"],
    }
    if (
        include_votes
        or actor["role"] in ("admin", "staff_admin")
        or round_row["status"] != "open"
    ):
        result["votes"] = [
            {
                "examinatorId": r["examinator_id"],
                "fullName": r["full_name_snapshot"],
                "value": r["value"],
                "votedAt": iso(r["created_at"]),
            }
            for r in rows
        ]
    return result


def current_question(db, attempt, actor, members_count):
    if not attempt["current_presented_question_id"]:
        return None
    row = db.one(
        """SELECT p.*,q.source_part_id,q.part_code,q.question_text,q.answer_text,g.question_weight
        FROM classic_exam_presented_questions p JOIN classic_exam_definition_questions q ON q.id=p.definition_question_id
        JOIN classic_exam_attempt_part_progress g ON g.attempt_id=p.attempt_id AND g.source_part_id=q.source_part_id
        WHERE p.id=%s AND p.attempt_id=%s""",
        (attempt["current_presented_question_id"], attempt["id"]),
    )
    if not row:
        fail("current_question_missing", status=500)
    round_row = db.one(
        "SELECT * FROM classic_exam_vote_rounds WHERE presented_question_id=%s ORDER BY round_no DESC LIMIT 1",
        (row["id"],),
    )
    last_completed = db.one(
        "SELECT * FROM classic_exam_vote_rounds WHERE presented_question_id=%s AND status<>'open' ORDER BY round_no DESC LIMIT 1",
        (row["id"],),
    )
    total_votes = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_votes v JOIN classic_exam_vote_rounds r ON r.id=v.round_id WHERE r.presented_question_id=%s",
        (row["id"],),
    )
    remaining = (
        db.scalar(
            "SELECT SUM(required_count-consensus_count) FROM classic_exam_attempt_part_progress WHERE attempt_id=%s",
            (attempt["id"],),
        )
        or 0
    )
    return {
        "id": row["id"],
        "sequenceNo": row["sequence_no"],
        "purpose": row["purpose"],
        "partCode": row["part_code"],
        "questionText": row["question_text"],
        "answerText": row["answer_text"],
        "weight": row["question_weight"],
        "cycleNo": row["cycle_no"],
        "status": row["status"],
        "replacesPresentedQuestionId": row["replaces_presented_question_id"],
        "consensus": row["consensus_value"],
        "awardedPoints": row["weighted_score"] or Decimal(0),
        "canReplace": attempt["status"] == "in_progress"
        and row["status"] == "open"
        and total_votes == 0
        and attempt["replacement_used"] < attempt["replacement_limit_snapshot"],
        "canGoNext": attempt["status"] == "in_progress"
        and row["status"] == "consensus"
        and row["purpose"] == "regular"
        and remaining > 0,
        "round": round_dto(db, round_row, actor, members_count) if round_row else None,
        "lastCompletedRound": (
            round_dto(db, last_completed, actor, members_count)
            if last_completed
            else None
        ),
        "completedRoundsCount": db.scalar(
            "SELECT COUNT(*) FROM classic_exam_vote_rounds WHERE presented_question_id=%s AND status<>'open'",
            (row["id"],),
        ),
    }


def attempt_state(db, attempt_id, actor):
    attempt = get_attempt_row(db, attempt_id, actor)
    exam = db.one(
        "SELECT e.direction_id,d.name FROM exams e LEFT JOIN directions d ON d.id=e.direction_id WHERE e.id=%s",
        (attempt["exam_id"],),
    )
    members = member_rows(db, attempt_id)
    all_ready = bool(members) and all(m["ready_at"] is not None for m in members)
    own = (
        next((m for m in members if m["examinator_id"] == actor["id"]), None)
        if actor["role"] == "examinator"
        else None
    )
    progress = progress_rows(db, attempt_id)
    question = current_question(db, attempt, actor, len(members))
    can_prepare = False
    can_start = False
    if attempt["status"] == "pending_ready":
        report = admin.readiness(db, attempt["exam_id"], attempt["assignment_id"], True)
        can_prepare = report["canPrepare"]
        can_start = report["canStartNow"] and all_ready
    result = None
    if attempt["status"] == "completed":
        from .results import result_summary

        result = result_summary(db, attempt)
    limit = attempt["replacement_limit_snapshot"]
    if limit is None:
        limit = admin.get_privilege(db, attempt["exam_id"], attempt["student_id"])[
            "replacementLimit"
        ]
    return {
        "id": attempt_id,
        "examId": attempt["exam_id"],
        "examType": "classic",
        "directionId": exam["direction_id"],
        "directionName": exam["name"],
        "assignmentId": attempt["assignment_id"],
        "attemptNo": attempt["attempt_no"],
        "historyGeneration": attempt["history_generation"],
        "student": {
            "id": attempt["student_id"],
            "fullName": attempt["student_name_snapshot"],
        },
        "status": attempt["status"],
        "phase": attempt["phase"],
        "stateVersion": attempt["state_version"],
        "members": [
            {
                "id": m["examinator_id"],
                "fullName": m["full_name_snapshot"],
                "position": m["position"],
                "ready": m["ready_at"] is not None,
                "readyAt": iso(m["ready_at"]),
            }
            for m in members
        ],
        "allMembersReady": all_ready,
        "startedAt": iso(attempt["started_at"]),
        "completedAt": iso(attempt["completed_at"]),
        "serverNow": iso(now()),
        "currentQuestion": question,
        "progress": {
            "regularConsensus": sum(p["consensus_count"] for p in progress),
            "regularRequired": sum(p["required_count"] for p in progress),
            "replacementUsed": attempt["replacement_used"],
            "replacementLimit": limit,
            "parts": [
                {
                    "sourcePartId": p["source_part_id"],
                    "code": p["part_code"],
                    "consensus": p["consensus_count"],
                    "required": p["required_count"],
                }
                for p in progress
            ],
        },
        "result": result,
        "resultVersion": attempt["result_version"],
        "permissions": {
            "canReady": bool(own and not own["ready_at"] and can_prepare),
            "canStart": bool(own and can_start),
            "canVote": bool(
                own
                and question
                and question["status"] == "open"
                and question["round"]
                and not question["round"]["hasVoted"]
            ),
            "canReplace": bool(own and question and question["canReplace"]),
            "canGoNext": bool(own and question and question["canGoNext"]),
        },
    }


def receipt_lookup(db, attempt_id, actor, key, command, payload):
    request_hash = digest({"command": command, "payload": payload})
    row = db.one(
        "SELECT * FROM classic_exam_attempt_commands WHERE attempt_id=%s AND actor_examinator_id=%s AND idempotency_key=%s",
        (attempt_id, actor["id"], key),
    )
    if row and row["request_hash"] != request_hash:
        fail("idempotency_key_reused", status=409)
    return loads(row["receipt"]) if row else None, request_hash


def store_receipt(db, attempt_id, actor, key, command, request_hash, receipt):
    command_id = db.insert(
        "classic_exam_attempt_commands",
        {
            "attempt_id": attempt_id,
            "actor_examinator_id": actor["id"],
            "idempotency_key": key,
            "command_type": command,
            "request_hash": request_hash,
            "receipt": dumps(receipt),
        },
    )
    receipt = dict(receipt, commandId=command_id)
    db.update(
        "classic_exam_attempt_commands",
        {"receipt": dumps(receipt)},
        "id=%s",
        (command_id,),
    )
    return receipt


def assert_prepare(db, exam_id, assignment_id, window=False):
    report = admin.readiness(db, exam_id, assignment_id, window)
    if report["errors"]:
        code = (
            "exam_outside_window"
            if all(e["scope"] == "window" for e in report["errors"])
            else "exam_not_ready"
        )
        fail(code, "Экзамен пока нельзя начать", 422, readiness=report)


def ensure(db, exam_id, assignment_id, payload, actor, key):
    fields(payload, ("expectedHistoryGeneration",), ("expectedHistoryGeneration",))
    generation = integer(payload["expectedHistoryGeneration"], "history_generation")
    lock_exam(db, exam_id, "classic", True)
    assignment = admin.require_row(
        db, "classic_exam_assignments", assignment_id, exam_id, "assignment_not_found"
    )
    if not db.one(
        "SELECT assignment_id FROM classic_exam_assignment_members WHERE assignment_id=%s AND examinator_id=%s",
        (assignment_id, actor["id"]),
    ):
        fail("assignment_not_found", status=404)
    first = db.one(
        "SELECT * FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s AND attempt_no=1 FOR UPDATE",
        (exam_id, assignment["student_id"]),
    )
    if not first or first["history_generation"] != generation:
        fail(
            "history_generation_changed",
            "История была очищена. Обновите список.",
            409,
            currentVersion=first["history_generation"] if first else None,
        )
    existing = db.one(
        "SELECT id FROM classic_exam_attempts WHERE assignment_id=%s FOR UPDATE",
        (assignment_id,),
    )
    created = not existing
    if created:
        assert_prepare(db, exam_id, assignment_id)
        student = admin.account(db, "student", assignment["student_id"])
        if assignment["attempt_no"] == 2 and not db.one(
            "SELECT id FROM classic_exam_attempts WHERE exam_id=%s AND student_id=%s AND attempt_no=1 AND status='completed'",
            (exam_id, assignment["student_id"]),
        ):
            fail("first_attempt_not_completed", status=422)
        attempt_id = db.insert(
            "classic_exam_attempts",
            {
                "exam_id": exam_id,
                "student_id": assignment["student_id"],
                "assignment_id": assignment_id,
                "attempt_no": assignment["attempt_no"],
                "history_generation": generation,
                "student_name_snapshot": student["full_name"],
            },
        )
        for member in admin.assignment_members(db, assignment_id):
            db.insert(
                "classic_exam_attempt_members",
                {
                    "attempt_id": attempt_id,
                    "examinator_id": member["id"],
                    "position": member["position"],
                    "full_name_snapshot": member["fullName"],
                },
            )
    else:
        attempt_id = existing["id"]
    receipt, request_hash = receipt_lookup(
        db, attempt_id, actor, key, "ensure", payload
    )
    replayed = receipt is not None
    if not receipt:
        version = db.scalar(
            "SELECT state_version FROM classic_exam_attempts WHERE id=%s", (attempt_id,)
        )
        receipt = store_receipt(
            db,
            attempt_id,
            actor,
            key,
            "ensure",
            request_hash,
            {
                "outcome": "created" if created else "existing",
                "appliedStateVersion": version,
                "attemptId": attempt_id,
            },
        )
    return {
        "created": created,
        "receipt": receipt,
        "replayed": replayed,
        "attempt": attempt_state(db, attempt_id, actor),
    }, (201 if created else 200)


def make_definition(db, exam_id):
    config = admin.get_config_row(db, exam_id)
    existing = db.one(
        "SELECT id FROM classic_exam_definition_versions WHERE exam_id=%s AND config_version=%s",
        (exam_id, config["config_version"]),
    )
    if existing:
        return existing["id"]
    snapshot = {
        key: iso(config[col]) if key in ("startAt", "endAt") else config[col]
        for key, col in admin.CONFIG_COLUMNS.items()
    }
    snapshot["parts"] = [
        {
            "sourcePartId": p["id"],
            "code": p["code"],
            "weight": p["question_weight"],
            "quota": p["question_count"],
        }
        for p in admin.part_rows(db, exam_id)
    ]
    snapshot["thresholds"] = admin.thresholds(db, exam_id)
    definition_id = db.insert(
        "classic_exam_definition_versions",
        {
            "exam_id": exam_id,
            "config_version": config["config_version"],
            "config_json": dumps(snapshot),
        },
    )
    db.execute(
        """INSERT INTO classic_exam_definition_questions(definition_id,source_question_id,source_part_id,part_code,question_text,answer_text)
        SELECT %s,q.id,q.part_id,p.code,q.question_text,q.answer_text FROM classic_exam_questions q
        JOIN classic_exam_parts p ON p.id=q.part_id WHERE q.exam_id=%s ORDER BY p.sort_order,q.sort_order,q.id""",
        (definition_id, exam_id),
    )
    return definition_id


def selection_inputs(db, attempt):
    questions = db.all(
        "SELECT id,source_part_id FROM classic_exam_definition_questions WHERE definition_id=%s ORDER BY id",
        (attempt["definition_id"],),
    )
    usage = {
        r["definition_question_id"]: r
        for r in db.all(
            "SELECT * FROM classic_exam_attempt_question_usage WHERE attempt_id=%s",
            (attempt["id"],),
        )
    }
    return questions, usage, progress_rows(db, attempt["id"])


def choose(db, attempt, purpose, replace_current=None):
    config = definition_config(db, attempt)
    questions, usage, progress = selection_inputs(db, attempt)
    if replace_current:
        source_id = db.scalar(
            "SELECT source_part_id FROM classic_exam_definition_questions WHERE id=%s",
            (replace_current["definition_question_id"],),
        )
        progress = [p for p in progress if p["source_part_id"] == source_id]
    return select_question(
        questions,
        usage,
        progress,
        purpose,
        attempt["last_definition_question_id"],
        config["tieBreakerSourceMode"],
        config["tieBreakerPartId"],
        replace_current["definition_question_id"] if replace_current else None,
    )


def present(db, attempt, purpose, selection=None, replaces_id=None):
    question, part_id, cycle = selection or choose(db, attempt, purpose)
    sequence = db.scalar(
        "SELECT COALESCE(MAX(sequence_no),0)+1 FROM classic_exam_presented_questions WHERE attempt_id=%s",
        (attempt["id"],),
    )
    presented_id = db.insert(
        "classic_exam_presented_questions",
        {
            "attempt_id": attempt["id"],
            "definition_question_id": question["id"],
            "sequence_no": sequence,
            "purpose": purpose,
            "cycle_no": cycle,
            "replaces_presented_question_id": replaces_id,
        },
    )
    db.execute(
        """INSERT INTO classic_exam_attempt_question_usage(attempt_id,definition_question_id,last_cycle_no,ever_presented,permanently_excluded)
        VALUES(%s,%s,%s,TRUE,FALSE) ON DUPLICATE KEY UPDATE last_cycle_no=VALUES(last_cycle_no),ever_presented=TRUE""",
        (attempt["id"], question["id"], cycle),
    )
    db.execute(
        "UPDATE classic_exam_attempt_part_progress SET cycle_no=%s WHERE attempt_id=%s AND source_part_id=%s",
        (cycle, attempt["id"], part_id),
    )
    db.insert(
        "classic_exam_vote_rounds",
        {
            "attempt_id": attempt["id"],
            "presented_question_id": presented_id,
            "round_no": 1,
        },
    )
    db.update(
        "classic_exam_attempts",
        {
            "current_presented_question_id": presented_id,
            "last_definition_question_id": question["id"],
        },
        "id=%s",
        (attempt["id"],),
    )
    attempt["current_presented_question_id"] = presented_id
    attempt["last_definition_question_id"] = question["id"]
    return presented_id


def finish_or_extra(db, attempt, consensus=None):
    config = definition_config(db, attempt)
    raw = Decimal(
        db.scalar(
            "SELECT COALESCE(SUM(weighted_score),0) FROM classic_exam_presented_questions WHERE attempt_id=%s AND purpose='regular' AND status='consensus'",
            (attempt["id"],),
        )
    )
    maximum = calculate_max_score(config["parts"])
    if attempt["phase"] == "tie_breaker":
        direction = resolve_extra_vote(consensus, config["tieBreakerHalfMode"])
        if direction == "repeat":
            return {
                "outcome": "next_extra",
                "presentedQuestionId": present(db, attempt, "tie_breaker"),
            }
        resolution = "tie_breaker_" + direction
    elif raw == raw.to_integral_value():
        direction = "down"
        resolution = "integer"
    elif config["fractionalMode"] == "extra_question":
        db.update(
            "classic_exam_attempts",
            {"raw_total": raw, "max_score": maximum, "phase": "tie_breaker"},
            "id=%s",
            (attempt["id"],),
        )
        attempt["phase"] = "tie_breaker"
        return {
            "outcome": "next_extra",
            "presentedQuestionId": present(db, attempt, "tie_breaker"),
        }
    else:
        direction = "up" if config["fractionalMode"] == "round_up" else "down"
        resolution = "configured_" + direction
    rounded = round_total(raw, direction)
    grade = grade_for_score(rounded, config["thresholds"])
    timestamp = now()
    db.update(
        "classic_exam_attempts",
        {
            "status": "completed",
            "phase": "completed",
            "completed_at": timestamp,
            "published_at": timestamp,
            "raw_total": raw,
            "rounded_total": rounded,
            "max_score": maximum,
            "calculated_grade": grade,
            "resolution_type": resolution,
            "current_presented_question_id": None,
        },
        "id=%s",
        (attempt["id"],),
    )
    attempt["status"] = "completed"
    attempt["phase"] = "completed"
    attempt["current_presented_question_id"] = None
    invalidate_rating(db)
    return {"outcome": "completed", "attemptId": attempt["id"]}


def vote(db, attempt, payload, actor):
    fields(
        payload,
        ("presentedQuestionId", "roundId", "value"),
        ("presentedQuestionId", "roundId", "value"),
    )
    presented_id = integer(payload["presentedQuestionId"], "presented_question_id")
    round_id = integer(payload["roundId"], "round_id")
    value = vote_value(payload["value"])
    if attempt["status"] != "in_progress":
        fail("attempt_not_active", status=422)
    if presented_id != attempt["current_presented_question_id"]:
        fail("question_not_current", "Обновите вопрос", 409)
    question = db.one(
        "SELECT * FROM classic_exam_presented_questions WHERE id=%s AND attempt_id=%s",
        (presented_id, attempt["id"]),
    )
    if question["status"] != "open":
        fail("question_already_resolved", status=409)
    round_row = db.one(
        "SELECT * FROM classic_exam_vote_rounds WHERE presented_question_id=%s AND attempt_id=%s ORDER BY round_no DESC LIMIT 1",
        (presented_id, attempt["id"]),
    )
    if not round_row or round_row["id"] != round_id or round_row["status"] != "open":
        fail("round_not_current", "Раунд изменился. Обновите состояние.", 409)
    if db.one(
        "SELECT id FROM classic_exam_votes WHERE round_id=%s AND examinator_id=%s",
        (round_id, actor["id"]),
    ):
        fail("vote_already_cast", "Ваш голос уже записан", 409)
    db.insert(
        "classic_exam_votes",
        {"round_id": round_id, "examinator_id": actor["id"], "value": value},
    )
    votes = db.all(
        "SELECT value FROM classic_exam_votes WHERE round_id=%s", (round_id,)
    )
    required = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_attempt_members WHERE attempt_id=%s",
        (attempt["id"],),
    )
    receipt = {
        "outcome": "vote_recorded",
        "presentedQuestionId": presented_id,
        "roundId": round_id,
    }
    if len(votes) < required:
        return receipt
    if len({r["value"] for r in votes}) > 1:
        db.update(
            "classic_exam_vote_rounds",
            {"status": "disputed", "completed_at": now()},
            "id=%s",
            (round_id,),
        )
        new_round = db.insert(
            "classic_exam_vote_rounds",
            {
                "attempt_id": attempt["id"],
                "presented_question_id": presented_id,
                "round_no": round_row["round_no"] + 1,
            },
        )
        return dict(receipt, outcome="disputed", nextRoundId=new_round)
    consensus = votes[0]["value"]
    db.update(
        "classic_exam_vote_rounds",
        {"status": "consensus", "consensus_value": consensus, "completed_at": now()},
        "id=%s",
        (round_id,),
    )
    part = db.one(
        """SELECT p.* FROM classic_exam_attempt_part_progress p JOIN classic_exam_definition_questions q ON q.source_part_id=p.source_part_id
        WHERE p.attempt_id=%s AND q.id=%s""",
        (attempt["id"], question["definition_question_id"]),
    )
    score = (
        calculate_question_score(consensus, part["question_weight"])
        if question["purpose"] == "regular"
        else Decimal(0)
    )
    db.update(
        "classic_exam_presented_questions",
        {
            "status": "consensus",
            "consensus_value": consensus,
            "weighted_score": score,
            "consensus_at": now(),
        },
        "id=%s",
        (presented_id,),
    )
    receipt.update(
        outcome="consensus", resolvedQuestionId=presented_id, consensus=consensus
    )
    if question["purpose"] == "regular":
        db.execute(
            "UPDATE classic_exam_attempt_part_progress SET consensus_count=consensus_count+1 WHERE attempt_id=%s AND source_part_id=%s",
            (attempt["id"], part["source_part_id"]),
        )
        remaining = db.scalar(
            "SELECT SUM(required_count-consensus_count) FROM classic_exam_attempt_part_progress WHERE attempt_id=%s",
            (attempt["id"],),
        )
        if remaining == 0:
            receipt.update(finish_or_extra(db, attempt))
    else:
        receipt.update(finish_or_extra(db, attempt, consensus))
    return receipt


def command(db, attempt_id, name, payload, actor, key):
    attempt = lock_attempt(db, attempt_id, actor, exclusive_exam=name == "start")
    previous, request_hash = receipt_lookup(db, attempt_id, actor, key, name, payload)
    if previous:
        return {
            "receipt": previous,
            "replayed": True,
            "attempt": attempt_state(db, attempt_id, actor),
        }
    changed = True
    if name == "ready":
        fields(payload, ())
        if attempt["status"] != "pending_ready":
            fail("attempt_not_pending", status=409)
        member = db.one(
            "SELECT * FROM classic_exam_attempt_members WHERE attempt_id=%s AND examinator_id=%s",
            (attempt_id, actor["id"]),
        )
        changed = member["ready_at"] is None
        if changed:
            assert_prepare(db, attempt["exam_id"], attempt["assignment_id"])
            db.execute(
                "UPDATE classic_exam_attempt_members SET ready_at=%s WHERE attempt_id=%s AND examinator_id=%s",
                (now(), attempt_id, actor["id"]),
            )
        receipt = {"outcome": "ready" if changed else "already_ready"}
    elif name == "start":
        fields(payload, ("expectedStateVersion",), ("expectedStateVersion",))
        integer(payload["expectedStateVersion"], "state_version")
        if attempt["status"] != "pending_ready":
            changed = False
            receipt = {"outcome": "already_started"}
        else:
            check_version(
                attempt, payload["expectedStateVersion"], "stale_state", "state_version"
            )
            members = member_rows(db, attempt_id)
            if not members or not all(m["ready_at"] for m in members):
                fail(
                    "commission_not_ready",
                    "Все участники должны подтвердить готовность",
                    422,
                )
            assert_prepare(db, attempt["exam_id"], attempt["assignment_id"], True)
            definition_id = make_definition(db, attempt["exam_id"])
            limit = admin.get_privilege(db, attempt["exam_id"], attempt["student_id"])[
                "replacementLimit"
            ]
            db.update(
                "classic_exam_attempts",
                {
                    "definition_id": definition_id,
                    "replacement_limit_snapshot": limit,
                    "started_at": now(),
                    "status": "in_progress",
                    "phase": "regular_questions",
                },
                "id=%s",
                (attempt_id,),
            )
            attempt.update(
                definition_id=definition_id,
                replacement_limit_snapshot=limit,
                status="in_progress",
                phase="regular_questions",
            )
            config = definition_config(db, attempt)
            for part in config["parts"]:
                db.insert(
                    "classic_exam_attempt_part_progress",
                    {
                        "attempt_id": attempt_id,
                        "source_part_id": part["sourcePartId"],
                        "part_code": part["code"],
                        "question_weight": part["weight"],
                        "required_count": part["quota"],
                    },
                )
            receipt = {
                "outcome": "started",
                "presentedQuestionId": present(db, attempt, "regular"),
            }
    elif name == "vote":
        receipt = vote(db, attempt, payload, actor)
    elif name in ("next-question", "replace-question"):
        fields(
            payload,
            ("presentedQuestionId", "expectedStateVersion"),
            ("presentedQuestionId", "expectedStateVersion"),
        )
        check_version(
            attempt, payload["expectedStateVersion"], "stale_state", "state_version"
        )
        if attempt["status"] != "in_progress":
            fail("attempt_not_active", status=422)
        current_id = integer(payload["presentedQuestionId"], "presented_question_id")
        if current_id != attempt["current_presented_question_id"]:
            fail("question_not_current", status=409)
        question = db.one(
            "SELECT * FROM classic_exam_presented_questions WHERE id=%s AND attempt_id=%s",
            (current_id, attempt_id),
        )
        if name == "next-question":
            if question["status"] != "consensus" or question["purpose"] != "regular":
                fail("question_not_resolved", status=409)
            if not db.scalar(
                "SELECT SUM(required_count-consensus_count) FROM classic_exam_attempt_part_progress WHERE attempt_id=%s",
                (attempt_id,),
            ):
                fail("no_regular_questions_remaining", status=409)
            receipt = {
                "outcome": "next_question",
                "presentedQuestionId": present(db, attempt, "regular"),
            }
        else:
            if question["status"] != "open":
                fail("question_already_resolved", status=409)
            votes = db.scalar(
                "SELECT COUNT(*) FROM classic_exam_votes v JOIN classic_exam_vote_rounds r ON r.id=v.round_id WHERE r.presented_question_id=%s",
                (current_id,),
            )
            if votes:
                fail(
                    "replacement_after_vote",
                    "Вопрос уже оценён хотя бы одним экзаменатором",
                    409,
                )
            if attempt["replacement_used"] >= attempt["replacement_limit_snapshot"]:
                fail("replacement_limit_exhausted", status=422)
            selected = choose(db, attempt, question["purpose"], question)
            db.update(
                "classic_exam_presented_questions",
                {
                    "status": "replaced",
                    "replaced_at": now(),
                    "replaced_by_examinator_id": actor["id"],
                },
                "id=%s",
                (current_id,),
            )
            db.execute(
                "UPDATE classic_exam_vote_rounds SET status='voided',completed_at=%s WHERE presented_question_id=%s AND status='open'",
                (now(), current_id),
            )
            db.execute(
                "UPDATE classic_exam_attempt_question_usage SET permanently_excluded=TRUE WHERE attempt_id=%s AND definition_question_id=%s",
                (attempt_id, question["definition_question_id"]),
            )
            db.execute(
                "UPDATE classic_exam_attempts SET replacement_used=replacement_used+1 WHERE id=%s",
                (attempt_id,),
            )
            receipt = {
                "outcome": "replaced",
                "replacedQuestionId": current_id,
                "presentedQuestionId": present(
                    db, attempt, question["purpose"], selected, current_id
                ),
            }
    else:
        fail("unknown_command", status=404)
    version = attempt["state_version"] + (1 if changed else 0)
    if changed:
        db.execute(
            "UPDATE classic_exam_attempts SET state_version=%s WHERE id=%s",
            (version, attempt_id),
        )
    receipt = store_receipt(
        db,
        attempt_id,
        actor,
        key,
        name,
        request_hash,
        dict(receipt, appliedStateVersion=version),
    )
    return {
        "receipt": receipt,
        "replayed": False,
        "attempt": attempt_state(db, attempt_id, actor),
    }


def examiner_students(db, exam_id, args, actor):
    lock_exam(db, exam_id, "classic", True)
    if not db.one(
        """SELECT a.id FROM classic_exam_assignments a JOIN classic_exam_assignment_members m ON m.assignment_id=a.id
        WHERE a.exam_id=%s AND m.examinator_id=%s LIMIT 1""",
        (exam_id, actor["id"]),
    ):
        fail("exam_not_found", status=404)
    result = admin.list_assignments(db, exam_id, args, actor["id"])
    bundle = admin.readiness_bundles(db, [exam_id])[exam_id]
    examiner_ids = sorted({m["id"] for a in result["items"] for m in a["members"]})
    bundle["validExaminatorIds"] = set()
    if examiner_ids:
        markers = ",".join(["%s"] * len(examiner_ids))
        bundle["validExaminatorIds"] = {
            r["ref_id"]
            for r in db.all(
                f"SELECT ref_id FROM auth_users WHERE role='examinator' AND ref_id IN ({markers})",
                examiner_ids,
            )
        }
    attempt_ids = [a["attemptId"] for a in result["items"] if a["attemptId"]]
    member_map = {i: [] for i in attempt_ids}
    progress_map = {i: [] for i in attempt_ids}
    if attempt_ids:
        markers = ",".join(["%s"] * len(attempt_ids))
        for member in db.all(
            f"SELECT * FROM classic_exam_attempt_members WHERE attempt_id IN ({markers}) ORDER BY attempt_id,position",
            attempt_ids,
        ):
            member_map[member["attempt_id"]].append(member)
        for part in db.all(
            f"SELECT * FROM classic_exam_attempt_part_progress WHERE attempt_id IN ({markers})",
            attempt_ids,
        ):
            progress_map[part["attempt_id"]].append(part)
    rows = []
    for assignment in result["items"]:
        attempt_id = assignment["attemptId"]
        members = member_map.get(attempt_id, [])
        ready_ids = {m["examinator_id"] for m in members if m["ready_at"]}
        assignment["allMembersReady"] = bool(members) and len(ready_ids) == len(members)
        report = (
            admin.readiness(
                db,
                exam_id,
                assignment["id"],
                True,
                bundle=bundle,
                assignment_data=assignment,
            )
            if assignment["status"] in ("not_started", "pending_ready")
            else None
        )
        progress = progress_map.get(attempt_id, [])
        rows.append(
            {
                "assignmentId": assignment["id"],
                "attemptNo": assignment["attemptNo"],
                "student": assignment["student"],
                "commission": [
                    dict(m, ready=m["id"] in ready_ids) for m in assignment["members"]
                ],
                "replacementLimit": assignment["replacementLimit"],
                "status": assignment["status"],
                "phase": assignment["phase"],
                "attemptId": attempt_id,
                "progress": {
                    "answered": sum(p["consensus_count"] for p in progress),
                    "required": sum(p["required_count"] for p in progress),
                },
                "historyGeneration": assignment["historyGeneration"],
                "canPrepare": bool(report and report["canPrepare"]),
                "canStart": bool(
                    report
                    and report["canStartNow"]
                    and members
                    and len(ready_ids) == len(members)
                ),
                "unavailableReasons": report["errors"] if report else [],
            }
        )
    return {"items": rows, "pagination": result["pagination"]}


def examiner_exams(db, args, actor):
    page, limit, offset = page_args(args)
    state = enum_value(
        args.get("state", "all"),
        ("all", "incomplete", "upcoming", "active", "ended"),
        "state",
    )
    where = [
        "e.exam_type='classic'",
        "EXISTS(SELECT 1 FROM classic_exam_assignments a JOIN classic_exam_assignment_members m ON m.assignment_id=a.id WHERE a.exam_id=e.id AND m.examinator_id=%s)",
    ]
    params = [actor["id"]]
    if args.get("search"):
        where.append("d.name LIKE %s ESCAPE '!'")
        params.append(search_value(args))
    timestamp = now()
    if state == "incomplete":
        where.append("(s.start_at IS NULL OR s.end_at IS NULL)")
    elif state == "upcoming":
        where.append("s.start_at>%s AND s.end_at IS NOT NULL")
        params.append(timestamp)
    elif state == "active":
        where.append("s.start_at<=%s AND s.end_at>=%s")
        params.extend([timestamp, timestamp])
    elif state == "ended":
        where.append("s.end_at<%s")
        params.append(timestamp)
    joins = (
        "FROM exams e JOIN directions d ON d.id=e.direction_id JOIN classic_exam_settings s ON s.exam_id=e.id WHERE "
        + " AND ".join(where)
    )
    total = db.scalar("SELECT COUNT(*) " + joins, params)
    exams = db.all(
        "SELECT e.id,e.direction_id,d.name,s.start_at,s.end_at "
        + joins
        + " ORDER BY s.start_at DESC,e.id DESC LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    exam_ids = [e["id"] for e in exams]
    bundles = admin.readiness_bundles(db, exam_ids)
    counter_map = {i: {} for i in exam_ids}
    student_map = {i: 0 for i in exam_ids}
    reserve_map = {i: 0 for i in exam_ids}
    if exam_ids:
        markers = ",".join(["%s"] * len(exam_ids))
        where = f"""a.exam_id IN ({markers}) AND EXISTS(SELECT 1 FROM classic_exam_assignment_members m
            WHERE m.assignment_id=a.id AND m.examinator_id=%s)"""
        params = exam_ids + [actor["id"]]
        for row in db.all(
            """SELECT a.exam_id,COALESCE(t.status,'not_started') status,COUNT(*) n FROM classic_exam_assignments a
            LEFT JOIN classic_exam_attempts t ON t.assignment_id=a.id WHERE """
            + where
            + " GROUP BY a.exam_id,COALESCE(t.status,'not_started')",
            params,
        ):
            counter_map[row["exam_id"]][row["status"]] = row["n"]
        for row in db.all(
            """SELECT a.exam_id,COUNT(DISTINCT a.student_id) students,MAX(COALESCE(p.replacement_limit,0)) max_reserve
            FROM classic_exam_assignments a LEFT JOIN classic_exam_student_privileges p ON p.exam_id=a.exam_id AND p.student_id=a.student_id
            WHERE """
            + where
            + " GROUP BY a.exam_id",
            params,
        ):
            student_map[row["exam_id"]] = row["students"]
            reserve_map[row["exam_id"]] = row["max_reserve"]
    rows = []
    for e in exams:
        counts = counter_map[e["id"]]
        students = student_map[e["id"]]
        bundle = bundles[e["id"]]
        report = admin.readiness(db, e["id"], bundle=bundle)
        reserve = reserve_map[e["id"]]
        unavailable = not report["isConfigured"] or any(
            p["bank_size"] < (p["question_count"] or 0) + reserve
            for p in bundle["parts"]
        )
        config = bundle["config"]
        if config["fractional_mode"] == "extra_question":
            pool = (
                bundle["parts"]
                if config["tie_breaker_source_mode"] == "any_part"
                else [
                    p
                    for p in bundle["parts"]
                    if p["id"] == config["tie_breaker_part_id"]
                ]
            )
            unavailable = unavailable or sum(p["bank_size"] for p in pool) < reserve + 2
        rows.append(
            {
                "id": e["id"],
                "directionId": e["direction_id"],
                "directionName": e["name"],
                "startAt": iso(e["start_at"]),
                "endAt": iso(e["end_at"]),
                "readiness": "ready" if report["isConfigured"] else "incomplete",
                "assignedStudentsCount": students,
                "assignments": {
                    "total": sum(counts.values()),
                    "notStarted": counts.get("not_started", 0),
                    "pendingReady": counts.get("pending_ready", 0),
                    "inProgress": counts.get("in_progress", 0),
                    "completed": counts.get("completed", 0),
                },
                "hasUnavailableAssignments": unavailable,
            }
        )
    return {"items": rows, "pagination": pagination(total, page, limit)}
