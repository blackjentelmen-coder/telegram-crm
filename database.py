import sqlite3
from datetime import datetime

DB_NAME = "database.db"

ACCOUNT_STATUS_ACTIVE = "Активен"
ACCOUNT_STATUS_SPAM = "Спам"
ACCOUNT_STATUS_SESSION_LOST = "Слетела сессия"
ACCOUNT_STATUS_PENDING = "Ожидает код"


def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'free',
            client_name TEXT,
            status_text TEXT DEFAULT 'В сети',
            note TEXT,
            is_muted INTEGER NOT NULL DEFAULT 0,
            admin_note TEXT NOT NULL DEFAULT '',
            is_paid INTEGER NOT NULL DEFAULT 0,
            tg_user_id INTEGER,
            tg_username TEXT NOT NULL DEFAULT ''
        )
    """)

    cols = [r["name"] for r in cursor.execute("PRAGMA table_info(access_keys)").fetchall()]
    if "status_text" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN status_text TEXT DEFAULT 'В сети'")
    if "is_muted" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN is_muted INTEGER NOT NULL DEFAULT 0")
    if "admin_note" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN admin_note TEXT NOT NULL DEFAULT ''")
    if "is_paid" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN is_paid INTEGER NOT NULL DEFAULT 0")
    if "tg_user_id" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN tg_user_id INTEGER")
    if "tg_username" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN tg_username TEXT NOT NULL DEFAULT ''")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_tg_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            phone TEXT NOT NULL,
            session_string TEXT NOT NULL,
            phone_code_hash TEXT,
            status TEXT DEFAULT 'Ожидает код',
            status_reason TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL DEFAULT ''
        )
    """)

    acc_cols = [r["name"] for r in cursor.execute("PRAGMA table_info(client_tg_accounts)").fetchall()]
    if "status_reason" not in acc_cols:
        cursor.execute("ALTER TABLE client_tg_accounts ADD COLUMN status_reason TEXT NOT NULL DEFAULT ''")
    if "checked_at" not in acc_cols:
        cursor.execute("ALTER TABLE client_tg_accounts ADD COLUMN checked_at TEXT NOT NULL DEFAULT ''")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            task_type TEXT NOT NULL,
            target_link TEXT,
            result_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS site_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_banner (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_active INTEGER DEFAULT 0,
            title TEXT DEFAULT 'Промо',
            text TEXT DEFAULT '',
            url TEXT DEFAULT '',
            image_url TEXT DEFAULT ''
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO promo_banner (id, is_active, title, text, url, image_url)
        VALUES (1, 0, 'GHOSTCORE', '', '', '')
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def ensure_bot_schema():
    init_db()


def set_tg_account_status(account_id: int, status: str, reason: str = ""):
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_reason = (reason or "").strip()[:200]
    conn = get_db()
    conn.execute(
        """
        UPDATE client_tg_accounts
        SET status = ?, status_reason = ?, checked_at = ?
        WHERE id = ?
        """,
        (status, clean_reason, now_str, int(account_id)),
    )
    conn.commit()
    conn.close()


def add_site_notification(client_key: str, title: str, message: str, level: str = "info"):
    init_db()
    clean_key = (client_key or "").strip().upper()
    if not clean_key:
        return
    now_str = datetime.now().strftime("%H:%M")
    conn = get_db()
    conn.execute(
        """
        INSERT INTO site_notifications (client_key, level, title, message, is_read, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (clean_key, level, (title or "Уведомление")[:80], (message or "")[:300], now_str),
    )
    conn.commit()
    conn.close()


def get_site_notifications(client_key: str, limit: int = 30):
    init_db()
    clean_key = (client_key or "").strip().upper()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, level, title, message, is_read, created_at
        FROM site_notifications
        WHERE client_key = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (clean_key, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_notifications_read(client_key: str):
    init_db()
    clean_key = (client_key or "").strip().upper()
    conn = get_db()
    conn.execute(
        "UPDATE site_notifications SET is_read = 1 WHERE client_key = ?",
        (clean_key,),
    )
    conn.commit()
    conn.close()


def get_client_profile(key_code: str):
    if not key_code:
        return None
    init_db()
    clean_key = key_code.strip().upper()
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, key_code, status, client_name, status_text, note,
               is_muted, admin_note, is_paid, tg_user_id, tg_username
        FROM access_keys
        WHERE key_code = ?
        """,
        (clean_key,),
    ).fetchone()
    if not row:
        conn.close()
        return None

    acc_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM client_tg_accounts WHERE client_key = ?",
        (clean_key,),
    ).fetchone()["cnt"]

    task_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM client_tasks WHERE client_key = ?",
        (clean_key,),
    ).fetchone()["cnt"]

    unread_notif_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM site_notifications WHERE client_key = ? AND is_read = 0",
        (clean_key,),
    ).fetchone()["cnt"]
    conn.close()

    data = dict(row)
    data["status_text"] = data.get("status_text") or "В сети"
    data["tg_accounts_count"] = acc_count
    data["tasks_count"] = task_count
    data["unread_notifications_count"] = unread_notif_count
    return data


