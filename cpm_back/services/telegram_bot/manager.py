from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.serv.student_plain_credentials import ensure_student_credentials_table

DEFAULT_WELCOME_TEXT = (
    "Здравствуйте, {full_name}! Я помогу получить доступ к личному кабинету CPM."
)
DEFAULT_NOT_FOUND_TEXT = (
    "Ученик с таким Telegram не найден. Проверьте никнейм у администратора."
)
DEFAULT_CREDENTIALS_TEXT = (
    "Ваши данные для входа в CPM:\n\nЛогин: `{login}`\nПароль: `{password}`"
)
DEFAULT_BUTTON_LABEL = "Узнать логин и пароль"
NO_USERNAME_TEXT = "Не удалось определить Telegram username. Обратитесь к администратору."
NO_PASSWORD_TEXT = "Пароль не найден, попросите администратора задать новый."

_bot_lock = threading.Lock()
_bot_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None
_running = False
_started_at: Optional[datetime] = None
_last_update_at: Optional[datetime] = None
_last_error: Optional[str] = None
_restart_required = False


def normalize_username(username: Optional[str]) -> str:
    return str(username or "").strip().lstrip("@").lower()


def _now_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def ensure_schema(cursor) -> None:
    ensure_student_credentials_table(cursor)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_bot_settings (
            id TINYINT NOT NULL PRIMARY KEY,
            bot_token VARCHAR(255) NULL,
            autostart TINYINT(1) NOT NULL DEFAULT 0,
            welcome_text TEXT NULL,
            not_found_text TEXT NULL,
            credentials_text TEXT NULL,
            button_label VARCHAR(255) NOT NULL DEFAULT 'Узнать логин и пароль',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO telegram_bot_settings (
            id,
            welcome_text,
            not_found_text,
            credentials_text,
            button_label
        )
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE id = id
        """,
        (
            1,
            DEFAULT_WELCOME_TEXT,
            DEFAULT_NOT_FOUND_TEXT,
            DEFAULT_CREDENTIALS_TEXT,
            DEFAULT_BUTTON_LABEL,
        ),
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_bot_chats (
            chat_id BIGINT NOT NULL PRIMARY KEY,
            student_id INT NULL,
            telegram_username VARCHAR(255) NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            last_credentials_sent_at TIMESTAMP NULL,
            messages_count INT NOT NULL DEFAULT 0,
            last_error TEXT NULL,
            INDEX idx_telegram_bot_chats_student (student_id),
            CONSTRAINT fk_telegram_bot_chats_student
                FOREIGN KEY (student_id) REFERENCES students(id)
                ON DELETE SET NULL
        )
        """
    )


