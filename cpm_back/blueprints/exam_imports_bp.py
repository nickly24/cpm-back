"""Exam XLSX endpoints. All sessions and idempotency receipts are actor-scoped."""

import hashlib
from flask import Blueprint, request
from cpm_back.services.exams import imports
from cpm_back.services.exams.common import (
    endpoint,
    transaction,
    lock_exam,
    fail,
    query_int,
    loads,
    reserve_admin_command,
    save_admin_receipt,
)

exam_imports_bp = Blueprint("exam_imports", __name__)


@exam_imports_bp.before_request
def limit_import_body():
    request.max_content_length = 18 * 1024 * 1024


def _kind(kind):
    if kind not in ("questions", "assignments", "outside"):
        fail("import_not_found", status=404)
    return kind


@exam_imports_bp.route(
    "/api/exams/<int:exam_id>/imports/<kind>/parse", methods=["POST"]
)
@exam_imports_bp.route(
    "/api/outside-exam-results-import/parse",
    methods=["POST"],
    defaults={"exam_id": None, "kind": "outside"},
)
@endpoint("admin", "staff_admin", mutation=True)
def parse(exam_id, kind, actor):
    _kind(kind)
    if exam_id is None:
        exam_id = query_int(request.form.get("exam_id"), "exam_id")
    file = request.files.get("file")
    if file is None:
        fail("file_required", "Выберите Excel-файл")
    content = file.read(imports.MAX_FILE + 1)
    rows = imports.parse_xlsx(content, file.filename, kind)
    payload = {
        "examId": exam_id,
        "kind": kind,
        "filename": file.filename,
        "fileSha256": hashlib.sha256(content).hexdigest(),
    }
    with transaction() as db:
        lock_exam(db, exam_id, "outside_lms" if kind == "outside" else "classic")
        command, replayed = reserve_admin_command(db, actor, payload, exam_id)
        if replayed:
            session_id = loads(command["receipt"])["sessionId"]
            session = imports.session_dto(
                db, kind, imports.session_row(db, kind, session_id, actor, exam_id)
            )
        else:
            session = imports.create_session(
                db, kind, exam_id, actor, file.filename, rows
            )
            save_admin_receipt(db, command, {"sessionId": session["id"]}, exam_id)
        return {"session": session, "replayed": replayed}, 200 if replayed else 201


@exam_imports_bp.route(
    "/api/exams/<int:exam_id>/imports/<kind>/sessions/<int:session_id>",
    methods=["GET", "PUT"],
)
@exam_imports_bp.route(
    "/api/outside-exam-results-import/sessions/<int:session_id>",
    methods=["GET", "PUT"],
    defaults={"exam_id": None, "kind": "outside"},
)
@endpoint("admin", "staff_admin")
def session(exam_id, kind, session_id, actor):
    _kind(kind)
    if exam_id is None:
        # Discovery uses its own completed read transaction. The mutation must
        # not retain a snapshot taken before waiting for the parent exam lock.
        with transaction() as db:
            exam_id = imports.session_row(db, kind, session_id, actor)["exam_id"]
    with transaction() as db:
        lock_exam(
            db,
            exam_id,
            "outside_lms" if kind == "outside" else "classic",
            shared=request.method == "GET",
        )
        row = imports.session_row(
            db, kind, session_id, actor, exam_id, request.method != "GET"
        )
        if request.method == "GET":
            return {"session": imports.session_dto(db, kind, row, request.args)}
        payload = request.get_json(silent=True)
        command, replayed = reserve_admin_command(db, actor, payload, exam_id)
        if replayed:
            result = imports.session_dto(db, kind, row)
        else:
            result = imports.update_session(db, kind, row, payload)
            save_admin_receipt(
                db,
                command,
                {"sessionId": session_id, "previewVersion": result["previewVersion"]},
                exam_id,
            )
        return {"session": result, "replayed": replayed}


@exam_imports_bp.route(
    "/api/exams/<int:exam_id>/imports/<kind>/sessions/<int:session_id>/commit",
    methods=["POST"],
)
@exam_imports_bp.route(
    "/api/outside-exam-results-import/sessions/<int:session_id>/commit",
    methods=["POST"],
    defaults={"exam_id": None, "kind": "outside"},
)
@endpoint("admin", "staff_admin", mutation=True)
def commit(exam_id, kind, session_id, actor):
    _kind(kind)
    if exam_id is None:
        with transaction() as db:
            exam_id = imports.session_row(db, kind, session_id, actor)["exam_id"]
    with transaction() as db:
        lock_exam(db, exam_id, "outside_lms" if kind == "outside" else "classic")
        row = imports.session_row(db, kind, session_id, actor, exam_id, True)
        payload = request.get_json(silent=True)
        command, replayed = reserve_admin_command(db, actor, payload, exam_id)
        result = imports.commit_session(db, kind, row, actor, payload)
        if not replayed:
            save_admin_receipt(
                db, command, {"sessionId": session_id, "committed": True}, exam_id
            )
        return {"result": dict(result, replayed=replayed or result["replayed"])}
