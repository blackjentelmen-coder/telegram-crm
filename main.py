import os
import asyncio
import qrcode
import io
import base64
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
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
def client_cabinet(request: Request, message: str = None):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM access_keys WHERE key_code = ?", (client_key,))
    key_row = cursor.fetchone()
    
    cursor.execute("SELECT * FROM client_tg_accounts WHERE client_key = ? ORDER BY id DESC", (client_key,))
    my_accounts = cursor.fetchall()
    
    cursor.execute("SELECT * FROM client_tasks WHERE client_key = ? ORDER BY id DESC LIMIT 5", (client_key,))
    tasks = cursor.fetchall()
    db.close()
    
    if not key_row:
        return RedirectResponse(url="/logout-client", status_code=303)
        
    client_name = key_row["client_name"] or "Гость"
    
    accounts_html = ""
    for acc in my_accounts:
        status_color = "#4ade80" if acc["status"] == "Активен" else "#f87171"
        
        if acc["status"] == "Ожидает код":
            action_form = f"""
            <form action="/cabinet/verify-code" method="POST" style="display: flex; gap: 5px; margin-top: 8px;">
                <input type="hidden" name="acc_id" value="{acc["id"]}">
                <input type="text" name="code" placeholder="Код из ТГ..." required style="padding: 6px; width: 120px; margin: 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; font-size: 13px;">
                <button type="submit" style="padding: 6px 12px; font-size: 12px; background: #10b981;">Подтвердить</button>
            </form>
            """
        else:
            action_form = ""

        accounts_html += f"""
        <div style="background: #0f172a; padding: 12px; border-radius: 6px; margin-bottom: 10px; font-size: 14px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div><b>{acc["phone"]}</b> <span style="color: {status_color}; margin-left: 10px; font-size: 12px;">[{acc["status"]}]</span></div>
                <form action="/cabinet/delete-account" method="POST" style="margin:0;">
                    <input type="hidden" name="acc_id" value="{acc["id"]}">
                    <button type="submit" style="padding: 4px 8px; font-size: 11px; background: #ef4444;">Удалить</button>
                </form>
            </div>
            {action_form}
        </div>
        """
        
    tasks_html = ""
    for t in tasks:
        tasks_html += f"""
        <div style="background: #0f172a; padding: 10px; border-radius: 4px; margin-bottom: 8px; font-size: 13px;">
            <div><b>[{t["task_type"]}]</b> ссылка: <a href="{t["target_link"]}" target="_blank" style="color: #60a5fa;">{t["target_link"]}</a></div>
            <div style="margin-top: 4px; color: #4ade80;">{t["result_text"]}</div>
        </div>
        """
        
    msg_div = f'<div style="background: #065f46; color: #6ee7b7; padding: 10px; border-radius: 4px; margin-bottom: 15px; font-size: 14px;">{message}</div>' if message else ""

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Личный кабинет</title>
        <style>
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; display: flex; justify-content: center; }}
            .wrapper {{ width: 650px; display: flex; flex-direction: column; gap: 20px; }}
            .card {{ background: #1e293b; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            code {{ background: #0f172a; padding: 4px 8px; border-radius: 4px; color: #4ade80; }}
            input[type="text"] {{ width: 100%; padding: 10px; margin: 8px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; box-sizing: border-box; }}
            select {{ width: 100%; padding: 10px; margin: 8px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; box-sizing: border-box; }}
            button {{ padding: 10px 15px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }}
            button:hover {{ background: #2563eb; }}
            .btn-exit {{ background: #ef4444; color: white; padding: 8px 12px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold; font-size: 13px; }}
            .auth-box {{ display: flex; gap: 20px; margin-top: 15px; }}
            .auth-option {{ flex: 1; background: #0f172a; padding: 15px; border-radius: 6px; border: 1px solid #334155; }}
        </style>
    </head>
    <body>
        <div class="wrapper">
            <div class="card" style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0 0 5px 0;">Клиент: {client_name}</h3>
                    <p style="margin: 0; font-size: 13px; color: #94a3b8;">Ключ: <code>{key_row["key_code"]}</code></p>
                </div>
                <a href="/logout-client" class="btn-exit">Выйти</a>
            </div>

            {msg_div}

            <div class="card">
                <h3 style="margin-top:0;">Мои Telegram-аккаунты</h3>
                <div style="margin-bottom: 15px;">
                    {accounts_html if accounts_html else '<p style="color: #64748b; font-size: 13px;">У вас пока нет добавленных аккаунтов.</p>'}
                </div>

                <div class="auth-box">
                    <!-- Способ 1: По номеру -->
                    <div class="auth-option">
                        <h4 style="margin: 0 0 10px 0; color: #60a5fa; font-size: 14px;">Вход по номеру (SMS)</h4>
                        <form action="/cabinet/add-account" method="POST">
                            <input type="text" name="phone" placeholder="+380..." required style="margin: 0 0 10px 0;">
                            <button type="submit" style="width: 100%; background: #10b981; font-size: 13px;">Запросить код</button>
                        </form>
                    </div>

                    <!-- Способ 2: По QR-коду -->
                    <div class="auth-option" style="text-align: center;">
                        <h4 style="margin: 0 0 10px 0; color: #c084fc; font-size: 14px;">Вход по QR-коду</h4>
                        <form action="/cabinet/start-qr" method="GET">
                            <button type="submit" style="width: 100%; background: #9333ea; font-size: 13px;">Получить QR-код</button>
                        </form>
                    </div>
                </div>
            </div>

            <div class="card">
                <h3 style="margin-top:0;">Панель инструментов</h3>
                <form action="/cabinet/run-task" method="POST">
                    <label style="font-size: 13px; color: #94a3b8;">Выберите инструмент:</label>
                    <select name="task_type">
                        <option value="Парсер">Парсер (проверка наличия / сбор чата)</option>
                        <option value="Инвайтер">Инвайтер (добавление в чат)</option>
                        <option value="Рассылка">Рассылка (SMS по контактам)</option>
                    </select>

                    <label style="font-size: 13px; color: #94a3b8; display: block; margin-top: 10px;">Ссылка на ресурс / чат / пост:</label>
                    <input type="text" name="target_link" placeholder="https://t.me/..." required>
                    
                    <button type="submit" style="margin-top: 10px; width: 100%;">Запустить задачу</button>
                </form>
            </div>

            <div class="card">
                <h3 style="margin-top:0;">История задач</h3>
                {tasks_html if tasks_html else '<p style="color: #64748b; font-size: 13px;">История пока пуста.</p>'}
            </div>
        </div>
    </body>
    </html>
    """

@app.get("/cabinet/start-qr", response_class=HTMLResponse)
async def start_qr_auth(request: Request):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    try:
        qr = await client.qr_login()
        # Сохраняем клиент во временный словарь для проверки сканирования
        session_id = uuid.uuid4().hex
        qr_sessions[session_id] = {"client": client, "qr": qr}
        
        # Генерируем картинку QR-кода в base64
        img = qrcode.make(qr.url)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8"><title>Авторизация по QR</title>
            <meta http-equiv="refresh" content="5"> <!-- Автообновление страницы каждые 5 сек для проверки статуса -->
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
                <p style="font-size: 13px; color: #94a3b8;">Откройте Telegram на телефоне: <b>Настройки → Устройства → Подключить устройство</b></p>
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
        # Проверяем, отсканировал ли пользователь QR
        await qr.wait(timeout=3)
        
        # Если успешно авторизовались:
        phone = client.get_me().phone if hasattr(client, 'get_me') else "Аккаунт QR"
        # Получаем номер или подставляем заглушку, если api не возвращает телефон сразу
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
        
        return RedirectResponse(url="/cabinet?message=QR-код успешно сканирован, аккаунт активирован!", status_code=303)
    except asyncio.TimeoutError:
        # Еще не отсканировал, перенаправляем обратно на экран QR
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
        
        return RedirectResponse(url="/cabinet?message=Код отправлен в официальный Telegram-аккаунт номера!", status_code=303)
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
        res_text = "Ошибка: Нет активных авторизованных аккаунтов для выполнения задачи!"
    else:
        res_text = f"Задача '{task_type}' запущена. Используется активных аккаунтов: {len(active_accs)}."
        
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
