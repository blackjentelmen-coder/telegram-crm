import sqlite3

DB_FILE = "crm_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            access_key TEXT UNIQUE NOT NULL,
            tier TEXT DEFAULT 'light'
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            session_string TEXT,
            proxy TEXT,
            status TEXT DEFAULT 'disconnected'
        )
    """)
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
