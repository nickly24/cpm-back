"""Published results, immutable paged protocols, appeals and physical deletion.

All mutations use the caller's UnitOfWork and transaction. No method commits or
loads production credentials. Student serialization is an explicit allowlist.
"""

from .common import (
    fail,
    fields,
    integer,
    query_int,
    enum_value,
    page_args,
    pagination,
    search_value,
    iso,
    now,
    loads,
    digest,
    lock_exam,
    check_version,
    invalidate_rating,
    confirmation_token,
    verify_confirmation,
)

RESULT_SELECT = """SELECT a.*,COALESCE(ap.new_grade,a.calculated_grade) effective_grade,
    ap.id latest_appeal_id FROM classic_exam_attempts a
    LEFT JOIN classic_exam_appeals ap ON ap.id=(
        SELECT MAX(aa.id) FROM classic_exam_appeals aa WHERE aa.attempt_id=a.id)
"""
CURRENT_CLAUSE = """a.status='completed' AND NOT EXISTS(
    SELECT 1 FROM classic_exam_attempts newer
    WHERE newer.exam_id=a.exam_id AND newer.student_id=a.student_id
      AND newer.status='completed' AND newer.attempt_no>a.attempt_no)
"""
ADMIN_ROLES = ("admin", "staff_admin")


def _classic(db, exam_id):
    row = db.one("SELECT * FROM exams WHERE id=%s AND exam_type='classic'", (exam_id,))
    if not row:
        fail("exam_not_found", "Экзамен не найден", 404)
    return row


def _attempt(db, attempt_id, actor=None, exam_id=None, locked=False):
    params = [attempt_id]
    where = "a.id=%s"
    if exam_id is not None:
        where += " AND a.exam_id=%s"
        params.append(exam_id)
    row = db.one(
        RESULT_SELECT + " WHERE " + where + (" FOR UPDATE" if locked else ""), params
    )
    if not row:
        fail("attempt_not_found", "Сдача не найдена", 404)
    if actor:
        if actor["role"] == "student":
            if row["student_id"] != actor["id"] or row["status"] != "completed":
                fail("attempt_not_found", "Сдача не найдена", 404)
        elif actor["role"] == "examinator":
            if not db.one(
                "SELECT 1 FROM classic_exam_attempt_members WHERE attempt_id=%s AND examinator_id=%s",
                (attempt_id, actor["id"]),
            ):
                fail("attempt_not_found", "Сдача не найдена", 404)
        elif actor["role"] not in ADMIN_ROLES:
            fail("forbidden", "Недостаточно прав", 403)
    return row


def _members(db, attempt_ids):
    result = {attempt_id: [] for attempt_id in attempt_ids}
    if not attempt_ids:
        return result
    placeholders = ",".join(["%s"] * len(attempt_ids))
    for row in db.all(
        f"""SELECT attempt_id,examinator_id,full_name_snapshot,position,ready_at
            FROM classic_exam_attempt_members WHERE attempt_id IN ({placeholders})
            ORDER BY attempt_id,position""",
        list(attempt_ids),
    ):
        result[row["attempt_id"]].append(
            {
                "id": row["examinator_id"],
                "fullName": row["full_name_snapshot"],
                "position": row["position"],
                "ready": row["ready_at"] is not None,
                "readyAt": iso(row["ready_at"]),
            }
        )
    return result


def effective_result(db, exam_id, student_id):
    """Current completed attempt: completed retake wins even when it is worse."""
    return db.one(
        RESULT_SELECT + " WHERE a.exam_id=%s AND a.student_id=%s AND " + CURRENT_CLAUSE,
        (exam_id, student_id),
    )


def result_summary(db, attempt, members=None, student=False):
    if not attempt or attempt["status"] != "completed":
        return None
    if "effective_grade" not in attempt:
        attempt = _attempt(db, attempt["id"])
    if members is None:
        members = _members(db, [attempt["id"]])[attempt["id"]]
    result = {
        "attemptId": attempt["id"],
        "attemptNo": attempt["attempt_no"],
        "rawTotal": attempt["raw_total"],
        "roundedTotal": attempt["rounded_total"],
        "maxScore": attempt["max_score"],
        "grade": int(attempt["effective_grade"]),
        "hasAppeal": attempt["latest_appeal_id"] is not None,
        "completedAt": iso(attempt["completed_at"]),
        "commission": [
            {"id": item["id"], "fullName": item["fullName"]} for item in members
        ],
    }
    if not student:
        result.update(
            {
                "calculatedGrade": attempt["calculated_grade"],
                "effectiveGrade": int(attempt["effective_grade"]),
                "resultVersion": attempt["result_version"],
                "resolutionType": attempt["resolution_type"],
                "publishedAt": iso(attempt["published_at"]),
            }
        )
    return result


