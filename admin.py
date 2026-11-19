import uuid
from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from database import get_db, get_banner, get_recent_chat_messages

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_USER = "admin"
ADMIN_PASS = "secret123"


def is_admin(request: Request) -> bool:
    return request.cookies.get("admin_session") == "authenticated"


@router.get("/login", response_class=HTMLResponse)
def login_page(error: str = None):
    err_html = (
        '<div style="color: #f87171; text-align: center; font-size: 13px; margin-bottom: 10px;">Неверный логин или пароль</div>'
        if error else ""
    )
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Вход в админку</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 16px; }}
            .login-box {{ background: #1e293b; padding: 24px; border-radius: 8px; width: 100%; max-width: 340px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            input {{ width: 100%; padding: 10px; margin: 10px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; font-size: 14px; }}
            button {{ width: 100%; padding: 10px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2 style="text-align: center; margin-top: 0;">Вход в CRM</h2>
            {err_html}
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
def admin_panel(request: Request, message: str = None):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM access_keys ORDER BY id DESC")
    keys = cursor.fetchall()
    db.close()

    banner = get_banner()
    messages = get_recent_chat_messages(limit=30)

    banner_checked = "checked" if banner.get("active") else ""
    current_img_html = (
        f"""
        <div style="margin-bottom: 10px;">
            <div style="font-size: 12px; color: #94a3b8; margin-bottom: 4px;">Текущая картинка:</div>
            <img src="{banner['image_url']}" alt="Banner" style="max-width: 100%; max-height: 140px; border-radius: 6px; border: 1px solid #334155;">
        </div>
        """
        if banner.get("image_url") else ""
    )

    chat_rows_html = ""
    for m in reversed(messages):
        chat_rows_html += f"""
        <tr>
            <td class="nowrap"><strong>{m["author"]}</strong></td>
            <td>{m["text"]}</td>
            <td class="nowrap" style="color: #94a3b8; font-size: 12px;">{m["created_at"]}</td>
            <td class="nowrap">
                <form action="/admin/chat/delete" method="POST" style="margin: 0;">
                    <input type="hidden" name="msg_id" value="{m["id"]}">
                    <button type="submit" class="btn-sm-danger">✕</button>
                </form>
            </td>
        </tr>
        """

    if not chat_rows_html:
        chat_rows_html = '<tr><td colspan="4" style="color: #94a3b8; text-align: center;">Сообщений пока нет</td></tr>'

    rows_html = ""
    for k in keys:
        status_class = "tag-free" if k["status"] == "free" else "tag-active"
        status_text = "Свободен" if k["status"] == "free" else "Активирован"
        client_display = k["client_name"] if k["client_name"] else "—"
        note_val = k["note"] if k["note"] else ""

        rows_html += f"""
        <tr>
            <td class="nowrap"><code>{k["key_code"]}</code></td>
            <td class="nowrap"><span class="{status_class}">{status_text}</span></td>
            <td class="nowrap">{client_display}</td>
            <td class="nowrap">
                <form action="/admin/keys/update-note" method="POST" style="display: flex; gap: 5px; margin: 0;">
                    <input type="hidden" name="key_id" value="{k["id"]}">
                    <input type="text" name="note" value="{note_val}" placeholder="Заметка..." class="note-input">
                    <button type="submit" class="btn-sm-blue">💾</button>
                </form>
            </td>
            <td class="nowrap">
                <form action="/admin/keys/delete" method="POST" style="margin:0;">
                    <input type="hidden" name="key_id" value="{k["id"]}">
                    <button type="submit" class="btn-sm-danger">Удалить</button>
                </form>
            </td>
        </tr>
        """

    alert_html = (
        f'<div style="background: #1e3a8a; border: 1px solid #3b82f6; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 13px;">{message}</div>'
        if message else ""
    )

    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Админ-панель</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 12px; margin: 0; }}
            .container {{ max-width: 1000px; margin: 0 auto; }}
            .panel-box {{ background: #1e293b; padding: 16px; border-radius: 8px; margin-bottom: 16px; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }}
            .table-wrap {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-top: 12px; }}
            table {{ width: 100%; border-collapse: collapse; min-width: 520px; }}
            th, td {{ padding: 10px 8px; border-bottom: 1px solid #334155; text-align: left; vertical-align: middle; }}
            th {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; }}
            .nowrap {{ white-space: nowrap; }}
            .note-input {{ padding: 6px 8px; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; font-size: 12px; width: 115px; }}
            .btn-sm-blue {{ padding: 6px 8px; font-size: 12px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; }}
            .btn-sm-danger {{ padding: 6px 10px; font-size: 12px; background: #ef4444; color: white; border: none; border-radius: 4px; cursor: pointer; }}
            .tag-free {{ color: #4ade80; font-weight: bold; }}
            .tag-active {{ color: #f87171; font-weight: bold; }}
            .btn-logout {{ background: #64748b; color: white; padding: 7px 12px; text-decoration: none; border-radius: 4px; font-size: 13px; white-space: nowrap; }}
            .field-input {{ width: 100%; padding: 9px 10px; margin: 6px 0 12px 0; background: #0f172a; border: 1px solid #334155; color: white; border-radius: 4px; font-size: 13px; }}
            .btn-primary {{ padding: 10px 16px; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; }}
            .btn-danger {{ padding: 9px 14px; background: #ef4444; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; }}
            label {{ font-size: 12px; color: #cbd5e1; display: block; }}
        </style>
    </head>
    <body>
        <div class="container">
            {alert_html}

            <div class="panel-box">
                <div class="header">
                    <h2 style="margin: 0; font-size: 18px;">Рекламный баннер на главной</h2>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <a href="/" style="color: #93c5fd; font-size: 13px; text-decoration: none; white-space: nowrap;">← На главную</a>
                        <a href="/admin/logout" class="btn-logout">Выйти</a>
                    </div>
                </div>

                <div style="margin-top: 14px;">
                    {current_img_html}
                    <form action="/admin/banner/update" method="POST" enctype="multipart/form-data">
                        <label>Картинка баннера (JPG, PNG, GIF, WEBP):</label>
                        <input type="file" name="image" accept="image/*" class="field-input">

                        <label>Заголовок / тег:</label>
                        <input type="text" name="title" value="{banner.get('title', 'Промо')}" class="field-input">

                        <label>Текст баннера:</label>
                        <input type="text" name="text" value="{banner.get('text', '')}" placeholder="Текст объявления или оффера..." class="field-input">

                        <label>Ссылка при клике:</label>
                        <input type="text" name="url" value="{banner.get('url', '')}" placeholder="https://t.me/..." class="field-input">

                        <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px;">
                            <input type="checkbox" name="active" value="on" {banner_checked} style="width: auto; margin: 0;">
                            <span>Показывать баннер на главной странице</span>
                        </label>

                        <button type="submit" class="btn-primary">Сохранить баннер</button>
                    </form>

                    <form action="/admin/banner/clear-image" method="POST" style="margin-top: 8px;">
                        <button type="submit" class="btn-danger">Удалить картинку баннера</button>
                    </form>
                </div>
            </div>

            <div class="panel-box">
                <div class="header">
                    <h2 style="margin: 0; font-size: 18px;">Модерация общего чата</h2>
                    <form action="/admin/chat/clear" method="POST" style="margin: 0;">
                        <button type="submit" class="btn-danger">Очистить весь чат</button>
                    </form>
                </div>
                <div class="table-wrap">
                    <table>
                        <tr><th>Автор</th><th>Сообщение</th><th>Время</th><th></th></tr>
                        {chat_rows_html}
                    </table>
                </div>
            </div>

            <div class="panel-box">
                <h2 style="margin-top: 0; font-size: 18px;">Управление ключами и клиентами</h2>
                <form action="/admin/keys/add" method="POST" style="margin-top: 12px; margin-bottom: 12px;">
                    <button type="submit" class="btn-primary">Сгенерировать пустой ключ</button>
                </form>
                <div class="table-wrap">
                    <table>
                        <tr><th>Ключ</th><th>Статус</th><th>Клиент</th><th>Заметка</th><th>Действие</th></tr>
                        {rows_html}
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content)


@router.post("/keys/add")
def add_key(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    db = get_db()
    cursor = db.cursor()
    new_key = str(uuid.uuid4())[:8].upper()
    cursor.execute("INSERT INTO access_keys (key_code, status, client_name, note) VALUES (?, 'free', NULL, NULL)", (new_key,))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/keys/update-note")
def update_note(request: Request, key_id: int = Form(...), note: str = Form(...)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE access_keys SET note = ? WHERE id = ?", (note.strip(), key_id))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/keys/delete")
def delete_key(request: Request, key_id: int = Form(...)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM access_keys WHERE id = ?", (key_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/admin", status_code=303)
