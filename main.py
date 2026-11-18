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
    # Если у клиента уже есть валидная кука сессии, перекидываем в кабинет
    if request.cookies.get("client_session"):
        return RedirectResponse(url="/cabinet", status_code=303)
        
    err_html = f'<div style="color: #f87171; margin-bottom: 15px; text-align: center;">{error}</div>' if error else ""
    
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Регистрация и Вход</title>
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
            <h2 style="text-align: center; margin-top: 0;">Добро пожаловать</h2>
            {err_html}
            
            <!-- Вариант 1: У клиента уже есть ключ -->
            <form action="/login" method="POST">
                <input type="text" name="key_code" placeholder="Введите ваш ключ" required style="text-transform: uppercase;">
                <button type="submit">Войти по ключу</button>
            </form>
            
            <div class="divider">или</div>
            
            <!-- Вариант 2: Получить новый ключ и зарегистрироваться -->
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
    
    # Генерируем уникальный ключ для клиента
    new_key = str(uuid.uuid4())[:8].upper()
    
    cursor.execute(
        "INSERT INTO access_keys (key_code, status, client_name) VALUES (?, 'active', ?)",
        (new_key, client_name.strip())
    )
    db.commit()
    db.close()
    
    # Сразу авторизуем клиента через куку по его ключу
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
        
    # Если ключ был свободен — активируем при входе
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
        
    client_name = key_row["client_name"] or "Гость"
    
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Личный кабинет</title>
        <style>
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; display: flex; justify-content: center; }}
            .cabinet {{ background: #1e293b; padding: 30px; border-radius: 8px; width: 400px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); text-align: center; }}
            code {{ background: #0f172a; padding: 8px 12px; border-radius: 4px; font-size: 18px; color: #4ade80; display: inline-block; margin: 10px 0; letter-spacing: 2px; }}
            .btn-exit {{ background: #ef4444; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block; margin-top: 20px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="cabinet">
            <h2>Личный кабинет</h2>
            <p>Рады видеть вас, <b>{client_name}</b>!</p>
            <p>Ваш персональный ключ доступа:</p>
            <code>{key_row["key_code"]}</code>
            <p style="font-size: 13px; color: #94a3b8; margin-top: 15px;">Сохраните его, чтобы входить на сайт с любых устройств без повторной регистрации.</p>
            <div>
                <a href="/logout-client" class="btn-exit">Выйти</a>
            </div>
        </div>
    </body>
    </html>
    """

@app.get("/logout-client")
def logout_client():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(key="client_session")
    return resp
