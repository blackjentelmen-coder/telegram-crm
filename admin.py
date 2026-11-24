import os
import shutil
import uuid
import time
from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from database import (
    get_db,
    get_banner,
    update_banner_db,
    get_recent_chat_messages,
)

router = APIRouter()

ADMIN_LOGIN = os.getenv("GHOSTCORE_ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("GHOSTCORE_ADMIN_PASSWORD", "ghostcore123")
ADMIN_COOKIE_NAME = "admin_session"
ADMIN_COOKIE_VALUE = "ghostcore_admin_authenticated"


def is_admin_logged_in(request: Request) -> bool:
    return request.cookies.get(ADMIN_COOKIE_NAME) == ADMIN_COOKIE_VALUE


def init_banners_table_safe():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS home_banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'GHOSTCORE',
            text TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            duration_sec INTEGER NOT NULL DEFAULT 8,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 0
        )
        """
    )
    db.commit()
    db.close()


def fetch_banners_list() -> list:
    init_banners_table_safe()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, title, text, url, image_url, duration_sec, is_active, created_at
        FROM home_banners
        ORDER BY id DESC
        """
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def render_admin_login(error: str = "") -> str:
    err_block = f'<div class="err">{error}</div>' if error else ""
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GHOSTCORE | Вход в админ-панель</title>
        <style>
            :root {{
                --bg: #060609;
                --panel: #110b0e;
                --border: #4a121d;
                --red: #ff1e42;
                --red-soft: rgba(255, 30, 66, 0.28);
                --text: #f5f5f7;
                --muted: #9e969b;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                padding: 16px;
                min-height: 100vh;
                background: radial-gradient(circle at top, #18070c 0%, var(--bg) 65%);
                color: var(--text);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .card {{
                width: 100%;
                max-width: 380px;
                background: var(--panel);
                border: 1.5px solid var(--border);
                border-radius: 12px;
                padding: 20px;
                box-shadow: 0 0 22px var(--red-soft);
            }}
            h1 {{
                margin: 0 0 6px 0;
                font-size: 18px;
                letter-spacing: 1.5px;
                text-transform: uppercase;
                color: var(--red);
            }}
            p {{
                margin: 0 0 14px 0;
                font-size: 12px;
                color: var(--muted);
            }}
            .field {{
                width: 100%;
                padding: 10px 12px;
                margin-bottom: 10px;
                background: #15090e;
                border: 1px solid #571522;
                border-radius: 8px;
                color: var(--text);
                font-size: 13px;
                outline: none;
            }}
            .field:focus {{
                border-color: var(--red);
            }}
            .btn {{
                width: 100%;
                padding: 11px;
                border: none;
                border-radius: 8px;
                background: linear-gradient(135deg, #c71230, var(--red));
                color: #fff;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1px;
                text-transform: uppercase;
                cursor: pointer;
                box-shadow: 0 0 12px var(--red-soft);
            }}
            .err {{
                color: var(--red);
                font-size: 12px;
                margin-bottom: 10px;
            }}
            .back {{
                display: block;
                text-align: center;
                margin-top: 12px;
                color: var(--muted);
                font-size: 12px;
                text-decoration: none;
            }}
        </style>
    </head>
    <body>
        <form class="card" action="/admin/login" method="POST">
            <h1>GHOSTCORE ADMIN</h1>
            <p>Авторизация для управления баннерами, таймерами и чатом</p>
            {err_block}
            <input type="text" name="login" class="field" placeholder="Логин" required>
            <input type="password" name="password" class="field" placeholder="Пароль" required>
            <button type="submit" class="btn">Войти в панель</button>
            <a href="/" class="back">← На главную</a>
        </form>
    </body>
    </html>
    """


@router.post("/admin/login")
def admin_login_submit(login: str = Form(...), password: str = Form(...)):
    if login.strip() == ADMIN_LOGIN and password == ADMIN_PASSWORD:
        resp = RedirectResponse(url="/admin?message=Вход+выполнен", status_code=303)
        resp.set_cookie(key=ADMIN_COOKIE_NAME, value=ADMIN_COOKIE_VALUE, httponly=True)
        return resp
    return HTMLResponse(content=render_admin_login("Неверный логин или пароль"), status_code=401)


@router.get("/admin/logout")
def admin_logout():
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.delete_cookie(key=ADMIN_COOKIE_NAME)
    return resp


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, message: str = ""):
    if not is_admin_logged_in(request):
        return HTMLResponse(content=render_admin_login())

    banners = fetch_banners_list()
    messages = get_recent_chat_messages(limit=30)
    current_legacy = get_banner()

    msg_html = f'<div class="notice">{message}</div>' if message else ""

    banner_rows_html = ""
    if not banners:
        banner_rows_html = '<div class="empty">Баннеров в ротации пока нет. Добавьте первый баннер выше.</div>'
    else:
        for b in banners:
            active_badge = (
                '<span class="badge badge-on">АКТИВЕН</span>'
                if int(b.get("is_active", 0)) == 1
                else '<span class="badge badge-off">ВЫКЛ</span>'
            )
            toggle_label = "Выключить" if int(b.get("is_active", 0)) == 1 else "Включить"
            dur = int(b.get("duration_sec") or 8)
            img_preview = (
                f'<img src="{b["image_url"]}" alt="" class="mini-thumb">'
                if b.get("image_url")
                else ""
            )
            url_line = f'<div class="b-url">{b["url"]}</div>' if b.get("url") else ""
            banner_rows_html += f"""
            <div class="b-row">
                <div class="b-head">
                    <div class="b-title">{b.get("title") or "GHOSTCORE"}</div>
                    <div class="b-meta">
                        <span class="timer-chip">⏱ {dur} сек</span>
                        {active_badge}
                    </div>
                </div>
                {img_preview}
                <div class="b-text">{b.get("text") or ""}</div>
                {url_line}
                <div class="b-actions">
                    <form action="/admin/banners/toggle" method="POST" style="margin:0;">
                        <input type="hidden" name="banner_id" value="{b["id"]}">
                        <button type="submit" class="btn-sm btn-ghost">{toggle_label}</button>
                    </form>
                    <form action="/admin/banners/delete" method="POST" style="margin:0;">
                        <input type="hidden" name="banner_id" value="{b["id"]}">
                        <button type="submit" class="btn-sm btn-danger">Удалить</button>
                    </form>
                </div>
            </div>
            """

    chat_rows_html = ""
    if not messages:
        chat_rows_html = '<div class="empty">Сообщений в чате пока нет.</div>'
    else:
        for m in reversed(messages):
            chat_rows_html += f"""
            <div class="msg-row">
                <div class="msg-body">
                    <span class="msg-nick">{m.get("author") or "Anon"}:</span>
                    <span>{m.get("text") or ""}</span>
                </div>
                <form action="/admin/chat/delete" method="POST" style="margin:0;">
                    <input type="hidden" name="msg_id" value="{m["id"]}">
                    <button type="submit" class="btn-sm btn-danger">✕</button>
                </form>
            </div>
            """

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GHOSTCORE | Админ-панель</title>
        <style>
            :root {{
                --bg: #060609;
                --panel: #110b0e;
                --border: #4a121d;
                --red: #ff1e42;
                --red-soft: rgba(255, 30, 66, 0.26);
                --text: #f5f5f7;
                --muted: #9e969b;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                padding: 12px;
                min-height: 100vh;
                background: radial-gradient(circle at top, #18070c 0%, var(--bg) 65%);
                color: var(--text);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }}
            .wrap {{
                max-width: 480px;
                margin: 0 auto;
            }}
            .top {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                margin-bottom: 12px;
            }}
            .brand {{
                font-size: 17px;
                font-weight: 800;
                letter-spacing: 1.5px;
                color: var(--red);
                text-transform: uppercase;
            }}
            .top-links {{
                display: flex;
                gap: 8px;
            }}
            .card {{
                background: var(--panel);
                border: 1.5px solid var(--border);
                border-radius: 12px;
                padding: 14px;
                margin-bottom: 12px;
            }}
            .card-title {{
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1px;
                text-transform: uppercase;
                color: var(--text);
                margin-bottom: 10px;
            }}
            .field {{
                width: 100%;
                padding: 9px 11px;
                margin-bottom: 8px;
                background: #15090e;
                border: 1px solid #571522;
                border-radius: 8px;
                color: var(--text);
                font-size: 13px;
                outline: none;
            }}
            .field:focus {{
                border-color: var(--red);
            }}
            .row-2 {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }}
            .lbl {{
                display: block;
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 0.7px;
                color: var(--muted);
                margin-bottom: 4px;
            }}
            .check-line {{
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 12px;
                margin: 4px 0 10px 0;
            }}
            .btn-main {{
                width: 100%;
                padding: 10px 12px;
                border: none;
                border-radius: 8px;
                background: linear-gradient(135deg, #c71230, var(--red));
                color: #fff;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                cursor: pointer;
                box-shadow: 0 0 12px var(--red-soft);
            }}
            .btn-ghost {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 8px 11px;
                border-radius: 8px;
                background: #180c11;
                border: 1px solid var(--border);
                color: var(--text);
                font-size: 11px;
                font-weight: 700;
                text-decoration: none;
                cursor: pointer;
            }}
            .btn-sm {{
                padding: 6px 10px;
                border-radius: 7px;
                font-size: 11px;
                font-weight: 700;
                cursor: pointer;
                border: 1px solid var(--border);
            }}
            .btn-danger {{
                background: rgba(255, 30, 66, 0.16);
                border-color: var(--red);
                color: #ff8fa3;
            }}
            .b-row {{
                background: #0c070a;
                border: 1px solid #2e1018;
                border-radius: 9px;
                padding: 10px;
                margin-bottom: 8px;
            }}
            .b-head {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                margin-bottom: 5px;
            }}
            .b-title {{
                font-size: 13px;
                font-weight: 800;
                color: var(--text);
            }}
            .b-meta {{
                display: flex;
                align-items: center;
                gap: 6px;
            }}
            .timer-chip {{
                font-size: 10px;
                font-weight: 700;
                padding: 2px 7px;
                border-radius: 999px;
                background: rgba(255, 30, 66, 0.14);
                border: 1px solid rgba(255, 30, 66, 0.4);
                color: var(--red);
            }}
            .badge {{
                font-size: 10px;
                font-weight: 700;
                padding: 2px 7px;
                border-radius: 999px;
            }}
            .badge-on {{
                background: rgba(34, 197, 94, 0.15);
                color: #86efac;
                border: 1px solid rgba(34, 197, 94, 0.35);
            }}
            .badge-off {{
                background: rgba(158, 150, 155, 0.14);
                color: var(--muted);
                border: 1px solid #352b30;
            }}
            .b-text {{
                font-size: 12px;
                color: #e4d4d8;
                margin-bottom: 4px;
            }}
            .b-url {{
                font-size: 11px;
                color: var(--muted);
                word-break: break-all;
                margin-bottom: 6px;
            }}
            .mini-thumb {{
                width: 100%;
                max-height: 110px;
                object-fit: cover;
                border-radius: 7px;
                margin-bottom: 6px;
                border: 1px solid #2e1018;
            }}
            .b-actions {{
                display: flex;
                gap: 8px;
                margin-top: 6px;
            }}
            .msg-row {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                background: #0c070a;
                border: 1px solid #2b0f16;
                border-radius: 8px;
                padding: 8px 10px;
                margin-bottom: 6px;
                font-size: 12px;
            }}
            .msg-nick {{
                color: var(--red);
                font-weight: 700;
                margin-right: 4px;
            }}
            .notice {{
                background: rgba(255, 30, 66, 0.14);
                border: 1px solid var(--red);
                color: #ffd6de;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 12px;
                margin-bottom: 10px;
            }}
            .empty {{
                font-size: 12px;
                color: var(--muted);
            }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="top">
                <div class="brand">GHOSTCORE ADMIN</div>
                <div class="top-links">
                    <a href="/" class="btn-ghost">Главная</a>
                    <a href="/admin/logout" class="btn-ghost">Выйти</a>
                </div>
            </div>

            {msg_html}

            <section class="card">
                <div class="card-title">Добавить баннер в ротацию</div>
                <form action="/admin/banners/add" method="POST" enctype="multipart/form-data">
                    <label class="lbl">Заголовок</label>
                    <input type="text" name="title" class="field" placeholder="Например: GHOSTCORE VIP" required>

                    <label class="lbl">Текст баннера</label>
                    <input type="text" name="text" class="field" placeholder="Короткий промо-текст" required>

                    <div class="row-2">
                        <div>
                            <label class="lbl">Время показа (сек)</label>
                            <input type="number" name="duration_sec" class="field" value="8" min="2" max="3600" required>
                        </div>
                        <div>
                            <label class="lbl">Ссылка (необязательно)</label>
                            <input type="text" name="url" class="field" placeholder="https://...">
                        </div>
                    </div>

                    <label class="lbl">Картинка (необязательно)</label>
                    <input type="file" name="image" accept=".jpg,.jpeg,.png,.gif,.webp" class="field">

                    <label class="check-line">
                        <input type="checkbox" name="active" checked>
                        <span>Показывать на главной странице</span>
                    </label>

                    <button type="submit" class="btn-main">+ Добавить баннер</button>
                </form>
            </section>

            <section class="card">
                <div class="card-title">Баннеры и таймеры ({len(banners)})</div>
                {banner_rows_html}
            </section>

            <section class="card">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
                    <div class="card-title" style="margin:0;">Чат (модерация)</div>
                    <form action="/admin/chat/clear" method="POST" style="margin:0;">
                        <button type="submit" class="btn-sm btn-danger">Очистить чат</button>
                    </form>
                </div>
                {chat_rows_html}
            </section>
        </div>
    </body>
    </html>
    """