def _admin_result_row(row):
    return {
        "student": {"id": row["student_id"], "fullName": row["student_name_snapshot"]},
        "currentAttemptId": row["id"],
        "currentAttemptNo": row["attempt_no"],
        "rawTotal": row["raw_total"],
        "roundedTotal": row["rounded_total"],
        "maxScore": row["max_score"],
        "calculatedGrade": row["calculated_grade"],
        "effectiveGrade": int(row["effective_grade"]),
        "hasAppeal": row["latest_appeal_id"] is not None,
        "resultVersion": row["result_version"],
        "historyGeneration": row.get("history_generation"),
        "firstAttemptId": row.get("first_attempt_id"),
        "retakeAssignmentId": row.get("retake_assignment_id"),
        "retakeStatus": row.get("retake_status"),
    }


def list_results(db, exam_id, args):
    _classic(db, exam_id)
    page, limit, offset = page_args(args)
    where, params = ["a.exam_id=%s", CURRENT_CLAUSE], [exam_id]
    if args.get("search"):
        where.append(
            "(a.student_name_snapshot LIKE %s ESCAPE '!' OR CAST(a.student_id AS CHAR)=%s)"
        )
        params.extend((search_value(args), args["search"]))
    if args.get("grade") not in (None, ""):
        where.append("COALESCE(ap.new_grade,a.calculated_grade)=%s")
        params.append(query_int(args["grade"], "grade", minimum=0, maximum=5))
    if args.get("hasAppeal") not in (None, ""):
        value = enum_value(args["hasAppeal"], ("true", "false"), "has_appeal")
        where.append("ap.id IS " + ("NOT NULL" if value == "true" else "NULL"))
    base = RESULT_SELECT + " WHERE " + " AND ".join(where)
    total = db.scalar("SELECT COUNT(*) FROM (" + base + ") filtered", params)
    selected = db.all(
        base + " ORDER BY a.student_name_snapshot,a.student_id LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    students = [row["student_id"] for row in selected]
    metadata = {}
    if students:
        placeholders = ",".join(["%s"] * len(students))
        for row in db.all(
            f"""SELECT s.student_id,s.history_generation,f.id first_attempt_id,r.id retake_assignment_id,t.status retake_status
            FROM classic_exam_assignments s
            LEFT JOIN classic_exam_attempts f ON f.assignment_id=s.id
            LEFT JOIN classic_exam_assignments r ON r.exam_id=s.exam_id AND r.student_id=s.student_id AND r.attempt_no=2
            LEFT JOIN classic_exam_attempts t ON t.assignment_id=r.id
            WHERE s.exam_id=%s AND s.attempt_no=1 AND s.student_id IN ({placeholders})""",
            [exam_id] + students,
        ):
            metadata[row["student_id"]] = row
    for row in selected:
        row.update(metadata.get(row["student_id"], {}))
        if row.get("retake_assignment_id") and not row.get("retake_status"):
            row["retake_status"] = "not_started"
    return {
        "items": [_admin_result_row(row) for row in selected],
        "pagination": pagination(total, page, limit),
    }


def list_attempts(db, exam_id, args):
    _classic(db, exam_id)
    page, limit, offset = page_args(args)
    where, params = ["a.exam_id=%s"], [exam_id]
    if args.get("status"):
        where.append("a.status=%s")
        params.append(
            enum_value(
                args["status"], ("pending_ready", "in_progress", "completed"), "status"
            )
        )
    if args.get("attemptNo"):
        where.append("a.attempt_no=%s")
        params.append(query_int(args["attemptNo"], "attempt_no", maximum=2))
    if args.get("commissionId"):
        where.append(
            "EXISTS(SELECT 1 FROM classic_exam_assignments ca WHERE ca.id=a.assignment_id AND ca.source_commission_id=%s)"
        )
        params.append(query_int(args["commissionId"], "commission_id"))
    if args.get("search"):
        where.append(
            "(a.student_name_snapshot LIKE %s ESCAPE '!' OR CAST(a.student_id AS CHAR)=%s)"
        )
        params.extend((search_value(args), args["search"]))
    base = RESULT_SELECT + " WHERE " + " AND ".join(where)
    total = db.scalar("SELECT COUNT(*) FROM (" + base + ") filtered", params)
    selected = db.all(
        base
        + " ORDER BY a.student_name_snapshot,a.student_id,a.attempt_no LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    members = _members(db, [row["id"] for row in selected])
    return {
        "items": [
            {
                "id": row["id"],
                "examId": exam_id,
                "assignmentId": row["assignment_id"],
                "student": {
                    "id": row["student_id"],
                    "fullName": row["student_name_snapshot"],
                },
                "attemptNo": row["attempt_no"],
                "status": row["status"],
                "phase": row["phase"],
                "stateVersion": row["state_version"],
                "historyGeneration": row["history_generation"],
                "startedAt": iso(row["started_at"]),
                "completedAt": iso(row["completed_at"]),
                "members": members[row["id"]],
                "result": result_summary(db, row, members[row["id"]]),
            }
            for row in selected
        ],
        "pagination": pagination(total, page, limit),
    }


def attempt_detail(db, exam_id, attempt_id, actor):
    attempt = _attempt(db, attempt_id, actor, exam_id)
    definition = (
        db.one(
            "SELECT config_json FROM classic_exam_definition_versions WHERE id=%s",
            (attempt["definition_id"],),
        )
        if attempt["definition_id"]
        else None
    )
    members = _members(db, [attempt_id])[attempt_id]
    counts = db.one(
        """SELECT
        (SELECT COUNT(*) FROM classic_exam_presented_questions WHERE attempt_id=%s) questions,
        (SELECT COUNT(*) FROM classic_exam_vote_rounds WHERE attempt_id=%s) rounds,
        (SELECT COUNT(*) FROM classic_exam_appeals WHERE attempt_id=%s) appeals""",
        (attempt_id, attempt_id, attempt_id),
    )
    result = {
        "id": attempt_id,
        "examId": exam_id,
        "assignmentId": attempt["assignment_id"],
        "student": {
            "id": attempt["student_id"],
            "fullName": attempt["student_name_snapshot"],
        },
        "attemptNo": attempt["attempt_no"],
        "status": attempt["status"],
        "phase": attempt["phase"],
        "stateVersion": attempt["state_version"],
        "historyGeneration": attempt["history_generation"],
        "startedAt": iso(attempt["started_at"]),
        "completedAt": iso(attempt["completed_at"]),
        "members": members,
        "definition": loads(definition["config_json"]) if definition else None,
        "progress": db.all(
            "SELECT source_part_id,part_code,required_count,consensus_count FROM classic_exam_attempt_part_progress WHERE attempt_id=%s ORDER BY source_part_id",
            (attempt_id,),
        ),
        "result": result_summary(db, attempt, members),
        "counts": counts,
    }
    # Definition contains rules only; the question bank is stored in another table.
    return result


def _definition_weights(db, definition_id):
    row = db.one(
        "SELECT config_json FROM classic_exam_definition_versions WHERE id=%s",
        (definition_id,),
    )
    if not row:
        return {}
    return {
        part.get("sourcePartId", part.get("id")): part.get(
            "weight", part.get("question_weight")
        )
        for part in loads(row["config_json"]).get("parts", [])
    }


def questions(db, attempt_id, args, actor, exam_id=None):
    attempt = _attempt(db, attempt_id, actor, exam_id)
    page, limit, offset = page_args(args, 10, 20)
    upper = db.scalar(
        "SELECT COALESCE(MAX(sequence_no),0) FROM classic_exam_presented_questions WHERE attempt_id=%s",
        (attempt_id,),
    )
    anchor = query_int(
        args.get("throughSequenceNo"), "through_sequence_no", upper, minimum=0
    )
    anchor = min(anchor, upper)
    total = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_presented_questions WHERE attempt_id=%s AND sequence_no<=%s",
        (attempt_id, anchor),
    )
    selected = db.all(
        """SELECT p.*,q.source_part_id,q.part_code,q.question_text,q.answer_text,
        (SELECT COUNT(*) FROM classic_exam_vote_rounds r WHERE r.presented_question_id=p.id AND r.status<>'open') completed_rounds
        FROM classic_exam_presented_questions p JOIN classic_exam_definition_questions q ON q.id=p.definition_question_id
        WHERE p.attempt_id=%s AND p.sequence_no<=%s ORDER BY p.sequence_no,p.id LIMIT %s OFFSET %s""",
        (attempt_id, anchor, limit, offset),
    )
    weights = _definition_weights(db, attempt["definition_id"])
    items = []
    for row in selected:
        item = {
            "id": row["id"],
            "sequenceNo": row["sequence_no"],
            "purpose": row["purpose"],
            "partCode": row["part_code"],
            "weight": weights.get(row["source_part_id"]),
            "questionText": row["question_text"],
            "answerText": row["answer_text"],
            "cycleNo": row["cycle_no"],
            "status": row["status"],
            "consensus": row["consensus_value"],
            "awardedPoints": row["weighted_score"] or 0,
            "replacesPresentedQuestionId": row["replaces_presented_question_id"],
        }
        if actor["role"] != "student":
            item.update(
                {
                    "completedRoundsCount": row["completed_rounds"],
                    "createdAt": iso(row["created_at"]),
                    "replacedAt": iso(row["replaced_at"]),
                    "replacedByExaminatorId": row["replaced_by_examinator_id"],
                }
            )
        items.append(item)
    return {
        "items": items,
        "pagination": pagination(total, page, limit),
        "throughSequenceNo": anchor,
    }


def rounds(db, attempt_id, presented_id, args, actor, exam_id=None):
    _attempt(db, attempt_id, actor, exam_id)
    if actor["role"] == "student":
        fail("forbidden", "Персональные голоса комиссии недоступны", 403)
    if not db.one(
        "SELECT id FROM classic_exam_presented_questions WHERE id=%s AND attempt_id=%s",
        (presented_id, attempt_id),
    ):
        fail("question_not_found", "Вопрос не найден", 404)
    page, limit, offset = page_args(args)
    upper = db.scalar(
        "SELECT COALESCE(MAX(round_no),0) FROM classic_exam_vote_rounds WHERE presented_question_id=%s",
        (presented_id,),
    )
    anchor = min(
        query_int(args.get("throughRoundNo"), "through_round_no", upper, minimum=0),
        upper,
    )
    total = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_vote_rounds WHERE presented_question_id=%s AND round_no<=%s",
        (presented_id, anchor),
    )
    selected = db.all(
        "SELECT * FROM classic_exam_vote_rounds WHERE presented_question_id=%s AND round_no<=%s ORDER BY round_no,id LIMIT %s OFFSET %s",
        (presented_id, anchor, limit, offset),
    )
    vote_map = {row["id"]: [] for row in selected}
    if selected:
        placeholders = ",".join(["%s"] * len(selected))
        for vote in db.all(
            f"""SELECT v.*,m.full_name_snapshot,r.status round_status FROM classic_exam_votes v
            JOIN classic_exam_vote_rounds r ON r.id=v.round_id
            JOIN classic_exam_attempt_members m ON m.attempt_id=r.attempt_id AND m.examinator_id=v.examinator_id
            WHERE v.round_id IN ({placeholders}) ORDER BY v.round_id,m.position""",
            list(vote_map),
        ):
            # Do not serialize other members' votes until their round closes.
            if (
                actor["role"] in ADMIN_ROLES
                or vote["round_status"] != "open"
                or vote["examinator_id"] == actor["id"]
            ):
                vote_map[vote["round_id"]].append(
                    {
                        "examinatorId": vote["examinator_id"],
                        "fullName": vote["full_name_snapshot"],
                        "value": vote["value"],
                        "votedAt": iso(vote["created_at"]),
                    }
                )
    return {
        "items": [
            {
                "id": row["id"],
                "roundNo": row["round_no"],
                "status": row["status"],
                "consensus": row["consensus_value"],
                "openedAt": iso(row["opened_at"]),
                "completedAt": iso(row["completed_at"]),
                "votes": vote_map[row["id"]],
            }
            for row in selected
        ],
        "pagination": pagination(total, page, limit),
        "throughRoundNo": anchor,
    }


