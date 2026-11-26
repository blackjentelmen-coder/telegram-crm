import os
import sqlite3
import uuid as _uuid
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database import (
    get_db,
    get_recent_chat_messages,
)

router = APIRouter()

ADMIN_LOGIN = os.getenv("GHOSTCORE_ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("GHOSTCORE_ADMIN_PASSWORD", "ghostcore123")
ADMIN_COOKIE_NAME = "admin_session"
ADMIN_COOKIE_VALUE = "ghostcore_admin_authenticated"

RAW_SECRET_PATH = (os.getenv("GHOSTCORE_ADMIN_PATH", "ghost_sys_73m9q2x") or "ghost_sys_73m9q2x").strip().strip("/")
SECRET_ADMIN_PATH = f"/{RAW_SECRET_PATH}" if RAW_SECRET_PATH else "/ghost_sys_73m9q2x"


def is_admin_logged_in(request: Request) -> bool:
    return request.cookies.get(ADMIN_COOKIE_NAME) == ADMIN_COOKIE_VALUE


def ensure_admin_schema():
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
    cols = [row["name"] for row in db.execute("PRAGMA table_info(access_keys)").fetchall()]
    if "is_muted" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN is_muted INTEGER NOT NULL DEFAULT 0")
    if "admin_note" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN admin_note TEXT NOT NULL DEFAULT ''")
    if "is_paid" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN is_paid INTEGER NOT NULL DEFAULT 0")
    db.commit()
    db.close()


ensure_admin_schema()


def fetch_banners_list() -> list:
    ensure_admin_schema()
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


def fetch_users_with_messages(search_query: str = "") -> list:
    ensure_admin_schema()
    db = get_db()
    q = (search_query or "").strip().lower()
    if q:
        like_q = f"%{q}%"
        users = [
            dict(r)
            for r in db.execute(
                """
                SELECT id, key_code, client_name, status, status_text, is_muted, admin_note, is_paid
                FROM access_keys
                WHERE LOWER(COALESCE(client_name, '')) LIKE ? OR LOWER(COALESCE(key_code, '')) LIKE ?
                ORDER BY id DESC
                """,
                (like_q, like_q),
            ).fetchall()
        ]
    else:
        users = [
            dict(r)
            for r in db.execute(
                """
                SELECT id, key_code, client_name, status, status_text, is_muted, admin_note, is_paid
                FROM access_keys
                ORDER BY id DESC
                """
            ).fetchall()
        ]

    all_msgs = []
    try:
        all_msgs = [
            dict(r)
            for r in db.execute(
                """
                SELECT id, author, text, created_at
                FROM chat_messages
                ORDER BY id DESC
                LIMIT 200
                """
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        recent = get_recent_chat_messages(limit=200)
        all_msgs = list(reversed(recent)) if recent else []

    acc_by_key = {}
    try:
        acc_cols = [r["name"] for r in db.execute("PRAGMA table_info(client_tg_accounts)").fetchall()]
        has_reason = "status_reason" in acc_cols
        sel = (
            "SELECT client_key, phone, status, status_reason FROM client_tg_accounts ORDER BY id DESC"
            if has_reason
            else "SELECT client_key, phone, status, '' AS status_reason FROM client_tg_accounts ORDER BY id DESC"
        )
        for r in db.execute(sel).fetchall():
            k = (r["client_key"] or "").strip().upper()
            acc_by_key.setdefault(k, []).append(dict(r))
    except sqlite3.OperationalError:
        acc_by_key = {}

    db.close()

    for u in users:
        name = (u.get("client_name") or "").strip().lower()
        key = (u.get("key_code") or "").strip().lower()
        user_msgs = []
        for m in all_msgs:
            author_low = (m.get("author") or "").strip().lower()
            if (name and author_low == name) or (key and author_low == key):
                user_msgs.append(m)
        u["messages"] = user_msgs[:15]
        u["tg_accounts"] = acc_by_key.get((u.get("key_code") or "").strip().upper(), [])

    return users


def render_admin_login(error: str = "") -> str:
    err_block = f'<div class="err">{error}</div>' if error else ""
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Вход в панель управления</title>
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
            .field:focus {{ border-color: var(--red); }}
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
        <form class="card" action="{SECRET_ADMIN_PATH}/login" method="POST">
            <h1>CONTROL PANEL</h1>
            <p>Авторизация для управления пользователями, тарифами, баннерами и чатом</p>
            {err_block}
            <input type="text" name="login" class="field" placeholder="Логин" required>
            <input type="password" name="password" class="field" placeholder="Пароль" required>
            <button type="submit" class="btn">Войти в панель</button>
            <a href="/" class="back">← На главную</a>
        </form>
    </body>
    </html>
    """


@router.get("/admin", response_class=HTMLResponse)
def legacy_admin_blocked():
    return HTMLResponse(content="Not Found", status_code=404)


@router.post(f"{SECRET_ADMIN_PATH}/login")
def admin_login_submit(login: str = Form(...), password: str = Form(...)):
    if login.strip() == ADMIN_LOGIN and password == ADMIN_PASSWORD:
        resp = RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Вход+выполнен", status_code=303)
        resp.set_cookie(key=ADMIN_COOKIE_NAME, value=ADMIN_COOKIE_VALUE, httponly=True, path="/")
        return resp
    return HTMLResponse(content=render_admin_login("Неверный логин или пароль"), status_code=401)


@router.get(f"{SECRET_ADMIN_PATH}/logout")
def admin_logout():
    resp = RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    resp.delete_cookie(key=ADMIN_COOKIE_NAME, path="/")
    return resp


@router.post(f"{SECRET_ADMIN_PATH}/users/create")
def admin_create_user(request: Request, client_name: str = Form(...), is_paid: str = Form("off")):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()
    clean_name = client_name.strip()
    if not clean_name:
        return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Укажите+имя+пользователя#users", status_code=303)

    db = get_db()
    dup = db.execute(
        "SELECT id FROM access_keys WHERE LOWER(TRIM(client_name)) = LOWER(?)",
        (clean_name,),
    ).fetchone()
    if dup:
        db.close()
        return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Ошибка:+имя+уже+занято#users", status_code=303)

    new_key = str(_uuid.uuid4())[:8].upper()
    paid_flag = 1 if is_paid in ("on", "1", "true", "yes") else 0
    db.execute(
        "INSERT INTO access_keys (key_code, status, client_name, is_paid) VALUES (?, 'active', ?, ?)",
        (new_key, clean_name, paid_flag),
    )
    db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Создан+ключ+{new_key}#users", status_code=303)


@router.post(f"{SECRET_ADMIN_PATH}/users/toggle-paid")
def admin_toggle_user_paid(request: Request, user_id: int = Form(...)):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()
    db = get_db()
    row = db.execute("SELECT is_paid FROM access_keys WHERE id = ?", (user_id,)).fetchone()
    if row:
        next_paid = 0 if int(row["is_paid"] or 0) == 1 else 1
        db.execute("UPDATE access_keys SET is_paid = ? WHERE id = ?", (next_paid, user_id))
        db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Тариф+пользователя+обновлён#paid-users", status_code=303)


@router.post(f"{SECRET_ADMIN_PATH}/users/toggle-ban")
def admin_toggle_user_ban(request: Request, user_id: int = Form(...)):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()
    db = get_db()
    row = db.execute("SELECT status FROM access_keys WHERE id = ?", (user_id,)).fetchone()
    if row:
        next_status = "active" if row["status"] == "banned" else "banned"
        db.execute("UPDATE access_keys SET status = ? WHERE id = ?", (next_status, user_id))
        db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Статус+бана+обновлён#users", status_code=303)


@router.post(f"{SECRET_ADMIN_PATH}/users/toggle-mute")
def admin_toggle_user_mute(request: Request, user_id: int = Form(...)):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()
    db = get_db()
    row = db.execute("SELECT is_muted FROM access_keys WHERE id = ?", (user_id,)).fetchone()
    if row:
        next_muted = 0 if int(row["is_muted"] or 0) == 1 else 1
        db.execute("UPDATE access_keys SET is_muted = ? WHERE id = ?", (next_muted, user_id))
        db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Статус+мута+в+чате+обновлён#users", status_code=303)


@router.post(f"{SECRET_ADMIN_PATH}/users/note")
def admin_save_user_note(request: Request, user_id: int = Form(...), admin_note: str = Form("")):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()
    db = get_db()
    db.execute(
        "UPDATE access_keys SET admin_note = ? WHERE id = ?",
        (admin_note.strip()[:500], user_id),
    )
    db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Заметка+сохранена#users", status_code=303)


@router.post(f"{SECRET_ADMIN_PATH}/users/delete")
def admin_delete_user(request: Request, user_id: int = Form(...)):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()
    db = get_db()
    db.execute("DELETE FROM access_keys WHERE id = ?", (user_id,))
    db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Пользователь+удалён#users", status_code=303)


def _acc_badge_html(st: str) -> str:
    if st == "Активен":
        return '<span class="badge badge-on">Активен</span>'
    if st == "Спам":
        return '<span class="badge badge-warn">Спам</span>'
    if st == "Слетела сессия":
        return '<span class="badge badge-danger">Слетела сессия</span>'
    return f'<span class="badge badge-off">{st or "Ожидает код"}</span>'


def _render_user_card(u: dict) -> str:
    u_name = u.get("client_name") or "Без имени"
    u_key = u.get("key_code") or "—"
    is_banned = (u.get("status") == "banned")
    is_muted = int(u.get("is_muted") or 0) == 1
    is_paid = int(u.get("is_paid") or 0) == 1
    note_val = u.get("admin_note") or ""

    paid_badge = (
        '<span class="badge badge-paid">ОПЛАЧЕНО / VIP</span>'
        if is_paid
        else '<span class="badge badge-off">FREE</span>'
    )
    ban_badge = (
        '<span class="badge badge-danger">ЗАБАНЕН</span>'
        if is_banned
        else '<span class="badge badge-on">АКТИВЕН</span>'
    )
    mute_badge = (
        '<span class="badge badge-warn">В МУТЕ</span>'
        if is_muted
        else '<span class="badge badge-off">ЧАТ ОК</span>'
    )

    u_accs = u.get("tg_accounts") or []
    if not u_accs:
        accs_block = '<div class="u-msg-empty">TG-аккаунтов нет.</div>'
    else:
        acc_items = "".join(
            f'<div class="u-msg-line">• <b>{a.get("phone") or ""}</b> — {_acc_badge_html(a.get("status") or "")}'
            + (f' <span style="color:#9e969b;">({a.get("status_reason")})</span>' if a.get("status_reason") else "")
            + "</div>"
            for a in u_accs
        )
        accs_block = f'<div class="u-msg-box">{acc_items}</div>'

    u_msgs = u.get("messages") or []
    if not u_msgs:
        msgs_block = '<div class="u-msg-empty">Сообщений в чате не найдено.</div>'
    else:
        items_str = "".join(
            f'<div class="u-msg-line">• {m.get("text") or ""}</div>'
            for m in u_msgs
        )
        msgs_block = f'<div class="u-msg-box">{items_str}</div>'

    return f"""
    <div class="u-card">
        <div class="u-top">
            <div>
                <div class="u-name">{u_name}</div>
                <div class="u-key">Ключ: <code>{u_key}</code></div>
            </div>
            <div class="u-badges">
                {paid_badge}
                {ban_badge}
                {mute_badge}
            </div>
        </div>

        <div class="u-actions">
            <form action="{SECRET_ADMIN_PATH}/users/toggle-paid" method="POST" style="margin:0;">
                <input type="hidden" name="user_id" value="{u["id"]}">
                <button type="submit" class="btn-sm btn-paid">
                    {"Снять VIP" if is_paid else "Перенести в VIP"}
                </button>
            </form>
            <form action="{SECRET_ADMIN_PATH}/users/toggle-ban" method="POST" style="margin:0;">
                <input type="hidden" name="user_id" value="{u["id"]}">
                <button type="submit" class="btn-sm btn-danger">
                    {"Разбанить" if is_banned else "Забанить"}
                </button>
            </form>
            <form action="{SECRET_ADMIN_PATH}/users/toggle-mute" method="POST" style="margin:0;">
                <input type="hidden" name="user_id" value="{u["id"]}">
                <button type="submit" class="btn-sm btn-warn">
                    {"Размутить" if is_muted else "Замутить в чате"}
                </button>
            </form>
            <form action="{SECRET_ADMIN_PATH}/users/delete" method="POST" style="margin:0;">
                <input type="hidden" name="user_id" value="{u["id"]}">
                <button type="submit" class="btn-sm btn-ghost">Удалить</button>
            </form>
        </div>

        <form action="{SECRET_ADMIN_PATH}/users/note" method="POST" class="u-note-form">
            <input type="hidden" name="user_id" value="{u["id"]}">
            <input type="text" name="admin_note" class="field-mini" value="{note_val}" placeholder="Заметка о пользователе...">
            <button type="submit" class="btn-sm btn-ghost">Сохранить</button>
        </form>

        <details class="u-details">
            <summary class="u-summary">TG-аккаунты ({len(u_accs)})</summary>
            {accs_block}
        </details>

        <details class="u-details">
            <summary class="u-summary">СМС пользователя в чате ({len(u_msgs)})</summary>
            {msgs_block}
        </details>
    </div>
    """


@router.get(SECRET_ADMIN_PATH, response_class=HTMLResponse)
def admin_dashboard(request: Request, message: str = "", q: str = ""):
    if not is_admin_logged_in(request):
        return HTMLResponse(content=render_admin_login())

    banners = fetch_banners_list()
    users = fetch_users_with_messages(search_query=q)
    paid_users = [u for u in users if int(u.get("is_paid") or 0) == 1]
    messages = get_recent_chat_messages(limit=30)

    msg_html = f'<div class="notice">{message}</div>' if message else ""

    paid_html = (
        '<div class="empty">Оплативших пользователей пока нет. Переведите нужного клиента в VIP кнопкой ниже.</div>'
        if not paid_users
        else "".join(_render_user_card(u) for u in paid_users)
    )

    users_html = (
        '<div class="empty">Пользователи не найдены.</div>'
        if not users
        else "".join(_render_user_card(u) for u in users)
    )

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
                    <div class="b-title">{b.get("title") or "PANEL"}</div>
                    <div class="b-meta">
                        <span class="timer-chip">⏱ {dur} сек</span>
                        {active_badge}
                    </div>
                </div>
                {img_preview}
                <div class="b-text">{b.get("text") or ""}</div>
                {url_line}
                <div class="b-actions">
                    <form action="{SECRET_ADMIN_PATH}/banners/toggle" method="POST" style="margin:0;">
                        <input type="hidden" name="banner_id" value="{b["id"]}">
                        <button type="submit" class="btn-sm btn-ghost">{toggle_label}</button>
                    </form>
                    <form action="{SECRET_ADMIN_PATH}/banners/delete" method="POST" style="margin:0;">
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
                <form action="{SECRET_ADMIN_PATH}/chat/delete" method="POST" style="margin:0;">
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
        <title>Панель управления</title>
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
            .top-links {{ display: flex; gap: 8px; }}
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
            .field-mini {{
                flex: 1;
                padding: 7px 10px;
                background: #15090e;
                border: 1px solid #4a121d;
                border-radius: 7px;
                color: var(--text);
                font-size: 12px;
                outline: none;
            }}
            .search-row {{
                display: flex;
                gap: 6px;
                margin-bottom: 10px;
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
                padding: 7px 10px;
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
            .btn-paid {{
                background: rgba(56, 189, 248, 0.16);
                border-color: rgba(56, 189, 248, 0.55);
                color: #7dd3fc;
            }}
            .btn-danger {{
                background: rgba(255, 30, 66, 0.16);
                border-color: var(--red);
                color: #ff8fa3;
            }}
            .btn-warn {{
                background: rgba(245, 158, 11, 0.16);
                border-color: rgba(245, 158, 11, 0.55);
                color: #fcd34d;
            }}
            .u-card, .b-row {{
                background: #0c070a;
                border: 1px solid #2e1018;
                border-radius: 9px;
                padding: 10px;
                margin-bottom: 8px;
            }}
            .u-top, .b-head {{
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 8px;
                margin-bottom: 6px;
            }}
            .u-name, .b-title {{
                font-size: 13px;
                font-weight: 800;
                color: var(--text);
            }}
            .u-key {{
                font-size: 11px;
                color: var(--muted);
                margin-top: 2px;
            }}
            .u-key code {{
                color: #ffb3c1;
                background: #1a0c12;
                padding: 1px 5px;
                border-radius: 4px;
            }}
            .u-badges, .b-meta {{
                display: flex;
                align-items: center;
                gap: 5px;
                flex-wrap: wrap;
            }}
            .u-actions, .b-actions {{
                display: flex;
                gap: 6px;
                flex-wrap: wrap;
                margin-top: 6px;
            }}
            .u-note-form {{
                display: flex;
                gap: 6px;
                margin-top: 8px;
            }}
            .u-details {{
                margin-top: 8px;
                border-top: 1px solid #230d13;
                padding-top: 6px;
            }}
            .u-summary {{
                cursor: pointer;
                font-size: 11px;
                font-weight: 700;
                color: var(--muted);
                list-style: none;
            }}
            .u-msg-box {{
                margin-top: 6px;
                max-height: 130px;
                overflow-y: auto;
                background: #14090e;
                border: 1px solid #2a0f16;
                border-radius: 7px;
                padding: 6px 8px;
            }}
            .u-msg-line {{
                font-size: 11px;
                color: #e6d6da;
                padding: 3px 0;
                word-break: break-word;
            }}
            .u-msg-empty {{
                font-size: 11px;
                color: var(--muted);
                margin-top: 4px;
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
            .badge-paid {{
                background: rgba(56, 189, 248, 0.16);
                color: #7dd3fc;
                border: 1px solid rgba(56, 189, 248, 0.4);
            }}
            .badge-on {{
                background: rgba(34, 197, 94, 0.15);
                color: #86efac;
                border: 1px solid rgba(34, 197, 94, 0.35);
            }}
            .badge-warn {{
                background: rgba(245, 158, 11, 0.16);
                color: #fcd34d;
                border: 1px solid rgba(245, 158, 11, 0.4);
            }}
            .badge-danger {{
                background: rgba(255, 30, 66, 0.18);
                color: #ff8fa3;
                border: 1px solid rgba(255, 30, 66, 0.45);
            }}
            .badge-off {{
                background: rgba(158, 150, 155, 0.14);
                color: var(--muted);
                border: 1px solid #352b30;
            }}
            .b-text {{ font-size: 12px; color: #e4d4d8; margin-bottom: 4px; }}
            .b-url {{ font-size: 11px; color: var(--muted); word-break: break-all; margin-bottom: 6px; }}
            .mini-thumb {{
                width: 100%;
                max-height: 110px;
                object-fit: cover;
                border-radius: 7px;
                margin-bottom: 6px;
                border: 1px solid #2e1018;
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
            .msg-nick {{ color: var(--red); font-weight: 700; margin-right: 4px; }}
            .notice {{
                background: rgba(255, 30, 66, 0.14);
                border: 1px solid var(--red);
                color: #ffd6de;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 12px;
                margin-bottom: 10px;
            }}
            .empty {{ font-size: 12px; color: var(--muted); }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="top">
                <div class="brand">CONTROL PANEL</div>
                <div class="top-links">
                    <a href="/" class="btn-ghost">Главная</a>
                    <a href="{SECRET_ADMIN_PATH}/logout" class="btn-ghost">Выйти</a>
                </div>
            </div>

            {msg_html}

            <section id="paid-users" class="card">
                <div class="card-title">Оплатившие / VIP пользователи ({len(paid_users)})</div>
                {paid_html}
            </section>

            <section id="users" class="card">
                <div class="card-title">Пользователи и ключи ({len(users)})</div>

                <form action="{SECRET_ADMIN_PATH}" method="GET" class="search-row">
                    <input type="text" name="q" class="field-mini" value="{q}" placeholder="Поиск по имени или ключу...">
                    <button type="submit" class="btn-sm btn-ghost">Найти</button>
                    <a href="{SECRET_ADMIN_PATH}#users" class="btn-sm btn-ghost">Сброс</a>
                </form>

                <form action="{SECRET_ADMIN_PATH}/users/create" method="POST" style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #260d14;">
                    <label class="lbl">Выдать новый ключ (уникальное имя)</label>
                    <div class="search-row" style="margin-bottom:6px;">
                        <input type="text" name="client_name" class="field-mini" placeholder="Имя клиента" required>
                        <button type="submit" class="btn-sm btn-danger">+ Ключ</button>
                    </div>
                    <label class="check-line" style="margin:0;">
                        <input type="checkbox" name="is_paid">
                        <span>Сразу выдать оплаченный VIP-доступ</span>
                    </label>
                </form>

                {users_html}
            </section>

            <section class="card">
                <div class="card-title">Добавить баннер в ротацию</div>
                <form action="{SECRET_ADMIN_PATH}/banners/add" method="POST" enctype="multipart/form-data">
                    <label class="lbl">Заголовок</label>
                    <input type="text" name="title" class="field" placeholder="Например: VIP ДОСТУП" required>

                    <label class="lbl">Текст баннера</label>
                    <input type="text" name="text" class="field" placeholder="Короткое описание акции или новости">

                    <label class="lbl">Ссылка (необязательно)</label>
                    <input type="text" name="url" class="field" placeholder="https://t.me/...">

                    <div class="row-2">
                        <div>
                            <label class="lbl">Длительность (сек)</label>
                            <input type="number" name="duration_sec" class="field" value="8" min="2" max="3600">
                        </div>
                        <div>
                            <label class="lbl">Картинка</label>
                            <input type="file" name="image" class="field" accept="image/*">
                        </div>
                    </div>

                    <label class="check-line">
                        <input type="checkbox" name="active" checked>
                        <span>Показывать баннер в ротации</span>
                    </label>

                    <button type="submit" class="btn-main">Добавить баннер</button>
                </form>
            </section>

            <section class="card">
                <div class="card-title">Активные и сохранённые баннеры ({len(banners)})</div>
                {banner_rows_html}
            </section>

            <section class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                    <div class="card-title" style="margin-bottom:0;">Чат сайта ({len(messages)})</div>
                    <form action="{SECRET_ADMIN_PATH}/chat/clear" method="POST" style="margin:0;">
                        <button type="submit" class="btn-sm btn-danger">Очистить чат</button>
                    </form>
                </div>
                {chat_rows_html}
            </section>
        </div>
    </body>
    </html>
    """
