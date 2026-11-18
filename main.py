import os
import asyncio
import qrcode
import io
import base64
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from database import init_db, get_db
import admin
import uuid
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 38977487
API_HASH = "a6afb062a4c36f6d7b2cb87d27385672"

app = FastAPI()

init_db()
app.include_router(admin.router)

# Временное хранилище активных процессов QR-авторизации в памяти сервера
qr_sessions = {}

@app.get("/", response_class=HTMLResponse)
def client_home(request: Request, error: str = None):
    if request.cookies.get("client_session"):
        return RedirectResponse(url="/cabinet", status_code=303)
        
    err_html = f'<div style="color: #f87171; margin-bottom: 15px; text-align: center;">{error}</div>' if error else ""
    
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Вход в систему</title>
        <style>
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
            .card {{ background: #1e293b; padding: 30px; border-radius: 8px; width: 360px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            input {{ width: 100%; padding: 10px; margin: 10px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; box-sizing: border-box; }}
            button {{ width: 100%; padding: 12px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; margin-top: 5px; }}
            button:hover {{ background: #2563eb; }}
            .divider {{ text-align: center; margin: 20px 0; color: #64748b; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2 style="text-align: center; margin-top: 0;">Вход в CRM</h2>
            {err_html}
            
            <form action="/login" method="POST">
                <input type="text" name="key_code" placeholder="Введите ваш ключ" required style="text-transform: uppercase;">
                <button type="submit">Войти по ключу</button>
            </form>
            
            <div class="divider">или</div>
            
            <form action="/register" method="POST">
                <input type="text" name="client_name" placeholder="Ваше Имя" required>
                <button type="submit" style="background: #10b981;">Получить ключ и войти</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/register")
def register_client(response: Response, client_name: str = Form(...)):
    db = get_db()
    cursor = db.cursor()
    new_key = str(uuid.uuid4())[:8].upper()
    cursor.execute(
        "INSERT INTO access_keys (key_code, status, client_name) VALUES (?, 'active', ?)",
        (new_key, client_name.strip())
    )
    db.commit()
    db.close()
    
    resp = RedirectResponse(url="/cabinet", status_code=303)
    resp.set_cookie(key="client_session", value=new_key, httponly=True)
    return resp

@app.post("/login")
def login_client(response: Response, key_code: str = Form(...)):
    clean_key = key_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM access_keys WHERE key_code = ?", (clean_key,))
    key_row = cursor.fetchone()
    db.close()
    
    if not key_row:
        return RedirectResponse(url="/?error=Неверный ключ доступа", status_code=303)
        
    if key_row["status"] == "free":
        db = get_db()
        cursor = db.cursor()
        cursor.execute("UPDATE access_keys SET status = 'active' WHERE id = ?", (key_row["id"],))
        db.commit()
        db.close()

    resp = RedirectResponse(url="/cabinet", status_code=303)
    resp.set_cookie(key="client_session", value=clean_key, httponly=True)
    return resp

@app.get("/cabinet", response_class=HTMLResponse)
def client_cabinet(request: Request):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM access_keys WHERE key_code = ?", (client_key,))
    key_row = cursor.fetchone()
    db.close()
    
    if not key_row:
        return RedirectResponse(url="/logout-client", status_code=303)
        
    # Возвращаем твой красивый файл index.html в стиле Telegram вместо старого текста
    return FileResponse("index.html")

@app.get("/cabinet/start-qr", response_class=HTMLResponse)
async def start_qr_auth(request: Request):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    try:
        qr = await client.qr_login()
        session_id = uuid.uuid4().hex
        qr_sessions[session_id] = {"client": client, "qr": qr}
        
        img = qrcode.make(qr.url)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8"><title>Авторизация по QR</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
                .card {{ background: #1e293b; padding: 30px; border-radius: 8px; width: 320px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
                img {{ background: white; padding: 10px; border-radius: 6px; margin: 15px 0; width: 200px; height: 200px; }}
                .btn-back {{ display: inline-block; margin-top: 15px; color: #94a3b8; text-decoration: none; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h3 style="margin-top: 0;">Сканируйте QR-код</h3>
                <p style="font-size: 13px; color: #94a3b8;">Откройте Telegram: <b>Настройки → Устройства → Подключить устройство</b></p>
                <img src="data:image/png;base64,{img_str}" alt="QR Code">
                <p style="font-size: 12px; color: #facc15;">Ожидание сканирования...</p>
                <a href="/cabinet/check-qr?sid={session_id}" style="display:inline-block; margin-top:10px; padding:8px 15px; background:#3b82f6; color:white; border-radius:4px; text-decoration:none; font-weight:bold; font-size:13px;">Проверить вход</a>
                <br>
                <a href="/cabinet" class="btn-back">← Назад в кабинет</a>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        await client.disconnect()
        return RedirectResponse(url=f"/cabinet?message=Ошибка запуска QR: {str(e)}", status_code=303)

@app.get("/cabinet/check-qr", response_class=HTMLResponse)
async def check_qr_auth(request: Request, sid: str):
    client_key = request.cookies.get("client_session")
    if not client_key or sid not in qr_sessions:
        return RedirectResponse(url="/cabinet", status_code=303)
        
    data = qr_sessions[sid]
    client = data["client"]
    qr = data["qr"]
    
    try:
        await qr.wait(timeout=3)
        me = await client.get_me()
        phone_str = f"+{me.phone}" if me and me.phone else "QR-Аккаунт"
        
        final_session_str = client.session.save()
        await client.disconnect()
        del qr_sessions[sid]
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO client_tg_accounts (client_key, phone, session_string, status) VALUES (?, ?, ?, 'Активен')",
            (client_key, phone_str, final_session_str)
        )
        db.commit()
        db.close()
        
        return RedirectResponse(url="/cabinet?message=QR-код успешно сканирован!", status_code=303)
    except asyncio.TimeoutError:
        return RedirectResponse(url=f"/cabinet/start-qr", status_code=303)
    except Exception as e:
        del qr_sessions[sid]
        return RedirectResponse(url=f"/cabinet?message=Ошибка QR: {str(e)}", status_code=303)

@app.post("/cabinet/add-account")
async def add_client_account(request: Request, phone: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    clean_phone = phone.strip()
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    try:
        sent = await client.send_code_request(clean_phone)
        phone_code_hash = sent.phone_code_hash
        session_str = client.session.save()
        await client.disconnect()
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO client_tg_accounts (client_key, phone, session_string, phone_code_hash, status) VALUES (?, ?, ?, ?, 'Ожидает код')",
            (client_key, clean_phone, session_str, phone_code_hash)
        )
        db.commit()
        db.close()
        
        return RedirectResponse(url="/cabinet?message=Код отправлен!", status_code=303)
    except Exception as e:
        await client.disconnect()
        return RedirectResponse(url=f"/cabinet?message=Ошибка запроса кода: {str(e)}", status_code=303)

@app.post("/cabinet/verify-code")
async def verify_client_code(request: Request, acc_id: int = Form(...), code: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM client_tg_accounts WHERE id = ? AND client_key = ?", (acc_id, client_key))
    acc = cursor.fetchone()
    db.close()
    
    if not acc:
        return RedirectResponse(url="/cabinet?message=Аккаунт не найден", status_code=303)
        
    client = TelegramClient(StringSession(acc["session_string"]), API_ID, API_HASH)
    await client.connect()
    
    try:
        await client.sign_in(phone=acc["phone"], code=code.strip(), phone_code_hash=acc["phone_code_hash"])
        final_session_str = client.session.save()
        await client.disconnect()
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE client_tg_accounts SET session_string = ?, status = 'Активен' WHERE id = ?",
            (final_session_str, acc_id)
        )
        db.commit()
        db.close()
        
        return RedirectResponse(url="/cabinet?message=Аккаунт успешно авторизован!", status_code=303)
    except Exception as e:
        await client.disconnect()
        return RedirectResponse(url=f"/cabinet?message=Ошибка авторизации: {str(e)}", status_code=303)

@app.post("/cabinet/delete-account")
def delete_client_account(request: Request, acc_id: int = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM client_tg_accounts WHERE id = ? AND client_key = ?", (acc_id, client_key))
    db.commit()
    db.close()
    
    return RedirectResponse(url="/cabinet?message=Аккаунт удален", status_code=303)

@app.post("/cabinet/run-task")
def run_client_task(request: Request, task_type: str = Form(...), target_link: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM client_tg_accounts WHERE client_key = ? AND status = 'Активен'", (client_key,))
    active_accs = cursor.fetchall()
    
    if not active_accs:
        res_text = "Ошибка: Нет активных авторизованных аккаунтов!"
    else:
        res_text = f"Задача '{task_type}' запущена. Аккаунтов: {len(active_accs)}."
        
    cursor.execute(
        "INSERT INTO client_tasks (client_key, task_type, target_link, result_text) VALUES (?, ?, ?, ?)",
        (client_key, task_type, target_link.strip(), res_text)
    )
    db.commit()
    db.close()
    
    return RedirectResponse(url="/cabinet?message=Задача обработана!", status_code=303)

@app.get("/logout-client")
def logout_client():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(key="client_session")
    return resp
