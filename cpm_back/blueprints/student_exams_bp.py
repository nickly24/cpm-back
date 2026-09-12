"""Student-only published examination results, never live exam interaction."""

from flask import Blueprint, request
from cpm_back.services.exams import results
from cpm_back.services.exams.common import endpoint, transaction

student_exams_bp = Blueprint("student_exams", __name__, url_prefix="/api/student/exams")


@student_exams_bp.get("/results")
@endpoint("student", capability="STUDENT_EXAM_RESULTS_V2_ENABLED")
def student_results(actor):
    with transaction() as db:
        return results.student_list(db, actor["id"], request.args)


@student_exams_bp.get("/<int:exam_id>/result")
@endpoint("student", capability="STUDENT_EXAM_RESULTS_V2_ENABLED")
def student_result(exam_id, actor):
    with transaction() as db:
        return results.student_detail(db, exam_id, actor["id"])


@student_exams_bp.get("/<int:exam_id>/attempts/<int:attempt_id>/questions")
@endpoint("student", capability="STUDENT_EXAM_RESULTS_V2_ENABLED")
def student_attempt_questions(exam_id, attempt_id, actor):
    with transaction() as db:
        return results.questions(db, attempt_id, request.args, actor, exam_id)