def _appeal_dto(row):
    return {
        "id": row["id"],
        "previousGrade": row["previous_grade"],
        "newGrade": row["new_grade"],
        "changedBy": {
            "id": row["changed_by_admin_id"],
            "role": row["changed_by_role"],
            "fullName": row["admin_name_snapshot"],
        },
        "createdAt": iso(row["created_at"]),
    }


def appeals(db, exam_id, attempt_id, args):
    _attempt(db, attempt_id, exam_id=exam_id)
    page, limit, offset = page_args(args)
    total = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_appeals WHERE attempt_id=%s", (attempt_id,)
    )
    selected = db.all(
        "SELECT * FROM classic_exam_appeals WHERE attempt_id=%s ORDER BY id LIMIT %s OFFSET %s",
        (attempt_id, limit, offset),
    )
    return {
        "items": [_appeal_dto(row) for row in selected],
        "pagination": pagination(total, page, limit),
    }


def appeal(db, exam_id, attempt_id, payload, actor):
    fields(
        payload, ("grade", "expectedResultVersion"), ("grade", "expectedResultVersion")
    )
    grade = integer(payload["grade"], "grade", 0, 5)
    lock_exam(db, exam_id, "classic", shared=True)
    row = _attempt(db, attempt_id, actor, exam_id, locked=True)
    if row["status"] != "completed":
        fail("attempt_not_completed", "Апелляция доступна после завершения сдачи", 422)
    check_version(
        row, payload["expectedResultVersion"], "result_modified", "result_version"
    )
    if actor["role"] not in ADMIN_ROLES:
        fail("forbidden", status=403)
    admin_table = "admins" if actor["role"] == "admin" else "admin_role_users"
    admin_name = db.scalar(
        f"SELECT full_name FROM {admin_table} WHERE id=%s", (actor["id"],), default=None
    )
    if admin_name is None:
        fail("actor_not_found", "Учётная запись администратора недоступна", 403)
    appeal_id = db.insert(
        "classic_exam_appeals",
        {
            "attempt_id": attempt_id,
            "previous_grade": row["effective_grade"],
            "new_grade": grade,
            "changed_by_role": actor["role"],
            "changed_by_admin_id": actor["id"],
            "admin_name_snapshot": admin_name,
        },
    )
    # Completed examiner state includes the effective grade. Advance its state
    # version too, so a pre-appeal response cannot overwrite a newer grade.
    db.update(
        "classic_exam_attempts",
        {
            "result_version": row["result_version"] + 1,
            "state_version": row["state_version"] + 1,
        },
        "id=%s",
        (attempt_id,),
    )
    current = effective_result(db, exam_id, row["student_id"])
    is_current = current["id"] == attempt_id
    if is_current:
        invalidate_rating(db)
    return {
        "appeal": _appeal_dto(
            db.one("SELECT * FROM classic_exam_appeals WHERE id=%s", (appeal_id,))
        ),
        "effectiveGrade": grade,
        "hasAppeal": True,
        "resultVersion": row["result_version"] + 1,
        "isCurrentAttempt": is_current,
    }


