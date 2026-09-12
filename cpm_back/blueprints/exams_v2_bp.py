"""Versioned administrative exam API. No I/O at import time."""

from flask import Blueprint, current_app, request
from cpm_back.services.exams import admin
from cpm_back.services.exams.common import (
    endpoint,
    transaction,
    lock_exam,
    reserve_admin_command,
    save_admin_receipt,
    loads,
    fields,
    fail,
    integer,
    query_int,
    check_version,
    bump_config,
    invalidate_rating,
    parse_date,
    confirmation_token,
    verify_confirmation,
    digest,
    page_args,
    pagination,
    search_value,
)

exams_v2_bp = Blueprint("exams_v2", __name__, url_prefix="/api/exams")
ADMIN = ("admin", "staff_admin")


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail("invalid_body", "Ожидается JSON-объект")
    return data


def admin_mutation(actor, exam_id, payload, action, replay, kind=None, status=200):
    """Action returns (response data, compact receipt). Replays reread domain data."""
    with transaction() as db:
        exam = lock_exam(db, exam_id, kind) if exam_id else None
        command, repeated = reserve_admin_command(db, actor, payload, exam_id)
        if repeated:
            if command.get("tombstone"):
                fail("removed_target", "История этого действия была удалена", 404)
            receipt = loads(command["receipt"]) or {}
            data = replay(db, receipt)
        else:
            data, receipt = action(db, exam)
            receipt = dict(
                receipt,
                commandId=command["id"],
                outcome=receipt.get("outcome", "applied"),
            )
            save_admin_receipt(
                db,
                command,
                receipt,
                exam_id or receipt.get("examId"),
                receipt.get("studentId"),
                receipt.get("attemptId"),
            )
        if status == 204:
            return {}, 204
        return dict(data, receipt=receipt, replayed=repeated), (
            200 if repeated else status
        )


@exams_v2_bp.get("/capabilities")
@endpoint()
def capabilities(actor):
    from cpm_back.auth.admin_permissions import has_permission

    enabled = current_app.config.get("EXAMS_V2_ENABLED", False)
    can_view = actor["role"] in ADMIN and has_permission(actor, "exams", "view")
    can_edit = actor["role"] in ADMIN and has_permission(actor, "exams", "edit")
    return {
        "apiVersion": "v2",
        "canReadAdminExams": bool(enabled and can_view),
        "canManageOutside": bool(enabled and can_edit),
        "canCreateClassic": bool(
            enabled
            and can_edit
            and current_app.config.get("CLASSIC_EXAM_CREATION_ENABLED", False)
        ),
        "canConductClassic": bool(
            actor["role"] == "examinator"
            and current_app.config.get("CLASSIC_EXAM_COMMANDS_ENABLED", False)
        ),
        "canReadStudentResults": bool(
            actor["role"] == "student"
            and current_app.config.get("STUDENT_EXAM_RESULTS_V2_ENABLED", False)
        ),
    }


