import uuid
import sqlite3
from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])

# Задай здесь свой надежный логин и пароль для админки
ADMIN_USER = "admin"
ADMIN_PASS = "secret123"

@router.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Вход в админку</title>
        <style>
            body { font-family: sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .login-box { background: #1e293b; padding: 30px; border-radius: 8px; width: 300px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
            input { width: 100%; padding: 10px; margin: 10px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; box-sizing: border-box; }
            button { width: 100%; padding: 10px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
            button:hover { background: #2563eb; }
            .error { color: #f87171; font-size: 14px; text-align: center; margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2 style="text-align: center; margin-top: 0;">Вход в CRM</h2>
            <form action="/admin/login" method="POST">
                <input type="text" name="username" placeholder="Логин" required>
                <input type="password" name="password" placeholder="Пароль" required>
                <button type="submit">Войти</button>
            </form>
        </div>
    </body>
    </html>
    """

@router.post("/login")
def login(response: Response, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        resp = RedirectResponse(url="/admin", status_code=303)
        resp.set_cookie(key="admin_session", value="authenticated", httponly=True)
        return resp
    return RedirectResponse(url="/admin/login?error=1", status_code=303)

@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(key="admin_session")
    return resp

@router.get("", response_class=HTMLResponse)
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
                    <button type="submit" style="padding:6px 10px; font-size:12px; margin:0; background:#ef4444; color:white; border:none; border-radius:4px; cursor:pointer;">Удалить</button>
                </form>
            </td>
        </tr>
        """

    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8"><title>Админ-панель</title>
        <style>
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }}
            .container {{ max-width: 800px; margin: 0 auto; background: #1e293b; padding: 20px; border-radius: 8px; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            th, td {{ padding: 10px; border-bottom: 1px solid #334155; text-align: left; }}
            .tag-free {{ color: #4ade80; font-weight: bold; }}
            .tag-active {{ color: #f87171; font-weight: bold; }}
            .btn-logout {{ background: #64748b; color: white; padding: 8px 12px; text-decoration: none; border-radius: 4px; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>Управление ключами</h2>
                <a href="/admin/logout" class="btn-logout">Выйти</a>
            </div>
            <form action="/admin/keys/add" method="POST" style="margin-bottom: 20px; margin-top: 20px;">
                <button type="submit" style="padding: 10px 15px; background: #3b82f6; color: white; border:none; border-radius:4px; cursor:pointer; font-weight:bold;">Сгенерировать ключ</button>
            </form>
            <table>
                <tr><th>Ключ</th><th>Статус</th><th>Действие</th></tr>
                {rows_html}
            </table>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content)

@router.post("/keys/add")
def add_key(request: Request):
    if request.cookies.get("admin_session") != "authenticated":
        return RedirectResponse(url="/admin/login", status_code=303)
    db = get_db()
    cursor = db.cursor()
    new_key = str(uuid.uuid4())[:8].upper()
    cursor.execute("INSERT INTO access_keys (key_code, status) VALUES (?, 'free')", (new_key,))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/keys/delete")
def delete_key(request: Request, key_id: int = Form(...)):
    if request.cookies.get("admin_session") != "authenticated":
        return RedirectResponse(url="/admin/login", status_code=303)
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM access_keys WHERE id = ?", (key_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)