def retake(db, exam_id, student_id, payload, actor):
    from .admin import get_commission, copy_assignment, get_assignment

    fields(
        payload,
        ("commissionId", "expectedHistoryGeneration"),
        ("commissionId", "expectedHistoryGeneration"),
    )
    commission_id = integer(payload["commissionId"], "commission_id")
    generation = integer(payload["expectedHistoryGeneration"], "history_generation")
    lock_exam(db, exam_id, "classic")
    first = db.one(
        "SELECT * FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s AND attempt_no=1 FOR UPDATE",
        (exam_id, student_id),
    )
    if not first:
        fail(
            "first_attempt_not_completed",
            "Сначала должна быть завершена первая сдача",
            422,
        )
    check_version(first, generation, "history_generation_changed", "history_generation")
    if not db.one(
        "SELECT id FROM classic_exam_attempts WHERE assignment_id=%s AND status='completed'",
        (first["id"],),
    ):
        fail(
            "first_attempt_not_completed",
            "Сначала должна быть завершена первая сдача",
            422,
        )
    if db.one(
        "SELECT id FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s AND attempt_no=2",
        (exam_id, student_id),
    ):
        fail("retake_already_assigned", "Пересдача уже назначена", 409)
    commission = get_commission(db, exam_id, commission_id)
    previous = {
        row["examinator_id"]
        for row in db.all(
            "SELECT examinator_id FROM classic_exam_assignment_members WHERE assignment_id=%s",
            (first["id"],),
        )
    }
    if previous == {row["id"] for row in commission["members"]}:
        fail(
            "retake_commission_unchanged",
            "Для пересдачи требуется другой состав комиссии",
            422,
        )
    if not 1 <= len(commission["members"]) <= 6:
        fail(
            "invalid_commission_size",
            "В комиссии должно быть от 1 до 6 экзаменаторов",
            422,
        )
    assignment_id = copy_assignment(
        db, exam_id, student_id, commission, actor, 2, generation
    )
    return {"assignment": get_assignment(db, exam_id, assignment_id)}


