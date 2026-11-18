import sqlite3

def get_db():
    db = sqlite3.connect("database.db", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    cursor = db.cursor()
    
    # Таблица ключей и клиентов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT NOT NULL,
            status TEXT DEFAULT 'free',
            client_name TEXT DEFAULT NULL,
            note TEXT DEFAULT NULL
        )
    """)
    
    # Таблица аккаунтов для парсера (телефоны/сессии)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parser_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            session_name TEXT NOT NULL,
            status TEXT DEFAULT 'active'
        )
    """)
    
    # История запросов парсинга от клиентов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parse_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key TEXT NOT NULL,
            target_link TEXT NOT NULL,
            result_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    db.commit()
    db.close()
