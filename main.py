from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from database import init_db, get_db
import admin

app = FastAPI()

# Инициализация базы данных при старте
init_db()

# Подключаем роутер админки
app.include_router(admin.router)

@app.get("/", response_class=HTMLResponse)
def client_home(error: str = None, success: str = None):
    msg_html = ""
    if error:
        msg_html = f'<div style="color: #f87171; margin-bottom: 15px; text-align: center;">{error}</div>'
    if success:
        msg_html = f'<div style="color: #4ade80; margin-bottom: 15px; text-align: center;">{success}</div>'

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Активация доступа</title>
        <style>
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
            .card {{ background: #1e293b; padding: 30px; border-radius: 8px; width: 350px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            input {{ width: 100%; padding: 10px; margin: 10px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; box-sizing: border-box; }}
            button {{ width: 100%; padding: 10px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }}
            button:hover {{ background: #2563eb; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2 style="text-align: center; margin-top: 0;">Активация ключа</h2>
            {msg_html}
            <form action="/activate" method="POST">
                <input type="text" name="client_name" placeholder="Ваше Имя / Ник" required>
                <input type="text" name="key_code" placeholder="Введите ключ доступа" required style="text-transform: uppercase;">
                <button type="submit">Активировать</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/activate", response_class=HTMLResponse)
def activate_key(client_name: str = Form(...), key_code: str = Form(...)):
    clean_key = key_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    
    # Проверяем существование ключа
    cursor.execute("SELECT * FROM access_keys WHERE key_code = ?", (clean_key,))
    key_row = cursor.fetchone()
    
    if not key_row:
        db.close()
        return client_home(error="Ключ не найден!")
    
    if key_row["status"] == "active":
        db.close()
        return client_home(error="Этот ключ уже был активирован ранее!")
    
    # Активируем ключ и записываем имя клиента
    cursor.execute(
        "UPDATE access_keys SET status = 'active', client_name = ? WHERE id = ?",
        (client_name.strip(), key_row["id"])
    )
    db.commit()
    db.close()
    
    return client_home(success="Ключ успешно активирован! Добро пожаловать.")