def _history_snapshot(db, exam_id, student_id, locked=False):
    assignments = db.all(
        "SELECT id,attempt_no,version,history_generation FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s ORDER BY attempt_no"
        + (" FOR UPDATE" if locked else ""),
        (exam_id, student_id),
    )
    attempts = db.all(
        "SELECT id,state_version,result_version,status FROM classic_exam_attempts WHERE exam_id=%s AND student_id=%s ORDER BY id"
        + (" FOR UPDATE" if locked else ""),
        (exam_id, student_id),
    )
    return {
        "examId": exam_id,
        "studentId": student_id,
        "assignments": assignments,
        "attempts": attempts,
    }


def _attempt_counts(db, exam_id, student_id=None):
    where, params = "a.exam_id=%s", [exam_id]
    if student_id is not None:
        where += " AND a.student_id=%s"
        params.append(student_id)
    return db.one(
        f"""SELECT COUNT(*) attempts,
        COALESCE(SUM((SELECT COUNT(*) FROM classic_exam_presented_questions p WHERE p.attempt_id=a.id)),0) questions,
        COALESCE(SUM((SELECT COUNT(*) FROM classic_exam_vote_rounds r WHERE r.attempt_id=a.id)),0) rounds,
        COALESCE(SUM((SELECT COUNT(*) FROM classic_exam_votes v JOIN classic_exam_vote_rounds r ON r.id=v.round_id WHERE r.attempt_id=a.id)),0) votes,
        COALESCE(SUM((SELECT COUNT(*) FROM classic_exam_appeals ap WHERE ap.attempt_id=a.id)),0) appeals
        FROM classic_exam_attempts a WHERE {where}""",
        params,
    )


