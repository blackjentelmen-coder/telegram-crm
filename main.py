import os
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from database import init_db, get_db
import admin
import uuid

app = FastAPI()

init_db()
app.include_router(admin.router)

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
    
    # Получаем аккаунты этого конкретного клиента
    cursor.execute("SELECT * FROM client_tg_accounts WHERE client_key = ? ORDER BY id DESC", (client_key,))
    my_accounts = cursor.fetchall()
    
    # История задач
    cursor.execute("SELECT * FROM client_tasks WHERE client_key = ? ORDER BY id DESC LIMIT 5", (client_key,))
    tasks = cursor.fetchall()
    db.close()
    
    if not key_row:
        return RedirectResponse(url="/logout-client", status_code=303)
        
    client_name = key_row["client_name"] or "Гость"
    
    # Рендерим аккаунты клиента
    accounts_html = ""
    for acc in my_accounts:
        accounts_html += f"""
        <div style="display: flex; justify-content: space-between; align-items: center; background: #0f172a; padding: 10px; border-radius: 4px; margin-bottom: 8px; font-size: 14px;">
            <div><b>{acc["phone"]}</b> <span style="color: #4ade80; margin-left: 10px; font-size: 12px;">[{acc["status"]}]</span></div>
            <form action="/cabinet/delete-account" method="POST" style="margin:0;">
                <input type="hidden" name="acc_id" value="{acc["id"]}">
                <button type="submit" style="padding: 4px 8px; font-size: 11px; background: #ef4444;">Удалить</button>
            </form>
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
        </style>
    </head>
    <body>
        <div class="wrapper">
            <!-- Шапка кабинета -->
            <div class="card" style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0 0 5px 0;">Клиент: {client_name}</h3>
                    <p style="margin: 0; font-size: 13px; color: #94a3b8;">Ключ: <code>{key_row["key_code"]}</code></p>
                </div>
                <a href="/logout-client" class="btn-exit">Выйти</a>
            </div>

            {msg_div}

            <!-- Управление аккаунтами -->
            <div class="card">
                <h3 style="margin-top:0;">Мои Telegram-аккаунты</h3>
                <p style="font-size: 13px; color: #94a3b8;">Добавьте аккаунты для работы парсера, инвайтера и рассылок.</p>
                
                <div style="margin-bottom: 15px;">
                    {accounts_html if accounts_html else '<p style="color: #64748b; font-size: 13px;">У вас пока нет добавленных аккаунтов.</p>'}
                </div>

                <form action="/cabinet/add-account" method="POST" style="display: flex; gap: 10px;">
                    <input type="text" name="phone" placeholder="Номер телефона (+380...)" required style="margin:0; flex-grow:1;">
                    <button type="submit" style="background: #10b981;">Добавить аккаунт</button>
                </form>
            </div>

            <!-- Запуск инструментов (Парсер / Инвайтер / Рассылки) -->
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

            <!-- История -->
            <div class="card">
                <h3 style="margin-top:0;">История задач</h3>
                {tasks_html if tasks_html else '<p style="color: #64748b; font-size: 13px;">История пока пуста.</p>'}
            </div>
        </div>
    </body>
    </html>
    """

@app.post("/cabinet/add-account")
def add_client_account(request: Request, phone: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    session_name = f"sess_{uuid.uuid4().hex[:6]}"
    
    cursor.execute(
        "INSERT INTO client_tg_accounts (client_key, phone, session_name, status) VALUES (?, ?, ?, 'Ожидает код SMS')",
        (client_key, phone.strip(), session_name)
    )
    db.commit()
    db.close()
    
    return RedirectResponse(url="/cabinet?message=Аккаунт добавлен. Скоро здесь появится запрос SMS-кода авторизации.", status_code=303)

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
    
    return RedirectResponse(url="/cabinet?message=Аккаунт успешно удален", status_code=303)

@app.post("/cabinet/run-task")
def run_client_task(request: Request, task_type: str = Form(...), target_link: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)
        
    db = get_db()
    cursor = db.cursor()
    
    # Проверяем, есть ли у клиента аккаунты
    cursor.execute("SELECT * FROM client_tg_accounts WHERE client_key = ?", (client_key,))
    accs = cursor.fetchall()
    
    if not accs:
        res_text = "Ошибка: Для выполнения задачи необходимо добавить хотя бы один Telegram-аккаунт!"
    else:
        res_text = f"Задача '{task_type}' успешно запущена с использованием {len(accs)} аккаунт(-ов)."
        
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