def _serialize_settings(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "bot_token": row.get("bot_token") or "",
        "token_configured": bool(row.get("bot_token")),
        "autostart": bool(row.get("autostart")),
        "welcome_text": row.get("welcome_text") or DEFAULT_WELCOME_TEXT,
        "not_found_text": row.get("not_found_text") or DEFAULT_NOT_FOUND_TEXT,
        "credentials_text": row.get("credentials_text") or DEFAULT_CREDENTIALS_TEXT,
        "button_label": row.get("button_label") or DEFAULT_BUTTON_LABEL,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def get_settings() -> Dict[str, Any]:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        ensure_schema(cursor)
        conn.commit()
        cursor.execute("SELECT * FROM telegram_bot_settings WHERE id = 1")
        return _serialize_settings(cursor.fetchone() or {})
    finally:
        if conn:
            close_db_connection(conn)


def save_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    global _restart_required
    current = get_settings()
    next_token = str(data.get("bot_token", current["bot_token"]) or "").strip()
    token_changed = next_token != (current.get("bot_token") or "")

    autostart = data.get("autostart", current["autostart"])
    next_settings = {
        "bot_token": next_token,
        "autostart": 1 if bool(autostart) else 0,
        "welcome_text": str(data.get("welcome_text", current["welcome_text"]) or "").strip()
        or DEFAULT_WELCOME_TEXT,
        "not_found_text": str(data.get("not_found_text", current["not_found_text"]) or "").strip()
        or DEFAULT_NOT_FOUND_TEXT,
        "credentials_text": str(
            data.get("credentials_text", current["credentials_text"]) or ""
        ).strip()
        or DEFAULT_CREDENTIALS_TEXT,
        "button_label": str(data.get("button_label", current["button_label"]) or "").strip()
        or DEFAULT_BUTTON_LABEL,
    }

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        ensure_schema(cursor)
        cursor.execute(
            """
            UPDATE telegram_bot_settings
            SET bot_token = %s,
                autostart = %s,
                welcome_text = %s,
                not_found_text = %s,
                credentials_text = %s,
                button_label = %s
            WHERE id = 1
            """,
            (
                next_settings["bot_token"],
                next_settings["autostart"],
                next_settings["welcome_text"],
                next_settings["not_found_text"],
                next_settings["credentials_text"],
                next_settings["button_label"],
            ),
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            close_db_connection(conn)

    if token_changed and is_running():
        _restart_required = True
    return {"status": True, "settings": get_settings(), "restart_required": _restart_required}


def _chat_stats(cursor) -> Dict[str, int]:
    ensure_schema(cursor)
    cursor.execute(
        """
        SELECT
            COUNT(*) AS chats_total,
            SUM(CASE WHEN student_id IS NULL THEN 0 ELSE 1 END) AS linked_students
        FROM telegram_bot_chats
        """
    )
    row = cursor.fetchone() or {}
    return {
        "chats_total": int(row.get("chats_total") or 0),
        "linked_students": int(row.get("linked_students") or 0),
    }


def is_running() -> bool:
    with _bot_lock:
        return bool(_running and _bot_thread and _bot_thread.is_alive())


def get_status() -> Dict[str, Any]:
    settings = get_settings()
    conn = None
    stats = {"chats_total": 0, "linked_students": 0}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        stats = _chat_stats(cursor)
        conn.commit()
    finally:
        if conn:
            close_db_connection(conn)

    return {
        "status": True,
        "running": is_running(),
        "restart_required": _restart_required,
        "token_configured": settings["token_configured"],
        "autostart": settings["autostart"],
        "started_at": _now_iso(_started_at),
        "last_update_at": _now_iso(_last_update_at),
        "last_error": _last_error,
        **stats,
    }


def _telegram_request(token: str, method: str, payload: Dict[str, Any], timeout: int = 35) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _send_message(
    token: str,
    chat_id: int,
    text: str,
    *,
    button_label: Optional[str] = None,
) -> None:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if button_label:
        payload["reply_markup"] = {
            "keyboard": [[{"text": button_label}]],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
    _telegram_request(token, "sendMessage", payload, timeout=10)


def _find_student_by_username(cursor, username: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_username(username)
    if not normalized:
        return None
    cursor.execute(
        """
        SELECT
            s.id,
            s.full_name,
            s.class,
            s.group_id,
            s.tg_name,
            COALESCE(sc.login, a.username) AS login,
            sc.password AS password
        FROM students s
        LEFT JOIN auth_users a ON a.ref_id = s.id AND a.role = 'student'
        LEFT JOIN student_credentials sc ON sc.student_id = s.id
        WHERE LOWER(TRIM(REPLACE(s.tg_name, '@', ''))) = %s
        LIMIT 1
        """,
        (normalized,),
    )
    return cursor.fetchone()


def _upsert_chat(cursor, chat_id: int, username: str, student_id: Optional[int], error: Optional[str] = None) -> None:
    cursor.execute(
        """
        INSERT INTO telegram_bot_chats (
            chat_id,
            student_id,
            telegram_username,
            messages_count,
            last_error
        )
        VALUES (%s, %s, %s, 1, %s)
        ON DUPLICATE KEY UPDATE
            student_id = VALUES(student_id),
            telegram_username = VALUES(telegram_username),
            messages_count = messages_count + 1,
            last_error = VALUES(last_error),
            last_seen_at = CURRENT_TIMESTAMP
        """,
        (chat_id, student_id, normalize_username(username), error),
    )


def _mark_credentials_sent(cursor, chat_id: int) -> None:
    cursor.execute(
        """
        UPDATE telegram_bot_chats
        SET last_credentials_sent_at = CURRENT_TIMESTAMP,
            messages_count = messages_count + 1,
            last_error = NULL
        WHERE chat_id = %s
        """,
        (chat_id,),
    )


def _format_template(template: str, student: Dict[str, Any]) -> str:
    values = {
        "full_name": student.get("full_name") or "",
        "login": student.get("login") or "",
        "password": student.get("password") or "",
        "class": student.get("class") or "",
        "tg_name": student.get("tg_name") or "",
    }
    try:
        return template.format(**values)
    except Exception:
        return DEFAULT_CREDENTIALS_TEXT.format(**values)


def _handle_start(token: str, settings: Dict[str, Any], chat_id: int, username: str) -> None:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        ensure_schema(cursor)

        if not normalize_username(username):
            _upsert_chat(cursor, chat_id, "", None, NO_USERNAME_TEXT)
            conn.commit()
            _send_message(token, chat_id, NO_USERNAME_TEXT)
            return

        student = _find_student_by_username(cursor, username)
        if not student:
            _upsert_chat(cursor, chat_id, username, None, "student_not_found")
            conn.commit()
            _send_message(token, chat_id, settings["not_found_text"])
            return

        _upsert_chat(cursor, chat_id, username, student["id"], None)
        conn.commit()
        text = _format_template(settings["welcome_text"], student)
        _send_message(token, chat_id, text, button_label=settings["button_label"])
    finally:
        if conn:
            close_db_connection(conn)


def _handle_credentials(token: str, settings: Dict[str, Any], chat_id: int) -> None:
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        ensure_schema(cursor)
        cursor.execute(
            """
            SELECT
                s.id,
                s.full_name,
                s.class,
                s.group_id,
                s.tg_name,
                COALESCE(sc.login, a.username) AS login,
                sc.password AS password
            FROM telegram_bot_chats c
            JOIN students s ON s.id = c.student_id
            LEFT JOIN auth_users a ON a.ref_id = s.id AND a.role = 'student'
            LEFT JOIN student_credentials sc ON sc.student_id = s.id
            WHERE c.chat_id = %s
            LIMIT 1
            """,
            (chat_id,),
        )
        student = cursor.fetchone()
        if not student:
            _upsert_chat(cursor, chat_id, "", None, "chat_not_linked")
            conn.commit()
            _send_message(token, chat_id, settings["not_found_text"])
            return
        if not student.get("password"):
            _upsert_chat(cursor, chat_id, student.get("tg_name") or "", student["id"], NO_PASSWORD_TEXT)
            conn.commit()
            _send_message(token, chat_id, NO_PASSWORD_TEXT, button_label=settings["button_label"])
            return

        _mark_credentials_sent(cursor, chat_id)
        conn.commit()
        _send_message(
            token,
            chat_id,
            _format_template(settings["credentials_text"], student),
            button_label=settings["button_label"],
        )
    finally:
        if conn:
            close_db_connection(conn)


def _handle_update(token: str, settings: Dict[str, Any], update: Dict[str, Any]) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    text = str(message.get("text") or "").strip()
    username = sender.get("username") or chat.get("username") or ""
    if not chat_id or not text:
        return
    if text.startswith("/start"):
        _handle_start(token, settings, int(chat_id), username)
        return
    if text == settings["button_label"]:
        _handle_credentials(token, settings, int(chat_id))


def _poll_loop(token: str, stop_event: threading.Event) -> None:
    global _last_error, _last_update_at, _running
    offset: Optional[int] = None
    while not stop_event.is_set():
        try:
            payload: Dict[str, Any] = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            response = _telegram_request(token, "getUpdates", payload, timeout=35)
            if not response.get("ok"):
                raise RuntimeError(response.get("description") or "Telegram API error")
            settings = get_settings()
            for update in response.get("result") or []:
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = int(update_id) + 1
                _last_update_at = datetime.utcnow()
                _handle_update(token, settings, update)
            _last_error = None
        except urllib.error.HTTPError as exc:
            _last_error = f"Telegram HTTP {exc.code}: {exc.reason}"
            time.sleep(5)
        except Exception as exc:
            _last_error = str(exc)
            time.sleep(5)
    _running = False


def start_bot() -> Dict[str, Any]:
    global _bot_thread, _running, _started_at, _stop_event, _last_error, _restart_required
    settings = get_settings()
    token = settings.get("bot_token")
    if not token:
        return {"status": False, "error": "Укажите Telegram bot token"}

    with _bot_lock:
        if _running and _bot_thread and _bot_thread.is_alive():
            already_running = True
        else:
            already_running = False

    if already_running:
        return {"status": True, "message": "Бот уже запущен", "bot": get_status()}

    with _bot_lock:
        _stop_event = threading.Event()
        _running = True
        _started_at = datetime.utcnow()
        _last_error = None
        _restart_required = False
        _bot_thread = threading.Thread(
            target=_poll_loop,
            args=(token, _stop_event),
            name="telegram-credentials-bot",
            daemon=True,
        )
        _bot_thread.start()

    return {"status": True, "message": "Бот запущен", "bot": get_status()}


def stop_bot() -> Dict[str, Any]:
    global _running, _restart_required
    with _bot_lock:
        if _stop_event:
            _stop_event.set()
        _running = False
        _restart_required = False
    return {"status": True, "message": "Бот остановлен", "bot": get_status()}


def restart_bot() -> Dict[str, Any]:
    stop_bot()
    time.sleep(0.2)
    return start_bot()


def start_bot_if_configured() -> None:
    try:
        settings = get_settings()
        if settings.get("autostart") and settings.get("bot_token"):
            start_bot()
    except Exception as exc:
        global _last_error
        _last_error = str(exc)