def history_delete_preview(db, exam_id, student_id, actor):
    _classic(db, exam_id)
    student = db.one("SELECT id,full_name FROM students WHERE id=%s", (student_id,))
    if not student:
        fail("student_not_found", "Студент не найден", 404)
    snapshot = _history_snapshot(db, exam_id, student_id)
    first = next(
        (row for row in snapshot["assignments"] if row["attempt_no"] == 1), None
    )
    counts = _attempt_counts(db, exam_id, student_id)
    counts["retakeAssignments"] = sum(
        row["attempt_no"] == 2 for row in snapshot["assignments"]
    )
    current = effective_result(db, exam_id, student_id)
    return {
        "examId": exam_id,
        "student": {"id": student_id, "fullName": student["full_name"]},
        "counts": counts,
        "historyGeneration": first["history_generation"] if first else None,
        "currentResult": result_summary(db, current),
        "preserved": ["firstAssignment", "replacementPrivilege"],
        **confirmation_token(
            actor, ["studentHistory", exam_id, student_id], digest(snapshot)
        ),
    }


def delete_history(db, exam_id, student_id, actor):
    lock_exam(db, exam_id, "classic")
    snapshot = _history_snapshot(db, exam_id, student_id, locked=True)
    verify_confirmation(
        actor, ["studentHistory", exam_id, student_id], digest(snapshot)
    )
    second = [row for row in snapshot["assignments"] if row["attempt_no"] == 2]
    if not snapshot["attempts"] and not second:
        return
    db.execute(
        "DELETE FROM classic_exam_attempts WHERE exam_id=%s AND student_id=%s",
        (exam_id, student_id),
    )
    db.execute(
        "DELETE FROM classic_exam_assignments WHERE exam_id=%s AND student_id=%s AND attempt_no=2",
        (exam_id, student_id),
    )
    db.execute(
        "UPDATE classic_exam_assignments SET history_generation=history_generation+1,version=version+1 WHERE exam_id=%s AND student_id=%s AND attempt_no=1",
        (exam_id, student_id),
    )
    _purge_unused_definitions(db, exam_id)
    ids = [row["id"] for row in snapshot["attempts"]]
    where, params = "target_exam_id=%s AND target_student_id=%s", [exam_id, student_id]
    if ids:
        where = (
            "("
            + where
            + ") OR target_attempt_id IN ("
            + ",".join(["%s"] * len(ids))
            + ")"
        )
        params += ids
    db.execute(
        "UPDATE exam_admin_commands SET receipt=NULL,tombstone=1 WHERE " + where, params
    )
    invalidate_rating(db)


def _purge_unused_definitions(db, exam_id):
    db.execute(
        """DELETE d FROM classic_exam_definition_versions d
        LEFT JOIN classic_exam_attempts a ON a.definition_id=d.id
        WHERE d.exam_id=%s AND a.id IS NULL""",
        (exam_id,),
    )


