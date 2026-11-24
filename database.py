import sqlite3
from datetime import datetime

DB_NAME = "database.db"


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
            note TEXT
        )
    """)

    # Мягкая миграция на случай, если таблица уже существовала без status_text
    cols = [r["name"] for r in cursor.execute("PRAGMA table_info(access_keys)").fetchall()]
    if "status_text" not in cols:
        cursor.execute("ALTER TABLE access_keys ADD COLUMN status_text TEXT DEFAULT 'В сети'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_tg_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            phone TEXT NOT NULL,
            session_string TEXT NOT NULL,
            phone_code_hash TEXT,
            status TEXT DEFAULT 'Ожидает код'
        )
    """)

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


def get_client_profile(key_code: str):
    if not key_code:
        return None
    clean_key = key_code.strip().upper()
    conn = get_db()
    row = conn.execute(
        "SELECT id, key_code, status, client_name, status_text, note FROM access_keys WHERE key_code = ?",
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
    conn.close()

    data = dict(row)
    data["status_text"] = data.get("status_text") or "В сети"
    data["tg_accounts_count"] = acc_count
    data["tasks_count"] = task_count
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
