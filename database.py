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
    
    db.commit()
    db.close()