def _exam_delete_snapshot(db, exam_id):
    exam = db.one("SELECT id,exam_type,version FROM exams WHERE id=%s", (exam_id,))
    if not exam:
        fail("exam_not_found", "Экзамен не найден", 404)
    result = {
        "exam": exam,
        "configVersion": db.scalar(
            "SELECT config_version FROM classic_exam_settings WHERE exam_id=%s",
            (exam_id,),
            None,
        ),
    }
    for name, table, columns in (
        ("attempts", "classic_exam_attempts", "id,state_version,result_version"),
        ("assignments", "classic_exam_assignments", "id,version,history_generation"),
        ("commissions", "classic_exam_commissions", "id,version"),
        ("privileges", "classic_exam_student_privileges", "student_id,version"),
        ("outsideResults", "exam_sessions", "id,version"),
        (
            "questionImports",
            "classic_exam_question_import_sessions",
            "id,preview_version,status",
        ),
        (
            "assignmentImports",
            "classic_exam_assignment_import_sessions",
            "id,preview_version,status",
        ),
        (
            "outsideImports",
            "outside_exam_result_import_sessions",
            "id,preview_version,status",
        ),
    ):
        result[name] = db.all(
            f"SELECT {columns} FROM {table} WHERE exam_id=%s ORDER BY 1", (exam_id,)
        )
    return result


def exam_delete_preview(db, exam_id, actor):
    snapshot = _exam_delete_snapshot(db, exam_id)
    counts = _attempt_counts(db, exam_id)
    counts.update(
        {
            name: len(snapshot[name])
            for name in ("assignments", "commissions", "privileges", "outsideResults")
        }
    )
    counts["importSessions"] = sum(
        len(snapshot[name])
        for name in ("questionImports", "assignmentImports", "outsideImports")
    )
    counts["bankQuestions"] = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_questions WHERE exam_id=%s", (exam_id,)
    )
    counts["parts"] = db.scalar(
        "SELECT COUNT(*) FROM classic_exam_parts WHERE exam_id=%s", (exam_id,)
    )
    return {
        "examId": exam_id,
        "counts": counts,
        **confirmation_token(actor, ["exam", exam_id], digest(snapshot)),
    }


def delete_exam(db, exam_id, actor):
    lock_exam(db, exam_id)
    snapshot = _exam_delete_snapshot(db, exam_id)
    verify_confirmation(actor, ["exam", exam_id], digest(snapshot))
    # Explicit ordering releases RESTRICT links from attempts and definition usage.
    db.execute("DELETE FROM classic_exam_attempts WHERE exam_id=%s", (exam_id,))
    _purge_unused_definitions(db, exam_id)
    # Additive phase deliberately has no legacy CASCADE FK yet.
    db.execute("DELETE FROM exam_sessions WHERE exam_id=%s", (exam_id,))
    db.execute("DELETE FROM exams WHERE id=%s", (exam_id,))
    db.execute(
        "UPDATE exam_admin_commands SET receipt=NULL,tombstone=1 WHERE target_exam_id=%s",
        (exam_id,),
    )
    invalidate_rating(db)


def _student_current(db, row, members):
    if row["exam_type"] == "outside_lms":
        return {
            "points": row["outside_points"],
            "grade": int(row["outside_grade"]),
            "examinator": row["examinator"],
            "hasAppeal": False,
        }
    attempt = {
        "id": row["attempt_id"],
        "status": "completed",
        "attempt_no": row["attempt_no"],
        "raw_total": row["raw_total"],
        "rounded_total": row["rounded_total"],
        "max_score": row["max_score"],
        "effective_grade": row["effective_grade"],
        "latest_appeal_id": row["latest_appeal_id"],
        "completed_at": row["completed_at"],
    }
    return result_summary(db, attempt, members, student=True)


STUDENT_SELECT = (
    """SELECT e.id exam_id,e.exam_type,e.direction_id,COALESCE(d.name,e.name) direction_name,
    e.date,s.start_at,s.end_at,o.val outside_points,o.points outside_grade,o.examinator,
    a.id attempt_id,a.attempt_no,a.raw_total,a.rounded_total,a.max_score,a.completed_at,
    COALESCE(ap.new_grade,a.calculated_grade) effective_grade,ap.id latest_appeal_id
    FROM exams e LEFT JOIN directions d ON d.id=e.direction_id
    LEFT JOIN classic_exam_settings s ON s.exam_id=e.id
    LEFT JOIN exam_sessions o ON o.exam_id=e.id AND o.student_id=%s
    LEFT JOIN classic_exam_attempts a ON a.exam_id=e.id AND a.student_id=%s AND """
    + CURRENT_CLAUSE
    + """
    LEFT JOIN classic_exam_appeals ap ON ap.id=(SELECT MAX(aa.id) FROM classic_exam_appeals aa WHERE aa.attempt_id=a.id)
"""
)
STUDENT_PRESENT = "((e.exam_type='outside_lms' AND o.id IS NOT NULL) OR (e.exam_type='classic' AND a.id IS NOT NULL))"


