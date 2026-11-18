import uuid
import sqlite3
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])

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
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            th, td {{ padding: 10px; border-bottom: 1px solid #334155; text-align: left; }}
            .tag-free {{ color: #4ade80; font-weight: bold; }}
            .tag-active {{ color: #f87171; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Управление ключами</h2>
            <form action="/admin/keys/add" method="POST" style="margin-bottom: 20px;">
                <button type="submit" style="padding: 10px 15px; background: #3b82f6; color: white; border:none; border-radius:4px; cursor:pointer;">Сгенерировать ключ</button>
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
def add_key():
    db = get_db()
    cursor = db.cursor()
    new_key = str(uuid.uuid4())[:8].upper()
    cursor.execute("INSERT INTO access_keys (key_code, status) VALUES (?, 'free')", (new_key,))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/keys/delete")
def delete_key(key_id: int = Form(...)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM access_keys WHERE id = ?", (key_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)