def update_client_profile(key_code: str, client_name: str, status_text: str):
    clean_key = key_code.strip().upper()
    clean_name = (client_name or "").strip()[:40]
    clean_status = (status_text or "").strip()[:80] or "В сети"

    conn = get_db()
    conn.execute(
        "UPDATE access_keys SET client_name = ?, status_text = ? WHERE key_code = ?",
        (clean_name or None, clean_status, clean_key),
    )
    conn.commit()
    conn.close()


def get_banner():
    conn = get_db()
    row = conn.execute("SELECT * FROM promo_banner WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return {"active": False, "title": "GHOSTCORE", "text": "", "url": "", "image_url": ""}
    return {
        "active": bool(row["is_active"]),
        "title": row["title"] or "GHOSTCORE",
        "text": row["text"] or "",
        "url": row["url"] or "",
        "image_url": row["image_url"] or "",
    }


def update_banner_db(is_active: bool, title: str, text: str, url: str, image_url: str = None):
    conn = get_db()
    current = conn.execute("SELECT image_url FROM promo_banner WHERE id = 1").fetchone()
    final_img = current["image_url"] if current else ""
    if image_url is not None:
        final_img = image_url

    conn.execute(
        """
        UPDATE promo_banner
        SET is_active = ?, title = ?, text = ?, url = ?, image_url = ?
        WHERE id = 1
        """,
        (1 if is_active else 0, title, text, url, final_img),
    )
    conn.commit()
    conn.close()


def add_chat_message(author: str, text: str):
    now_str = datetime.now().strftime("%H:%M")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_messages (author, text, created_at) VALUES (?, ?, ?)",
        (author, text, now_str),
    )
    msg_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": msg_id, "author": author, "text": text, "created_at": now_str}


def get_recent_chat_messages(limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, author, text, created_at FROM chat_messages ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def delete_chat_message_db(msg_id: int):
    conn = get_db()
    conn.execute("DELETE FROM chat_messages WHERE id = ?", (msg_id,))
    conn.commit()
    conn.close()


def clear_chat_messages_db():
    conn = get_db()
    conn.execute("DELETE FROM chat_messages")
    conn.commit()
    conn.close()


def link_tg_to_key(key_code: str, tg_user_id: int, tg_username: str = "") -> dict:
    init_db()
    clean_key = (key_code or "").strip().upper()
    conn = get_db()
    row = conn.execute(
        "SELECT id, key_code, client_name, status, is_paid FROM access_keys WHERE key_code = ?",
        (clean_key,),
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "Ключ не найден."}
    if (row["status"] or "").lower() == "banned":
        conn.close()
        return {"ok": False, "error": "Этот ключ заблокирован."}

    conn.execute(
        "UPDATE access_keys SET tg_user_id = ?, tg_username = ? WHERE id = ?",
        (int(tg_user_id), (tg_username or "").strip(), row["id"]),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "user": dict(row)}


def get_user_by_tg_id(tg_user_id: int):
    init_db()
    conn = get_db()
    row = conn.execute(
        "SELECT id, key_code, client_name, status, is_paid, tg_user_id FROM access_keys WHERE tg_user_id = ?",
        (int(tg_user_id),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    if (data.get("status") or "").lower() == "banned":
        return None
    return data


def get_user_by_key(key_code: str):
    init_db()
    clean_key = (key_code or "").strip().upper()
    conn = get_db()
    row = conn.execute(
        "SELECT id, key_code, client_name, status, is_paid, tg_user_id FROM access_keys WHERE key_code = ?",
        (clean_key,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_client_tg_accounts(client_key: str) -> list:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, client_key, phone, session_string, status, status_reason, checked_at
        FROM client_tg_accounts
        WHERE client_key = ? AND status = ?
        ORDER BY id DESC
        """,
        ((client_key or "").strip().upper(), ACCOUNT_STATUS_ACTIVE),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