def _student_base(row):
    result = {
        "examId": row["exam_id"],
        "examType": row["exam_type"],
        "directionId": row["direction_id"],
        "directionName": row["direction_name"],
    }
    if row["exam_type"] == "classic":
        result.update({"startAt": iso(row["start_at"]), "endAt": iso(row["end_at"])})
    else:
        result["date"] = iso(row["date"])
    return result


def student_list(db, student_id, args):
    page, limit, offset = page_args(args)
    where, params = [STUDENT_PRESENT], [student_id, student_id]
    kind = enum_value(
        args.get("type", "all"), ("all", "classic", "outside_lms"), "exam_type"
    )
    if kind != "all":
        where.append("e.exam_type=%s")
        params.append(kind)
    if args.get("directionId"):
        where.append("e.direction_id=%s")
        params.append(query_int(args["directionId"], "direction_id"))
    if args.get("grade") not in (None, ""):
        where.append(
            "CASE WHEN e.exam_type='outside_lms' THEN o.points ELSE COALESCE(ap.new_grade,a.calculated_grade) END=%s"
        )
        params.append(query_int(args["grade"], "grade", minimum=0, maximum=5))
    date_sql = "CASE WHEN e.exam_type='classic' THEN DATE(DATE_ADD(s.start_at,INTERVAL 3 HOUR)) ELSE e.date END"
    orders = {
        "date_desc": date_sql + " DESC,e.id DESC",
        "date_asc": date_sql + ",e.id",
        "direction_asc": "COALESCE(d.name,e.name),e.id",
    }
    order = orders[enum_value(args.get("sort", "date_desc"), orders, "sort")]
    base = STUDENT_SELECT + " WHERE " + " AND ".join(where)
    total = db.scalar("SELECT COUNT(*) FROM (" + base + ") filtered", params)
    selected = db.all(
        base + " ORDER BY " + order + " LIMIT %s OFFSET %s", params + [limit, offset]
    )
    members = _members(db, [row["attempt_id"] for row in selected if row["attempt_id"]])
    items = []
    for row in selected:
        item = _student_base(row)
        current = _student_current(db, row, members.get(row["attempt_id"], []))
        if row["exam_type"] == "classic":
            item.update(
                {
                    "currentAttempt": current,
                    "hasPreviousAttempt": row["attempt_no"] == 2,
                }
            )
        else:
            item.update(current)
        items.append(item)
    return {"items": items, "pagination": pagination(total, page, limit)}


def student_detail(db, exam_id, student_id):
    row = db.one(
        STUDENT_SELECT + " WHERE e.id=%s AND " + STUDENT_PRESENT,
        (student_id, student_id, exam_id),
    )
    if not row:
        fail("result_not_found", "Результат не найден", 404)
    result = _student_base(row)
    result["history"] = []
    if row["exam_type"] == "outside_lms":
        result["current"] = _student_current(db, row, [])
        return result
    attempts = db.all(
        RESULT_SELECT
        + " WHERE a.exam_id=%s AND a.student_id=%s AND a.status='completed' ORDER BY a.attempt_no DESC",
        (exam_id, student_id),
    )
    members = _members(db, [attempt["id"] for attempt in attempts])
    summaries = []
    for attempt in attempts:  # At most two immutable completed attempts.
        summary = result_summary(db, attempt, members[attempt["id"]], student=True)
        summary["questionCount"] = db.scalar(
            "SELECT COUNT(*) FROM classic_exam_presented_questions WHERE attempt_id=%s",
            (attempt["id"],),
        )
        definition = db.one(
            "SELECT config_json FROM classic_exam_definition_versions WHERE id=%s",
            (attempt["definition_id"],),
        )
        config = loads(definition["config_json"]) if definition else {}
        summary["examWindowSnapshot"] = {
            "startAt": config.get("startAt", config.get("start_at")),
            "endAt": config.get("endAt", config.get("end_at")),
        }
        summaries.append(summary)
    result["current"], result["history"] = summaries[0], summaries[1:]
    return result
