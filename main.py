from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3

# Импортируем роутер авторизации и наш парсер
from api.telethon_auth import router as auth_router
from parser import TelegramParser

app = FastAPI(title="Telegram CRM", version="1.0")
DB_FILE = "crm_database.db"

# Подключаем роутер авторизации
app.include_router(auth_router)

class RegisterKey(BaseModel):
    access_key: str
    tier: str = 'light'

class AccountAdd(BaseModel):
    phone: str
    proxy: str = ""

class ParseRequest(BaseModel):
    account_phone: str      # Телефон аккаунта, с которого запускаем парсинг
    target: str             # Ссылка на чат, канал или конкретное сообщение
    limit_messages: Optional[int] = 5000  # Глубина истории (5000, 10000, 20000)

# Функция проверки ключа и получения ID пользователя
def get_current_user_id(x_access_key: str = Header(...)) -> int:
    if len(x_access_key) < 12:
        raise HTTPException(status_code=400, detail="Ключ должен содержать минимум 12 символов.")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE access_key = ?", (x_access_key,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=403, detail="Неверный ключ доступа.")
    return user[0]

@app.get("/", response_class=HTMLResponse)
async def admin_panel():
    return """
    <html>
        <body>
            <h1>Админ-панель Telegram CRM</h1>
            <p>Мультиаккаунтная система управления.</p>
        </body>
    </html>
    """

@app.post("/auth/register")
def register_key(data: RegisterKey):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (access_key, tier) VALUES (?, ?)", (data.access_key, data.tier))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Ключ зарегистрирован."}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Ключ уже существует.")

# Получение аккаунтов ТОЛЬКО текущего пользователя
@app.get("/accounts")
def get_accounts(user_id: int = Depends(get_current_user_id)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone, proxy, status FROM accounts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {"accounts": [{"id": r[0], "phone": r[1], "proxy": r[2], "status": r[3]} for r in rows]}

# Добавление аккаунта к профилю текущего пользователя
@app.post("/accounts/add")
def add_account(data: AccountAdd, user_id: int = Depends(get_current_user_id)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO accounts (user_id, phone, proxy, status) VALUES (?, ?, ?, ?)",
            (user_id, data.phone, data.proxy, "disconnected")
        )
        conn.commit()
        return {"success": True, "message": f"Аккаунт {data.phone} привязан к вашему профилю."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

# Эндпоинт запуска парсера
@app.post("/parser/run")
async def run_parser(data: ParseRequest, user_id: int = Depends(get_current_user_id)):
    """Запуск парсера участников чата или по истории сообщений с выбором лимита"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT session_string FROM accounts WHERE user_id = ? AND phone = ? AND status = 'active'",
        (user_id, data.account_phone)
    )
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row[0]:
        raise HTTPException(
            status_code=400, 
            detail=f"Аккаунт {data.account_phone} не найден или не имеет активной сессии."
        )
    
    session_string = row[0]
    parser = TelegramParser(session_string=session_string)
    
    # Запускаем парсинг
    result = await parser.parse_chat_members(
        target=data.target, 
        user_id=user_id, 
        limit_messages=data.limit_messages
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
        
    return {
        "success": True,
        "message": f"Успешно собрано уникальных пользователей: {result['count']}",
        "count": result["count"]
    }
