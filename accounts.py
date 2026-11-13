import sqlite3
from pydantic import BaseModel

DB_FILE = "crm_database.db"

class AccountCreate(BaseModel):
    user_id: int
    phone: str
    proxy: str = ""

def add_account_to_db(data: AccountCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO accounts (user_id, phone, proxy, status) VALUES (?, ?, ?, ?)",
            (data.user_id, data.phone, data.proxy, "disconnected")
        )
        conn.commit()
        return {"success": True, "message": f"Аккаунт {data.phone} успешно добавлен."}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()

def get_user_accounts(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone, proxy, status FROM accounts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {"id": row[0], "phone": row[1], "proxy": row[2], "status": row[3]}
        for row in rows
    ]

def update_account_status(account_id: int, status: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET status = ? WHERE id = ?", (status, account_id))
    conn.commit()
    conn.close()
