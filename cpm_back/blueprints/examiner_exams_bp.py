"""Assigned commission catalogs and transactional examiner commands."""

from flask import Blueprint, request
from cpm_back.services.exams import lifecycle
from cpm_back.services.exams.common import endpoint, transaction, fail, idempotency_key

examiner_exams_bp = Blueprint("examiner_exams", __name__, url_prefix="/api/examiner")


def payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        fail("invalid_body", "Ожидается JSON-объект")
    return value


@examiner_exams_bp.get("/exams")
@endpoint("examinator")
def exams_list(actor):
    with transaction() as db:
        return lifecycle.examiner_exams(db, request.args, actor)


@examiner_exams_bp.get("/exams/<int:exam_id>/students")
@endpoint("examinator")
def students_list(exam_id, actor):
    with transaction() as db:
        return lifecycle.examiner_students(db, exam_id, request.args, actor)


@examiner_exams_bp.post(
    "/exams/<int:exam_id>/assignments/<int:assignment_id>/attempts/ensure"
)
@endpoint("examinator")
def attempt_ensure(exam_id, assignment_id, actor):
    data, key = payload(), idempotency_key()
    with transaction() as db:
        return lifecycle.ensure(db, exam_id, assignment_id, data, actor, key)


@examiner_exams_bp.get("/attempts/<int:attempt_id>")
@endpoint("examinator")
def attempt_detail(attempt_id, actor):
    with transaction() as db:
        return {"attempt": lifecycle.attempt_state(db, attempt_id, actor)}


@examiner_exams_bp.post("/attempts/<int:attempt_id>/<name>")
@endpoint("examinator")
def attempt_command(attempt_id, name, actor):
    if name not in ("ready", "start", "vote", "next-question", "replace-question"):
        fail("not_found", status=404)
    data, key = payload(), idempotency_key()
    with transaction() as db:
        return lifecycle.command(db, attempt_id, name, data, actor, key)


@examiner_exams_bp.get("/attempts/<int:attempt_id>/questions")
@endpoint("examinator")
def attempt_questions(attempt_id, actor):
    from cpm_back.services.exams.results import questions

    with transaction() as db:
        return questions(db, attempt_id, request.args, actor)


@examiner_exams_bp.get("/attempts/<int:attempt_id>/questions/<int:presented_id>/rounds")
@endpoint("examinator")
def question_rounds(attempt_id, presented_id, actor):
    from cpm_back.services.exams.results import rounds

    with transaction() as db:
        return rounds(db, attempt_id, presented_id, request.args, actor)
