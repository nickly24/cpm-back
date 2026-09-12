"""Shared transaction, validation, transport and idempotency primitives.

Services receive an existing UnitOfWork; only the HTTP boundary commits it.
Importing this module never connects to a database or starts a Flask app.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
import hashlib
import json
import math
import re
import time
import uuid

from flask import current_app, g, jsonify, request, has_request_context
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.exceptions import HTTPException
from mysql.connector.errors import IntegrityError, PoolError

UTC = timezone.utc
MOSCOW = timezone(timedelta(hours=3))
MAX_ID = 9007199254740991


class ExamError(Exception):
    def __init__(self, code, message=None, status=400, details=None):
        super().__init__(message or code)
        self.code, self.message = code, message or code
        self.status, self.details = status, details or {}


def fail(code, message=None, status=400, **details):
    raise ExamError(code, message, status, details)


def integer(value, field="id", minimum=1, maximum=MAX_ID):
    if type(value) is not int or not minimum <= value <= maximum:
        fail("invalid_" + field, f"Некорректное значение поля {field}", field=field)
    return value


def query_int(value, field="id", default=None, minimum=1, maximum=MAX_ID):
    if value is None or value == "":
        if default is not None:
            return default
        fail("invalid_" + field, field=field)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        fail("invalid_" + field, field=field)
    return integer(int(value), field, minimum, maximum)


def decimal_number(value, field="points", maximum=Decimal("9999999999.99"), places=2):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        fail("invalid_" + field, field=field)
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        fail("invalid_" + field, field=field)
    if not result.is_finite() or result < 0 or result > maximum:
        fail("invalid_" + field, field=field)
    if result != result.quantize(Decimal(1).scaleb(-places)):
        fail("invalid_" + field, "Лишняя точность числа", field=field)
    return result


def text_value(value, field, maximum=255, byte_limit=None):
    if not isinstance(value, str):
        fail(field + "_required", field=field)
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        fail(field + "_required", field=field)
    if (maximum is not None and len(value) > maximum) or (
        byte_limit and len(value.encode("utf-8")) > byte_limit
    ):
        fail("text_too_long", field=field)
    return value


def enum_value(value, values, field, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or value not in values:
        fail("invalid_" + field, field=field)
    return value


def fields(payload, allowed, required=()):
    if not isinstance(payload, dict):
        fail("invalid_body", "Ожидается JSON-объект")
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        fail("unknown_field", "Неизвестные поля", fields=unknown)
    missing = sorted(set(required) - set(payload))
    if missing:
        fail("missing_field", "Заполните обязательные поля", fields=missing)
    return payload


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        fail("invalid_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail("invalid_date")


def parse_datetime(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(UTC).replace(tzinfo=None)
    except (ValueError, TypeError, AttributeError):
        fail("invalid_datetime", "Нужно ISO-время с часовым поясом")


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).astimezone(MOSCOW).isoformat()
    return value.isoformat() if isinstance(value, date) else value


def jsonable(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, (datetime, date)):
        return iso(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def dumps(value):
    return json.dumps(
        jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def loads(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def digest(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def check_version(row, expected, code="stale_state", column="version"):
    integer(expected, "version", minimum=0)
    if row[column] != expected:
        fail(
            code,
            "Данные изменились. Обновите страницу.",
            409,
            currentVersion=row[column],
        )


def pagination(total, page, limit):
    pages = max(1, math.ceil(total / limit))
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": pages,
        "hasNext": page < pages,
        "hasPrev": page > 1,
    }


def page_args(args, default=20, maximum=100):
    page = query_int(args.get("page"), "page", 1)
    limit = query_int(args.get("limit"), "limit", default, maximum=maximum)
    return page, limit, (page - 1) * limit


def search_value(args):
    value = args.get("search", "").strip()
    if len(value) > 200:
        fail("invalid_search")
    return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


class UnitOfWork:
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.cursor(dictionary=True, buffered=True)

    def execute(self, sql, params=()):
        self.cursor.execute(sql, params)
        return self.cursor

    def one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def scalar(self, sql, params=(), default=0):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else default

    def insert(self, table, data):
        # table/column identifiers are internal constants, never user input.
        columns = ",".join("`" + name + "`" for name in data)
        self.execute(
            f'INSERT INTO `{table}` ({columns}) VALUES ({",".join(["%s"] * len(data))})',
            tuple(data.values()),
        )
        return self.cursor.lastrowid

    def update(self, table, data, where, params):
        self.execute(
            f'UPDATE `{table}` SET {",".join("`" + key + "`=%s" for key in data)} WHERE {where}',
            tuple(data.values()) + tuple(params),
        )


@contextmanager
def transaction(write=None):
    from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

    # Connector pools are nonblocking. A short, bounded admission wait absorbs
    # concurrent committee clicks without retrying any SQL or logical command.
    # Exhaustion is reported before a transaction/receipt exists, so the client
    # can safely retry the same idempotency key after the temporary overload.
    deadline = time.monotonic() + 0.5
    while True:
        try:
            connection = get_db_connection()
            break
        except PoolError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail(
                    "exam_temporarily_unavailable",
                    "Сервер занят. Повторите действие через секунду.",
                    503,
                    retryAfterSeconds=1,
                )
            time.sleep(min(0.01, remaining))
    uow = UnitOfWork(connection)
    try:
        uow.execute("SET time_zone='+00:00'")
        # Production has an empty global sql_mode. Never silently truncate a
        # domain value; apply strictness to this pooled session only.
        uow.execute(
            "SET SESSION sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'"
        )
        if write is None:
            write = has_request_context() and request.method not in (
                "GET",
                "HEAD",
                "OPTIONS",
            )
        # Mutation discovery may precede a contended row lock. READ COMMITTED
        # ensures subsequent vote/receipt reads observe the transaction that
        # released that lock. Read-only DTOs retain a consistent RR snapshot.
        connection.start_transaction(
            isolation_level="READ COMMITTED" if write else "REPEATABLE READ"
        )
        yield uow
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        uow.cursor.close()
        close_db_connection(connection)


def lock_exam(db, exam_id, kind=None, shared=False):
    exam = db.one(
        "SELECT * FROM exams WHERE id=%s "
        + ("LOCK IN SHARE MODE" if shared else "FOR UPDATE"),
        (exam_id,),
    )
    if not exam:
        fail("exam_not_found", "Экзамен не найден", 404)
    if kind and exam["exam_type"] != kind:
        fail("wrong_exam_type", "Операция недоступна для этого типа экзамена", 422)
    return exam


def bump_config(db, exam_id):
    if getattr(db, "defer_invalidations", False):
        if not hasattr(db, "pending_configs"):
            db.pending_configs = set()
        db.pending_configs.add(exam_id)
        return
    db.execute(
        "UPDATE classic_exam_settings SET config_version=config_version+1 WHERE exam_id=%s",
        (exam_id,),
    )


def invalidate_rating(db):
    # Singleton is deliberately locked last, after all domain rows.
    if getattr(db, "defer_invalidations", False):
        db.pending_rating = True
        return
    db.execute(
        "UPDATE rating_source_state SET source_revision=source_revision+1 WHERE id=1"
    )


def flush_invalidations(db):
    """End one bulk command: versions increment once, rating is locked last."""
    db.defer_invalidations = False
    for exam_id in sorted(getattr(db, "pending_configs", set())):
        bump_config(db, exam_id)
    db.pending_configs = set()
    if getattr(db, "pending_rating", False):
        invalidate_rating(db)
    db.pending_rating = False


def idempotency_key():
    raw = request.headers.get("Idempotency-Key", "")
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        fail("invalid_idempotency_key", "Требуется Idempotency-Key UUID")


def reserve_admin_command(db, actor, payload, exam_id=None):
    key = idempotency_key()
    request_hash = digest(
        {"path": request.path, "method": request.method, "payload": payload}
    )
    lookup = "SELECT * FROM exam_admin_commands WHERE actor_role=%s AND actor_id=%s AND idempotency_key=%s FOR UPDATE"
    lookup_params = (actor["role"], actor["id"], key)

    def replay(row):
        if row["request_hash"] != request_hash:
            fail(
                "idempotency_key_reused",
                "Этот ключ использован для другого действия",
                409,
            )
        if row["expires_at"] < now():
            fail(
                "idempotency_key_expired",
                "Создайте новое действие после обновления страницы",
                409,
            )
        return row, True

    row = db.one(lookup, lookup_params)
    if row:
        return replay(row)
    try:
        command_id = db.insert(
            "exam_admin_commands",
            {
                "actor_role": actor["role"],
                "actor_id": actor["id"],
                "idempotency_key": key,
                "command_type": request.method + ":" + (request.endpoint or "")[:50],
                "target_exam_id": exam_id,
                "request_hash": request_hash,
                "expires_at": now() + timedelta(hours=72),
            },
        )
    except IntegrityError as error:
        # Creation has no existing exam row to serialize competing requests.
        # Under RC both reservations may initially miss; UNIQUE waits for the
        # winner to commit. Re-read its finished receipt, not the whole command.
        if error.errno != 1062:
            raise
        row = db.one(lookup, lookup_params)
        if not row:
            raise
        return replay(row)
    return {"id": command_id, "receipt": None, "tombstone": False}, False


def save_admin_receipt(
    db, command, receipt, exam_id=None, student_id=None, attempt_id=None
):
    db.update(
        "exam_admin_commands",
        {
            "receipt": dumps(receipt),
            "target_exam_id": exam_id,
            "target_student_id": student_id,
            "target_attempt_id": attempt_id,
        },
        "id=%s",
        (command["id"],),
    )


def confirmation_token(actor, target, fingerprint):
    payload = {
        "actor": [actor["role"], actor["id"]],
        "target": target,
        "fingerprint": fingerprint,
    }
    serializer = URLSafeTimedSerializer(
        current_app.config["JWT_SECRET_KEY"], salt="classic-exam-delete-v2"
    )
    return {
        "confirmationToken": serializer.dumps(payload),
        "expiresAt": iso(now() + timedelta(minutes=10)),
    }


def verify_confirmation(actor, target, fingerprint):
    serializer = URLSafeTimedSerializer(
        current_app.config["JWT_SECRET_KEY"], salt="classic-exam-delete-v2"
    )
    try:
        data = serializer.loads(
            request.headers.get("X-Exam-Confirmation", ""), max_age=600
        )
    except (BadSignature, SignatureExpired):
        fail(
            "delete_confirmation_invalid",
            "Обновите предварительный просмотр удаления",
            409,
        )
    if (
        data.get("actor") != [actor["role"], actor["id"]]
        or data.get("target") != target
    ):
        fail("delete_confirmation_invalid", status=409)
    if data.get("fingerprint") != fingerprint:
        fail(
            "delete_preview_changed",
            "Данные изменились. Обновите предварительный просмотр.",
            409,
        )


def endpoint(*roles, capability=None, mutation=False):
    """Own uniform auth/errors. Global delegated admin policy remains authoritative."""

    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            correlation = str(uuid.uuid4())
            try:
                from cpm_back.auth.jwt_auth import get_current_user

                actor = get_current_user()
                if not actor:
                    fail("unauthorized", "Требуется авторизация", 401)
                if roles and actor.get("role") not in roles:
                    fail("forbidden", "Недостаточно прав", 403)
                if (
                    mutation or request.method not in ("GET", "HEAD", "OPTIONS")
                ) and not request.headers.get("Authorization", "").startswith(
                    "Bearer "
                ):
                    fail("bearer_token_required", "Требуется Bearer-токен", 401)
                if capability and not current_app.config.get(capability, False):
                    fail(
                        "exam_temporarily_unavailable",
                        "Раздел временно недоступен",
                        503,
                    )
                value = fn(*args, actor=actor, **kwargs)
                status = 200
                if isinstance(value, tuple):
                    value, status = value
                response = (
                    current_app.response_class(status=204)
                    if status == 204
                    else jsonify({"success": True, "data": jsonable(value)})
                )
                response.status_code = status
            except ExamError as error:
                response = jsonify(
                    {
                        "success": False,
                        "error": error.code,
                        "message": error.message,
                        "details": jsonable(error.details),
                        "correlationId": correlation,
                    }
                )
                response.status_code = error.status
                if error.status == 503 and error.details.get("retryAfterSeconds"):
                    response.headers["Retry-After"] = str(
                        error.details["retryAfterSeconds"]
                    )
            except HTTPException as error:
                code = "payload_too_large" if error.code == 413 else "invalid_request"
                response = jsonify(
                    {
                        "success": False,
                        "error": code,
                        "message": (
                            "Превышен размер запроса"
                            if error.code == 413
                            else "Некорректный запрос"
                        ),
                        "details": {},
                        "correlationId": correlation,
                    }
                )
                response.status_code = error.code
            except Exception as error:
                # SQL errors must never expose parameter values, question texts or credentials.
                errno = getattr(error, "errno", None)
                status = 409 if errno in (1062, 1205, 1213) else 500
                code = "concurrent_change" if status == 409 else "internal_error"
                current_app.logger.error(
                    "exam request failed correlation=%s endpoint=%s exception=%s",
                    correlation,
                    request.endpoint,
                    type(error).__name__,
                )
                response = jsonify(
                    {
                        "success": False,
                        "error": code,
                        "message": (
                            "Обновите данные и повторите действие"
                            if status == 409
                            else "Внутренняя ошибка сервера"
                        ),
                        "details": {},
                        "correlationId": correlation,
                    }
                )
                response.status_code = status
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Correlation-ID"] = correlation
            return response

        return wrapped

    return decorate