@exams_v2_bp.route("", methods=["GET", "POST"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def exams_collection(actor):
    if request.method == "GET":
        with transaction() as db:
            return admin.list_exams(db, request.args)
    payload = body()
    if payload.get("examType") == "classic" and not current_app.config.get(
        "CLASSIC_EXAM_CREATION_ENABLED", False
    ):
        fail(
            "exam_temporarily_unavailable",
            "Создание классических экзаменов временно отключено",
            503,
        )

    def action(db, exam):
        result = admin.create_exam(db, payload)
        return {"exam": result}, {"examId": result["id"]}

    return admin_mutation(
        actor,
        None,
        payload,
        action,
        lambda db, r: {"exam": admin.get_exam(db, r["examId"])},
        status=201,
    )


@exams_v2_bp.get("/<int:exam_id>")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def exam_detail(exam_id, actor):
    with transaction() as db:
        return {"exam": admin.get_exam(db, exam_id)}


@exams_v2_bp.patch("/<int:exam_id>/direction")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def exam_direction(exam_id, actor):
    payload = body()
    return admin_mutation(
        actor,
        exam_id,
        payload,
        lambda db, e: (
            {"exam": admin.update_direction(db, e, payload)},
            {"examId": exam_id},
        ),
        lambda db, r: {"exam": admin.get_exam(db, exam_id)},
    )


@exams_v2_bp.get("/lookups/<kind>")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def account_lookup(kind, actor):
    if kind not in ("students", "examinators"):
        fail("not_found", status=404)
    with transaction() as db:
        return admin.lookups(
            db, "student" if kind == "students" else "examinator", request.args
        )


@exams_v2_bp.route("/<int:exam_id>/classic/config", methods=["GET", "PATCH"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_config(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            return admin.get_config(db, exam_id)
    payload = body()
    return admin_mutation(
        actor,
        exam_id,
        payload,
        lambda db, e: (admin.update_config(db, exam_id, payload), {"examId": exam_id}),
        lambda db, r: admin.get_config(db, exam_id),
        "classic",
    )


@exams_v2_bp.route("/<int:exam_id>/classic/scoring", methods=["GET", "PUT"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_scoring(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            return admin.get_scoring(db, exam_id)
    payload = body()
    return admin_mutation(
        actor,
        exam_id,
        payload,
        lambda db, e: (admin.save_scoring(db, exam_id, payload), {"examId": exam_id}),
        lambda db, r: admin.get_scoring(db, exam_id),
        "classic",
    )


@exams_v2_bp.route("/<int:exam_id>/classic/parts", methods=["GET", "POST"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def parts_collection(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            return {"items": [admin.part_dto(r) for r in admin.part_rows(db, exam_id)]}
    payload = body()

    def action(db, e):
        part = admin.save_part(db, exam_id, payload)
        return {"part": part}, {"partId": part["id"]}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: {"part": admin.get_part(db, exam_id, r["partId"])},
        "classic",
        201,
    )


@exams_v2_bp.route(
    "/<int:exam_id>/classic/parts/<int:part_id>", methods=["PATCH", "DELETE"]
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def part_detail(exam_id, part_id, actor):
    payload = (
        body()
        if request.method == "PATCH"
        else {
            "expectedVersion": query_int(request.args.get("expectedVersion"), "version")
        }
    )

    def action(db, e):
        if request.method == "PATCH":
            return {"part": admin.save_part(db, exam_id, payload, part_id)}, {
                "partId": part_id
            }
        row = admin.require_row(
            db, "classic_exam_parts", part_id, exam_id, "part_not_found"
        )
        check_version(row, payload["expectedVersion"], "part_modified")
        preview = part_preview_data(db, exam_id, part_id)
        verify_confirmation(
            actor, {"examId": exam_id, "partId": part_id}, preview["fingerprint"]
        )
        db.execute(
            "UPDATE classic_exam_settings SET tie_breaker_part_id=NULL WHERE exam_id=%s AND tie_breaker_part_id=%s",
            (exam_id, part_id),
        )
        db.execute("DELETE FROM classic_exam_parts WHERE id=%s", (part_id,))
        bump_config(db, exam_id)
        return {}, {"partId": part_id, "outcome": "deleted"}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: (
            {}
            if request.method == "DELETE"
            else {"part": admin.get_part(db, exam_id, part_id)}
        ),
        "classic",
        204 if request.method == "DELETE" else 200,
    )


def part_preview_data(db, exam_id, part_id):
    part = admin.get_part(db, exam_id, part_id)
    config = admin.get_config_row(db, exam_id)
    values = {
        "part": part,
        "questionCount": part["bankSize"],
        "referencedAsTieBreaker": config["tie_breaker_part_id"] == part_id,
    }
    values["fingerprint"] = digest(
        {
            "partId": part_id,
            "partVersion": part["version"],
            "configVersion": config["config_version"],
        }
    )
    return values


@exams_v2_bp.get("/<int:exam_id>/classic/parts/<int:part_id>/delete-preview")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def part_delete_preview(exam_id, part_id, actor):
    with transaction() as db:
        lock_exam(db, exam_id, "classic", True)
        preview = part_preview_data(db, exam_id, part_id)
        return dict(
            preview,
            counts={"questions": preview["questionCount"]},
            **confirmation_token(
                actor,
                {"examId": exam_id, "partId": part_id},
                preview.pop("fingerprint"),
            ),
        )


@exams_v2_bp.route("/<int:exam_id>/classic/questions", methods=["GET", "POST"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def questions_collection(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            return admin.list_questions(db, exam_id, request.args)
    payload = body()

    def action(db, e):
        question = admin.save_question(db, exam_id, payload)
        return {"question": question}, {"questionId": question["id"]}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: {"question": admin.get_question(db, exam_id, r["questionId"])},
        "classic",
        201,
    )


@exams_v2_bp.route(
    "/<int:exam_id>/classic/questions/<int:question_id>", methods=["PATCH", "DELETE"]
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def question_detail(exam_id, question_id, actor):
    payload = (
        body()
        if request.method == "PATCH"
        else {
            "expectedVersion": query_int(request.args.get("expectedVersion"), "version")
        }
    )

    def action(db, e):
        if request.method == "PATCH":
            return {
                "question": admin.save_question(db, exam_id, payload, question_id)
            }, {"questionId": question_id}
        row = admin.require_row(
            db, "classic_exam_questions", question_id, exam_id, "question_not_found"
        )
        check_version(row, payload["expectedVersion"], "question_modified")
        db.execute("DELETE FROM classic_exam_questions WHERE id=%s", (question_id,))
        bump_config(db, exam_id)
        return {}, {"questionId": question_id, "outcome": "deleted"}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: (
            {}
            if request.method == "DELETE"
            else {"question": admin.get_question(db, exam_id, question_id)}
        ),
        "classic",
        204 if request.method == "DELETE" else 200,
    )


@exams_v2_bp.route("/<int:exam_id>/classic/commissions", methods=["GET", "POST"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def commissions_collection(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            page, limit, offset = page_args(request.args)
            total = db.scalar(
                "SELECT COUNT(*) FROM classic_exam_commissions WHERE exam_id=%s",
                (exam_id,),
            )
            rows = db.all(
                "SELECT id FROM classic_exam_commissions WHERE exam_id=%s ORDER BY id LIMIT %s OFFSET %s",
                (exam_id, limit, offset),
            )
            return {
                "items": [admin.get_commission(db, exam_id, r["id"]) for r in rows],
                "pagination": pagination(total, page, limit),
            }
    payload = body()

    def action(db, e):
        commission = admin.save_commission(db, exam_id, payload)
        return {"commission": commission}, {"commissionId": commission["id"]}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: {
            "commission": admin.get_commission(db, exam_id, r["commissionId"])
        },
        "classic",
        201,
    )


@exams_v2_bp.route(
    "/<int:exam_id>/classic/commissions/<int:commission_id>", methods=["PUT", "DELETE"]
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def commission_detail(exam_id, commission_id, actor):
    payload = (
        body()
        if request.method == "PUT"
        else {
            "expectedVersion": query_int(request.args.get("expectedVersion"), "version")
        }
    )

    def action(db, e):
        if request.method == "PUT":
            return {
                "commission": admin.save_commission(
                    db, exam_id, payload, commission_id
                ),
                "affectedExistingAssignments": 0,
            }, {"commissionId": commission_id}
        row = admin.require_row(
            db,
            "classic_exam_commissions",
            commission_id,
            exam_id,
            "commission_not_found",
        )
        check_version(row, payload["expectedVersion"], "commission_modified")
        db.execute("DELETE FROM classic_exam_commissions WHERE id=%s", (commission_id,))
        return {}, {"commissionId": commission_id, "outcome": "deleted"}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: (
            {}
            if request.method == "DELETE"
            else {
                "commission": admin.get_commission(db, exam_id, commission_id),
                "affectedExistingAssignments": 0,
            }
        ),
        "classic",
        204 if request.method == "DELETE" else 200,
    )


@exams_v2_bp.route("/<int:exam_id>/classic/assignments", methods=["GET", "POST"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def assignments_collection(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            return admin.list_assignments(db, exam_id, request.args)
    payload = body()

    def action(db, e):
        assignment = admin.save_assignment(db, exam_id, payload, actor)
        return {"assignment": assignment}, {
            "assignmentId": assignment["id"],
            "studentId": assignment["student"]["id"],
        }

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: {
            "assignment": admin.get_assignment(db, exam_id, r["assignmentId"])
        },
        "classic",
        201,
    )


@exams_v2_bp.route(
    "/<int:exam_id>/classic/assignments/<int:assignment_id>", methods=["PUT", "DELETE"]
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def assignment_detail(exam_id, assignment_id, actor):
    payload = (
        body()
        if request.method == "PUT"
        else {
            "expectedVersion": query_int(request.args.get("expectedVersion"), "version")
        }
    )

    def action(db, e):
        if request.method == "PUT":
            return {
                "assignment": admin.save_assignment(
                    db, exam_id, payload, actor, assignment_id
                )
            }, {"assignmentId": assignment_id}
        row = admin.require_row(
            db,
            "classic_exam_assignments",
            assignment_id,
            exam_id,
            "assignment_not_found",
        )
        check_version(row, payload["expectedVersion"], "assignment_modified")
        if db.one(
            "SELECT id FROM classic_exam_attempts WHERE assignment_id=%s",
            (assignment_id,),
        ):
            fail("assignment_has_attempt", status=409)
        db.execute("DELETE FROM classic_exam_assignments WHERE id=%s", (assignment_id,))
        return {}, {"assignmentId": assignment_id, "outcome": "deleted"}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: (
            {}
            if request.method == "DELETE"
            else {"assignment": admin.get_assignment(db, exam_id, assignment_id)}
        ),
        "classic",
        204 if request.method == "DELETE" else 200,
    )


@exams_v2_bp.route(
    "/<int:exam_id>/classic/students/<int:student_id>/privilege",
    methods=["GET", "PUT", "DELETE"],
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def student_privilege(exam_id, student_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "classic", True)
            return admin.get_privilege(db, exam_id, student_id)
    payload = (
        body()
        if request.method == "PUT"
        else {
            "expectedVersion": query_int(
                request.args.get("expectedVersion"), "version", minimum=0
            )
        }
    )

    def action(db, e):
        if request.method == "PUT":
            return admin.save_privilege(db, exam_id, student_id, payload), {
                "studentId": student_id
            }
        row = admin.get_privilege(db, exam_id, student_id)
        check_version(row, payload["expectedVersion"], "privilege_modified")
        db.execute(
            "DELETE FROM classic_exam_student_privileges WHERE exam_id=%s AND student_id=%s",
            (exam_id, student_id),
        )
        return {}, {"studentId": student_id, "outcome": "deleted"}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: (
            {}
            if request.method == "DELETE"
            else admin.get_privilege(db, exam_id, student_id)
        ),
        "classic",
        204 if request.method == "DELETE" else 200,
    )


@exams_v2_bp.get("/<int:exam_id>/classic/privileges")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def privileges_collection(exam_id, actor):
    with transaction() as db:
        lock_exam(db, exam_id, "classic", True)
        page, limit, offset = page_args(request.args)
        where = "p.exam_id=%s"
        params = [exam_id]
        if request.args.get("search"):
            where += " AND (s.full_name LIKE %s ESCAPE '!' OR CAST(s.id AS CHAR)=%s)"
            params.extend([search_value(request.args), request.args["search"]])
        joins = (
            "FROM classic_exam_student_privileges p JOIN students s ON s.id=p.student_id WHERE "
            + where
        )
        total = db.scalar("SELECT COUNT(*) " + joins, params)
        rows = db.all(
            "SELECT p.*,s.full_name "
            + joins
            + " ORDER BY s.full_name,s.id LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        return {
            "items": [
                {
                    "student": {"id": r["student_id"], "fullName": r["full_name"]},
                    "replacementLimit": r["replacement_limit"],
                    "version": r["version"],
                }
                for r in rows
            ],
            "pagination": pagination(total, page, limit),
        }


@exams_v2_bp.get("/<int:exam_id>/classic/readiness")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_readiness(exam_id, actor):
    with transaction() as db:
        lock_exam(db, exam_id, "classic", True)
        return {"readiness": admin.readiness(db, exam_id)}


@exams_v2_bp.get("/<int:exam_id>/classic/assignments/<int:assignment_id>/readiness")
@endpoint(*ADMIN, "examinator", capability="EXAMS_V2_ENABLED")
def assignment_readiness(exam_id, assignment_id, actor):
    with transaction() as db:
        lock_exam(db, exam_id, "classic", True)
        if actor["role"] == "examinator" and not db.one(
            "SELECT assignment_id FROM classic_exam_assignment_members WHERE assignment_id=%s AND examinator_id=%s",
            (assignment_id, actor["id"]),
        ):
            fail("assignment_not_found", status=404)
        return admin.readiness(db, exam_id, assignment_id, True)


@exams_v2_bp.patch("/<int:exam_id>/outside-lms")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def outside_config(exam_id, actor):
    payload = body()

    def action(db, e):
        fields(payload, ("date", "expectedVersion"), ("date", "expectedVersion"))
        check_version(e, payload["expectedVersion"], "exam_modified")
        value = parse_date(payload["date"])
        if value != e["date"]:
            db.update(
                "exams",
                {"date": value, "version": e["version"] + 1},
                "id=%s",
                (exam_id,),
            )
            invalidate_rating(db)
        return {"exam": admin.get_exam(db, exam_id)}, {"examId": exam_id}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: {"exam": admin.get_exam(db, exam_id)},
        "outside_lms",
    )


@exams_v2_bp.route("/<int:exam_id>/outside-lms/results", methods=["GET", "POST"])
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def outside_results(exam_id, actor):
    if request.method == "GET":
        with transaction() as db:
            lock_exam(db, exam_id, "outside_lms", True)
            return admin.list_outside_results(db, exam_id, request.args)
    payload = body()

    def action(db, e):
        result = admin.save_outside_result(db, exam_id, payload)
        return {"result": result}, {
            "resultId": result["id"],
            "studentId": result["studentId"],
        }

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: {"result": admin.outside_result(db, exam_id, r["resultId"])},
        "outside_lms",
        201,
    )


@exams_v2_bp.route(
    "/<int:exam_id>/outside-lms/results/<int:result_id>", methods=["PATCH", "DELETE"]
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def outside_result_detail(exam_id, result_id, actor):
    payload = (
        body()
        if request.method == "PATCH"
        else {
            "expectedVersion": query_int(request.args.get("expectedVersion"), "version")
        }
    )

    def action(db, e):
        if request.method == "PATCH":
            return {
                "result": admin.save_outside_result(db, exam_id, payload, result_id)
            }, {"resultId": result_id}
        row = admin.require_row(
            db, "exam_sessions", result_id, exam_id, "result_not_found"
        )
        check_version(row, payload["expectedVersion"], "result_modified")
        db.execute("DELETE FROM exam_sessions WHERE id=%s", (result_id,))
        invalidate_rating(db)
        return {}, {"resultId": result_id, "outcome": "deleted"}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, r: (
            {}
            if request.method == "DELETE"
            else {"result": admin.outside_result(db, exam_id, result_id)}
        ),
        "outside_lms",
        204 if request.method == "DELETE" else 200,
    )


@exams_v2_bp.get("/<int:exam_id>/overview")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def exam_overview(exam_id, actor):
    with transaction() as db:
        lock_exam(db, exam_id, shared=True)
        exam = admin.get_exam(db, exam_id)
        if exam["examType"] == "outside_lms":
            row = db.one(
                "SELECT COUNT(*) resultsCount,AVG(points) averageGrade,MAX(updated_at) lastUpdatedAt FROM exam_sessions WHERE exam_id=%s",
                (exam_id,),
            )
            return {
                "exam": exam,
                "outsideLms": row,
                "availableTabs": ["overview", "results"],
            }
        counts = {
            key: db.scalar(f"SELECT COUNT(*) FROM {table} WHERE exam_id=%s", (exam_id,))
            for key, table in (
                ("partsCount", "classic_exam_parts"),
                ("questionsCount", "classic_exam_questions"),
                ("commissionsCount", "classic_exam_commissions"),
                ("assignmentsCount", "classic_exam_assignments"),
            )
        }
        counts["assignedStudentsCount"] = db.scalar(
            "SELECT COUNT(DISTINCT student_id) FROM classic_exam_assignments WHERE exam_id=%s",
            (exam_id,),
        )
        statuses = {
            r["status"]: r["n"]
            for r in db.all(
                "SELECT status,COUNT(*) n FROM classic_exam_attempts WHERE exam_id=%s GROUP BY status",
                (exam_id,),
            )
        }
        counts["attempts"] = {
            "pending": statuses.get("pending_ready", 0),
            "inProgress": statuses.get("in_progress", 0),
            "completed": statuses.get("completed", 0),
        }
        counts["resultsCount"] = db.scalar(
            "SELECT COUNT(DISTINCT student_id) FROM classic_exam_attempts WHERE exam_id=%s AND status='completed'",
            (exam_id,),
        )
        report = admin.readiness(db, exam_id)
        counts["readiness"] = {
            "isConfigured": report["isConfigured"],
            "errorCount": len(report["errors"]),
        }
        return {
            "exam": exam,
            "classic": counts,
            "availableTabs": [
                "overview",
                "questions",
                "scoring",
                "commissions",
                "assignments",
                "attempts",
                "results",
            ],
        }


# Published results and audit protocols are a separate domain module.
from cpm_back.services.exams import results


@exams_v2_bp.get("/<int:exam_id>/classic/results")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_results(exam_id, actor):
    with transaction() as db:
        return results.list_results(db, exam_id, request.args)


@exams_v2_bp.get("/<int:exam_id>/classic/attempts")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_attempts(exam_id, actor):
    with transaction() as db:
        return results.list_attempts(db, exam_id, request.args)


@exams_v2_bp.get("/<int:exam_id>/classic/attempts/<int:attempt_id>")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_attempt_detail(exam_id, attempt_id, actor):
    with transaction() as db:
        return {"attempt": results.attempt_detail(db, exam_id, attempt_id, actor)}


@exams_v2_bp.get("/<int:exam_id>/classic/attempts/<int:attempt_id>/questions")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_attempt_questions(exam_id, attempt_id, actor):
    with transaction() as db:
        return results.questions(db, attempt_id, request.args, actor, exam_id)


@exams_v2_bp.get(
    "/<int:exam_id>/classic/attempts/<int:attempt_id>/questions/<int:presented_id>/rounds"
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_attempt_rounds(exam_id, attempt_id, presented_id, actor):
    with transaction() as db:
        return results.rounds(
            db, attempt_id, presented_id, request.args, actor, exam_id
        )


@exams_v2_bp.route(
    "/<int:exam_id>/classic/attempts/<int:attempt_id>/appeals", methods=["GET", "POST"]
)
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED", mutation=True)
def classic_attempt_appeals(exam_id, attempt_id, actor):
    if request.method == "GET":
        with transaction() as db:
            return results.appeals(db, exam_id, attempt_id, request.args)
    payload = body()

    def action(db, exam):
        data = results.appeal(db, exam_id, attempt_id, payload, actor)
        attempt = results._attempt(db, attempt_id, actor, exam_id)
        return data, {
            "appealId": data["appeal"]["id"],
            "attemptId": attempt_id,
            "studentId": attempt["student_id"],
        }

    def replay(db, receipt):
        attempt = results._attempt(db, attempt_id, actor, exam_id)
        appeal_row = db.one(
            "SELECT * FROM classic_exam_appeals WHERE id=%s AND attempt_id=%s",
            (receipt["appealId"], attempt_id),
        )
        if not appeal_row:
            fail("appeal_not_found", status=404)
        current = results.effective_result(db, exam_id, attempt["student_id"])
        return {
            "appeal": results._appeal_dto(appeal_row),
            "effectiveGrade": attempt["effective_grade"],
            "hasAppeal": attempt["latest_appeal_id"] is not None,
            "resultVersion": attempt["result_version"],
            "isCurrentAttempt": bool(current and current["id"] == attempt_id),
        }

    return admin_mutation(actor, exam_id, payload, action, replay, "classic", 201)


@exams_v2_bp.post("/<int:exam_id>/classic/students/<int:student_id>/retake")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED", mutation=True)
def classic_student_retake(exam_id, student_id, actor):
    payload = body()

    def action(db, exam):
        data = results.retake(db, exam_id, student_id, payload, actor)
        return data, {"assignmentId": data["assignment"]["id"], "studentId": student_id}

    return admin_mutation(
        actor,
        exam_id,
        payload,
        action,
        lambda db, receipt: {
            "assignment": admin.get_assignment(db, exam_id, receipt["assignmentId"])
        },
        "classic",
        201,
    )


@exams_v2_bp.get("/<int:exam_id>/classic/students/<int:student_id>/delete-preview")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def classic_history_delete_preview(exam_id, student_id, actor):
    with transaction() as db:
        return results.history_delete_preview(db, exam_id, student_id, actor)


def _destructive_command(actor, exam_id, student_id=None):
    """Tombstone replay works after physical deletion, without caching any data.

    Read-committed receipt probes acquire no lock ahead of the exam lock. A deletion
    that commits while this request waits is rechecked before returning a404.
    """
    from cpm_back.services.exams.common import idempotency_key, now

    payload = {"examId": exam_id}
    if student_id is not None:
        payload["studentId"] = student_id
    key = idempotency_key()
    wanted_hash = digest(
        {"path": request.path, "method": request.method, "payload": payload}
    )
    with transaction() as db:
        # common.transaction starts mutation transactions as READ COMMITTED.
        # Do not issue SET TRANSACTION after the transaction already started.
        def replay_if_done():
            row = db.one(
                "SELECT request_hash,tombstone,expires_at FROM exam_admin_commands WHERE actor_role=%s AND actor_id=%s AND idempotency_key=%s",
                (actor["role"], actor["id"], key),
            )
            if not row:
                return False
            if row["request_hash"] != wanted_hash:
                fail(
                    "idempotency_key_reused",
                    "Этот ключ использован для другого действия",
                    409,
                )
            if row["expires_at"] < now():
                fail(
                    "idempotency_key_expired",
                    "Обновите страницу перед новым действием",
                    409,
                )
            return bool(row["tombstone"])

        if replay_if_done():
            return {}, 204
        exam = db.one("SELECT * FROM exams WHERE id=%s FOR UPDATE", (exam_id,))
        if not exam:
            if replay_if_done():
                return {}, 204
            fail("exam_not_found", "Экзамен не найден", 404)
        command, repeated = reserve_admin_command(db, actor, payload, exam_id)
        if repeated and command["tombstone"]:
            return {}, 204
        if repeated:
            fail(
                "concurrent_change",
                "Действие ещё выполняется. Повторите запрос с тем же ключом.",
                409,
            )
        if student_id is None:
            results.delete_exam(db, exam_id, actor)
        else:
            results.delete_history(db, exam_id, student_id, actor)
        db.update(
            "exam_admin_commands",
            {
                "receipt": None,
                "tombstone": True,
                "target_exam_id": exam_id,
                "target_student_id": student_id,
            },
            "id=%s",
            (command["id"],),
        )
        return {}, 204


@exams_v2_bp.delete("/<int:exam_id>/classic/students/<int:student_id>/history")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED", mutation=True)
def classic_history_delete(exam_id, student_id, actor):
    return _destructive_command(actor, exam_id, student_id)


@exams_v2_bp.get("/<int:exam_id>/delete-preview")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED")
def full_exam_delete_preview(exam_id, actor):
    with transaction() as db:
        return results.exam_delete_preview(db, exam_id, actor)


@exams_v2_bp.delete("/<int:exam_id>")
@endpoint(*ADMIN, capability="EXAMS_V2_ENABLED", mutation=True)
def full_exam_delete(exam_id, actor):
    return _destructive_command(actor, exam_id)
