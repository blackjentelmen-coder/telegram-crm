import sqlite3
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession

router = APIRouter(prefix="/auth", tags=["Telegram Auth"])

API_ID = 38977487
API_HASH = "a6afb062a4c36f6d7b2cb87d27385672"
DB_FILE = "crm_database.db"

class PhoneAuthRequest(BaseModel):
    phone: str

class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str

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

@router.post("/qr")
async def generate_qr():
    """Генерация QR-кода для входа"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        qr_login = await client.qr_login()
        return {
            "success": True, 
            "qr_url": qr_login.url,
            "token": list(qr_login.token) if hasattr(qr_login, 'token') else None
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await client.disconnect()

@router.post("/phone/send-code")
async def send_phone_code(data: PhoneAuthRequest):
    """Запрос кода подтверждения по номеру телефона"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        sent_code = await client.send_code_request(data.phone)
        return {
            "success": True,
            "phone_code_hash": sent_code.phone_code_hash,
            "message": "Код отправлен в Telegram."
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await client.disconnect()

@router.post("/phone/verify")
async def verify_phone_code(data: VerifyCodeRequest, user_id: int = Depends(get_current_user_id)):
    """Проверка кода, авторизация и сохранение сессии в БД"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(
            phone=data.phone,
            code=data.code,
            phone_code_hash=data.phone_code_hash
        )
        session_string = client.session.save()
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO accounts (user_id, phone, session_string, status) 
               VALUES (?, ?, ?, ?)""",
            (user_id, data.phone, session_string, "active")
        )
        conn.commit()
        conn.close()

        return {
            "success": True,
            "message": "Аккаунт успешно авторизован и привязан к системе!"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка входа: {str(e)}")
    finally:
        await client.disconnect()
