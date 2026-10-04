"""No runtime DDL, credentials, or user-supplied storage identifiers."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json

from cpm_back.db.mysql_pool import get_db_connection, close_db_connection


class DeletionError(Exception):
    def __init__(self, code, message=None, status=409):
        super().__init__(message or code)
        self.code, self.message, self.status = code, message or code, status


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {'sha256': hashlib.sha256(value).hexdigest()}
    return str(value)


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=json_default)


def loads(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


@contextmanager
def transaction(connection=None):
    own = connection is None
    connection = connection or get_db_connection()
    cursor = connection.cursor(dictionary=True, buffered=True)
    try:
        yield cursor
        if own:
            connection.commit()
    except Exception:
        if own:
            connection.rollback()
        raise
    finally:
        cursor.close()
        if own:
            close_db_connection(connection)


def rows(cursor, query, args=()):
    cursor.execute(query, args)
    return cursor.fetchall()


def one(cursor, query, args=()):
    cursor.execute(query, args)
    return cursor.fetchone()


def quoted(identifier):
    # Identifiers are code/schema constants, never request data.
    if not identifier.replace('_', '').isalnum():
        raise DeletionError('unsupported_schema', 'Недопустимое имя в схеме базы данных.', 503)
    return '`' + identifier + '`'
