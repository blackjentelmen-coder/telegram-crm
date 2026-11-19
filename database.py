import sqlite3


def get_db():
    db = sqlite3.connect("database.db", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    cursor = db.cursor()

    # Ключи доступа клиентов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT NOT NULL,
            status TEXT DEFAULT 'free',
            client_name TEXT DEFAULT NULL,
            note TEXT DEFAULT NULL
        )
    """)

    # Аккаунты клиентов с поддержкой StringSession Telethon
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_tg_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            phone TEXT NOT NULL,
            session_string TEXT DEFAULT NULL,
            phone_code_hash TEXT DEFAULT NULL,
            status TEXT DEFAULT 'pending_code'
        )
    """)

    # История задач
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            task_type TEXT NOT NULL,
            target_link TEXT NOT NULL,
            result_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Настройки рекламного баннера на главной странице
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_banner (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_active INTEGER DEFAULT 0,
            title TEXT DEFAULT '',
            text TEXT DEFAULT '',
            url TEXT DEFAULT '',
            image_url TEXT DEFAULT ''
        )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO promo_banner (id, is_active, title, text, url, image_url)
        VALUES (1, 0, 'Промо', '', '', '')
    """)

    # Сообщения общего чата на главной странице
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.commit()
    db.close()


def get_banner():
    db = get_db()
    row = db.execute(
        "SELECT is_active, title, text, url, image_url FROM promo_banner WHERE id = 1"
    ).fetchone()
    db.close()
    if not row:
        return {
            "active": False,
            "title": "Промо",
            "text": "",
            "url": "",
            "image_url": ""
        }
    return {
        "active": bool(row["is_active"]),
        "title": row["title"] or "Промо",
        "text": row["text"] or "",
        "url": row["url"] or "",
        "image_url": row["image_url"] or ""
    }


def update_banner_db(is_active: bool, title: str, text: str, url: str, image_url: str | None = None):
    db = get_db()
    current = db.execute("SELECT image_url FROM promo_banner WHERE id = 1").fetchone()
    final_image = current["image_url"] if (current and image_url is None) else (image_url or "")

    db.execute(
        """
        UPDATE promo_banner
        SET is_active = ?, title = ?, text = ?, url = ?, image_url = ?
        WHERE id = 1
        """,
        (1 if is_active else 0, title, text, url, final_image)
    )
    db.commit()
    db.close()


def add_chat_message(author: str, text: str) -> dict:
    db = get_db()
    cursor = db.execute(
        "INSERT INTO chat_messages (author, text) VALUES (?, ?)",
        (author, text)
    )
    msg_id = cursor.lastrowid
    row = db.execute(
        "SELECT id, author, text, created_at FROM chat_messages WHERE id = ?",
        (msg_id,)
    ).fetchone()
    db.commit()
    db.close()
    return {
        "id": row["id"],
        "author": row["author"],
        "text": row["text"],
        "created_at": str(row["created_at"])
    }


def get_recent_chat_messages(limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT id, author, text, created_at
        FROM chat_messages
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,)
    ).fetchall()
    db.close()
    return [
        {
            "id": r["id"],
            "author": r["author"],
            "text": r["text"],
            "created_at": str(r["created_at"])
        }
        for r in reversed(rows)
    ]


def clear_chat_messages_db():
    db = get_db()
    db.execute("DELETE FROM chat_messages")
    db.commit()
    db.close()


def delete_chat_message_db(msg_id: int):
    db = get_db()
    db.execute("DELETE FROM chat_messages WHERE id = ?", (msg_id,))
    db.commit()
    db.close()
