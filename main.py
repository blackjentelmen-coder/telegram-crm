import sqlite3
import secrets
from datetime import datetime
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="Telegram CRM", version="2.3")

DB_FILE = "crm.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'free',
            created_at TEXT,
            activated_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("SELECT value FROM admin_config WHERE key='admin_pass'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO admin_config (key, value) VALUES ('admin_pass', 'admin123')")
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def generate_keys_batch(count=20):
    conn = get_db()
    cursor = conn.cursor()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(count):
        while True:
            p1 = "".join(secrets.choice(alphabet) for _ in range(4))
            p2 = "".join(secrets.choice(alphabet) for _ in range(4))
            p3 = "".join(secrets.choice(alphabet) for _ in range(4))
            key_code = f"CRM-{p1}-{p2}-{p3}"
            cursor.execute("SELECT id FROM access_keys WHERE key_code = ?", (key_code,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO access_keys (key_code, status, created_at) VALUES (?, 'free', ?)",
                    (key_code, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                break
    conn.commit()
    conn.close()

BASE_STYLE = """
<style>
    :root {
        --bg: #0b0f19;
        --card-bg: #131c2e;
        --accent: #00ffcc;
        --accent-hover: #00cca3;
        --danger: #ff4757;
        --text: #f1f2f6;
        --text-muted: #a4b0be;
        --border: #1f2937;
    }
    body {
        background-color: var(--bg);
        color: var(--text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        margin: 0;
        padding: 20px;
        display: flex;
        justify-content: center;
        align-items: center;
        min-height: 90vh;
        box-sizing: border-box;
    }
    .container {
        width: 100%;
        max-width: 480px;
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }
    h2, h3 { color: var(--accent); margin-top: 0; }
    input, button {
        width: 100%;
        padding: 12px;
        margin-top: 10px;
        margin-bottom: 15px;
        background: var(--bg);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text);
        font-size: 16px;
        box-sizing: border-box;
    }
    input:focus { border-color: var(--accent); outline: none; }
    button {
        background: var(--accent);
        color: #0b0f19;
        font-weight: bold;
        cursor: pointer;
        border: none;
        transition: background 0.2s;
    }
    button:hover { background: var(--accent-hover); }
    .btn-danger { background: var(--danger); color: white; }
    .error { color: var(--danger); font-size: 14px; margin-bottom: 10px; }
    table { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; }
    th, td { padding: 10px; border-bottom: 1px solid var(--border); text-align: left; }
    th { color: var(--text-muted); }
    .tag-free { color: var(--accent); }
    .tag-active { color: #ffa502; }
    .admin-btn {
        display: block;
        width: 100%;
        padding: 12px;
        background: #1f2937;
        color: var(--accent);
        border: 1px solid var(--border);
        border-radius: 8px;
        text-align: center;
        text-decoration: none;
        font-weight: bold;
        box-sizing: border-box;
        transition: background 0.2s;
    }
    .admin-btn:hover { background: #374151; }
</style>
"""

# --- 1. АДМИН-ПАНЕЛЬ ---
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(error: str = None):
    err_html = f'<div class="error">{error}</div>' if error else ''
    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Админ-панель - Вход</title>{BASE_STYLE}</head>
    <body>
        <div class="container">
            <h2>🔐 Панель Администратора</h2>
            {err_html}
            <form action="/admin/auth" method="POST">
                <input type="password" name="password" placeholder="Пароль администратора" required autocomplete="current-password">
                <button type="submit">Войти в админку</button>
            </form>
            <div style="margin-top: 15px; text-align: center;">
                <a href="/" style="color: var(--text-muted); text-decoration: none; font-size: 14px;">← На главную</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.post("/admin/auth")
def admin_auth(password: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM admin_config WHERE key='admin_pass'")
    row = cursor.fetchone()
    conn.close()
    
    if row and row["value"] == password:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(key="admin_session", value="authenticated", httponly=True)
        return response
    
    return RedirectResponse(url="/admin/login?error=Неверный пароль администратора", status_code=303)

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    if request.cookies.get("admin_session") != "authenticated":
        return RedirectResponse(url="/admin/login", status_code=303)
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM access_keys ORDER BY id DESC")
    keys = cursor.fetchall()
    db.close()
    
        rows_html = ""
    for k in keys:
        status_class = "tag-free" if k["status"] == "free" else "tag-active"
        status_text = "Свободен" if k["status"] == "free" else "Активирован"
        rows_html += f"""
        <tr>
            <td><code>{k["key_code"]}</code></td>
            <td><span class="{status_class}">{status_text}</span></td>
            <td>
                <form action="/admin/keys/delete" method="POST" style="margin:0;">
                    <input type="hidden" name="key_id" value="{k["id"]}">
                    <button type="submit" style="padding:6px 10px; font-size:12px; margin:0;" class="btn-danger">Удалить</button>
                </form>
            </td>
        </tr>
        """


        
    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Управление ключами CRM</title>{BASE_STYLE}</head>
    <body>
        <div class="container" style="max-width: 600px;">
            <h2>🛠 Управление ключами [v2.3]</h2>
            <form action="/admin/keys/generate" method="POST">
                <button type="submit">➕ Сгенерировать пачку (20 ключей)</button>
            </form>
            <hr style="border: 0; border-top: 1px solid var(--border); margin: 20px 0;">
            <h3>Список ключей в системе</h3>
            <div style="max-height: 350px; overflow-y: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Ключ</th>
                            <th>Статус</th>
                            <th>Действие</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html if rows_html else '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Ключей пока нет. Нажми кнопку выше.</td></tr>'}
                    </tbody>
                </table>
            </div>
            <div style="margin-top: 25px; display: flex; justify-content: space-between; align-items: center;">
                <a href="/" style="color: var(--text-muted); text-decoration: none; font-size: 14px;">🚪 На сайт</a>
                <a href="/logout" style="color: var(--danger); text-decoration: none; font-size: 14px;">Выйти из админки</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.post("/admin/keys/generate")
def admin_generate(request: Request):
    if request.cookies.get("admin_session") != "authenticated":
        return RedirectResponse(url="/admin/login", status_code=303)
    generate_keys_batch(20)
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/keys/delete")
def admin_delete_key(request: Request, key_id: int = Form(...)):
    if request.cookies.get("admin_session") != "authenticated":
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM access_keys WHERE id = ?", (key_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)


# --- 2. КЛИЕНТСКИЙ ВХОД ---
@app.get("/", response_class=HTMLResponse)
def user_login_page(error: str = None):
    err_html = f'<div class="error">{error}</div>' if error else ''
    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Telegram CRM - Вход</title>{BASE_STYLE}</head>
    <body>
        <div class="container">
            <h2>Telegram CRM [v2.3]</h2>
            <p style="color: var(--text-muted);">Введите ваш персональный ключ доступа.</p>
            {err_html}
            <form action="/auth" method="POST">
                <input type="text" name="key_code" placeholder="CRM-XXXX-XXXX-XXXX" required autocomplete="off">
                <button type="submit">Войти в систему</button>
            </form>
            <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid var(--border);">
                <a href="/admin/login" class="admin-btn">🔑 Панель администратора</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.post("/auth")
def user_authenticate(key_code: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    code = key_code.strip()
    
    cursor.execute("SELECT * FROM access_keys WHERE key_code = ?", (code,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return RedirectResponse(url="/?error=Неверный ключ доступа", status_code=303)
    
    if row["status"] == "free":
        cursor.execute(
            "UPDATE access_keys SET status = 'active', activated_at = ? WHERE key_code = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), code)
        )
        conn.commit()
    
    conn.close()
    
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="user_key", value=code)
    return response

@app.get("/dashboard", response_class=HTMLResponse)
def user_dashboard(request: Request):
    user_key = request.cookies.get("user_key")
    if not user_key:
        return RedirectResponse(url="/", status_code=303)
    
    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>CRM Dashboard</title>{BASE_STYLE}</head>
    <body>
        <div class="container">
            <h2>🚀 Рабочий кабинет CRM</h2>
            <p style="color: var(--text-muted);">Ключ: <strong>{user_key}</strong></p>
            <hr style="border: 0; border-top: 1px solid var(--border); margin: 20px 0;">
            <p>Панель управления клиентами и рассылками активна.</p>
            <a href="/logout"><button class="btn-danger">Выйти</button></a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("user_key")
    response.delete_cookie("admin_session")
    return response
