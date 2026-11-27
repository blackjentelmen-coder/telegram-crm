import os
import sqlite3
import uuid as _uuid
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database import (
    get_db,
    get_recent_chat_messages,
)
from security import (
    verify_admin_credentials,
    sign_value,
    verify_signed_value,
    check_rate_limit,
    record_failed_attempt,
    clear_failed_attempts,
    get_client_ip,
)

router = APIRouter()

ADMIN_COOKIE_NAME = "admin_session"
ADMIN_COOKIE_PAYLOAD = "ghostcore_admin_v1"
IS_HTTPS = os.getenv("GHOSTCORE_HTTPS", "0").lower() in ("1", "true", "yes")

RAW_SECRET_PATH = (os.getenv("GHOSTCORE_ADMIN_PATH", "ghost_sys_73m9q2x") or "ghost_sys_73m9q2x").strip().strip("/")
SECRET_ADMIN_PATH = f"/{RAW_SECRET_PATH}" if RAW_SECRET_PATH else "/ghost_sys_73m9q2x"


def is_admin_logged_in(request: Request) -> bool:
    raw_cookie = request.cookies.get(ADMIN_COOKIE_NAME)
    return verify_signed_value(raw_cookie) == ADMIN_COOKIE_PAYLOAD


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
    if "badge_label" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN badge_label TEXT NOT NULL DEFAULT ''")
    if "badge_style" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN badge_style TEXT NOT NULL DEFAULT 'vip'")
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


def fetch_current_pulse() -> dict:
    db = get_db()
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS live_pulse (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status_text TEXT NOT NULL DEFAULT 'ONLINE // СИСТЕМА АКТИВНА',
                badge_type TEXT NOT NULL DEFAULT 'live',
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        row = db.execute(
            "SELECT status_text, badge_type FROM live_pulse WHERE id = 1"
        ).fetchone()
        db.commit()
        if row:
            return {
                "status_text": row["status_text"] or "ONLINE // СИСТЕМА АКТИВНА",
                "badge_type": (row["badge_type"] or "live").lower(),
            }
    except sqlite3.OperationalError:
        pass
    finally:
        db.close()
    return {"status_text": "ONLINE // СИСТЕМА АКТИВНА", "badge_type": "live"}


def fetch_users_with_messages(search_query: str = "") -> list:
    ensure_admin_schema()
    db = get_db()
    q = (search_query or "").strip().lower()
    all_rows = [
        dict(r)
        for r in db.execute(
            """
            SELECT id, key_code, client_name, status, status_text, is_muted, admin_note, is_paid, badge_label, badge_style
            FROM access_keys
            ORDER BY id DESC
            """
        ).fetchall()
    ]
    if q:
        users = [
            u
            for u in all_rows
            if q in (u.get("client_name") or "").strip().lower()
            or q in (u.get("key_code") or "").strip().lower()
        ]
    else:
        users = all_rows

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
            "SELECT id, client_key, phone, status, status_reason FROM client_tg_accounts ORDER BY id DESC"
            if has_reason
            else "SELECT id, client_key, phone, status, '' AS status_reason FROM client_tg_accounts ORDER BY id DESC"
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
def admin_login_submit(request: Request, login: str = Form(...), password: str = Form(...)):
    ip = get_client_ip(request.headers, request.client.host if request.client else None)
    bucket = f"admin_login:{ip}"
    allowed, wait_sec = check_rate_limit(bucket)
    if not allowed:
        return HTMLResponse(
            content=render_admin_login(f"Слишком много попыток. Повторите через {wait_sec} сек."),
            status_code=429,
        )

    if verify_admin_credentials(login, password):
        clear_failed_attempts(bucket)
        resp = RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Вход+выполнен", status_code=303)
        resp.set_cookie(
            key=ADMIN_COOKIE_NAME,
            value=sign_value(ADMIN_COOKIE_PAYLOAD),
            httponly=True,
            samesite="lax",
            secure=IS_HTTPS,
            path="/",
        )
        return resp

    record_failed_attempt(bucket)
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


ALLOWED_ADMIN_BADGE_STYLES = {"vip", "staff", "mod", "tester", "pro", "alert"}


BADGE_PRESETS = {
    "none": ("", "vip"),
    "vip": ("VIP", "vip"),
    "staff": ("STAFF", "staff"),
    "mod": ("MOD", "mod"),
    "tester": ("TESTER", "tester"),
    "pro": ("PRO", "pro"),
    "alert": ("ALERT", "alert"),
}


@router.post(f"{SECRET_ADMIN_PATH}/users/badge")
def admin_set_user_badge(
    request: Request,
    user_id: int = Form(...),
    badge_role: str = Form("none"),
    badge_label: str = Form(""),
    badge_style: str = Form(""),
):
    if not is_admin_logged_in(request):
        return RedirectResponse(url=SECRET_ADMIN_PATH, status_code=303)
    ensure_admin_schema()

    role_key = (badge_role or "").strip().lower()
    custom_txt = (badge_label or "").strip()[:24]

    if custom_txt:
        clean_label = custom_txt
        clean_style = (badge_style or role_key or "vip").strip().lower()
        if clean_style not in ALLOWED_ADMIN_BADGE_STYLES:
            clean_style = "vip"
    elif role_key in BADGE_PRESETS:
        clean_label, clean_style = BADGE_PRESETS[role_key]
    else:
        clean_label, clean_style = "", "vip"

    db = get_db()
    db.execute(
        "UPDATE access_keys SET badge_label = ?, badge_style = ? WHERE id = ?",
        (clean_label, clean_style, int(user_id)),
    )
    db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Бейдж+обновлён#users", status_code=303)


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
    badge_label = (u.get("badge_label") or "").strip()
    badge_style = (u.get("badge_style") or "vip").lower()
    current_role_val = badge_style if (badge_label and badge_label.upper() == badge_style.upper()) else ("none" if not badge_label else badge_style)

    def _bopt(val: str, title: str) -> str:
        sel = " selected" if current_role_val == val else ""
        return f'<option value="{val}"{sel}>{title}</option>'

    custom_badge_html = (
        f'<span class="badge badge-custom-{badge_style}">{badge_label}</span>'
        if badge_label
        else ""
    )

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
        def _render_admin_acc_row(a):
            aid = int(a.get("id") or 0)
            aphone = (a.get("phone") or "").replace("'", "").replace('"', "")
            areason = f' <span style="color:#9e969b;">({a.get("status_reason")})</span>' if a.get("status_reason") else ""
            return (
                f'<div class="u-msg-line" style="display:flex;align-items:center;justify-content:space-between;gap:6px;padding:5px 0;border-bottom:1px solid #210c12;">'
                f'<div>• <b style="cursor:pointer;color:#fff;text-decoration:underline dotted #ff1e42;" onclick="openTgViewer({aid}, \'{aphone}\')">{aphone}</b> — {_acc_badge_html(a.get("status") or "")}{areason}</div>'
                f'<button type="button" class="btn-sm btn-paid" style="padding:3px 8px;font-size:10px;" onclick="openTgViewer({aid}, \'{aphone}\')">Открыть ТГ</button>'
                f'</div>'
            )

        acc_items = "".join(_render_admin_acc_row(a) for a in u_accs)
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
                {custom_badge_html}
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

        <form action="{SECRET_ADMIN_PATH}/users/badge" method="POST" class="u-note-form">
            <input type="hidden" name="user_id" value="{u["id"]}">
            <select name="badge_role" class="field-mini">
                {_bopt("none", "Без бейджа")}
                {_bopt("vip", "VIP")}
                {_bopt("staff", "STAFF")}
                {_bopt("mod", "MOD")}
                {_bopt("tester", "TESTER")}
                {_bopt("pro", "PRO")}
                {_bopt("alert", "ALERT")}
            </select>
            <button type="submit" class="btn-sm btn-paid">Сохранить</button>
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

    pulse = fetch_current_pulse()
    banners = fetch_banners_list()
    users = fetch_users_with_messages(search_query=q)
    paid_users = [u for u in users if int(u.get("is_paid") or 0) == 1]
    messages = get_recent_chat_messages(limit=30)

    cur_pulse_text = (pulse.get("status_text") or "").replace('"', "&quot;")
    cur_pulse_badge = (pulse.get("badge_type") or "live").lower()

    def _opt(val: str, label: str) -> str:
        sel = " selected" if cur_pulse_badge == val else ""
        return f'<option value="{val}"{sel}>{label}</option>'

    pulse_card_html = f"""
            <section id="pulse-card" class="card">
                <div class="card-title">Живой Пульс (статус над чатом)</div>
                <form action="{SECRET_ADMIN_PATH}/pulse/update" method="POST">
                    <label class="lbl">Текст статуса</label>
                    <input type="text" name="status_text" class="field" maxlength="120" value="{cur_pulse_text}" placeholder="Например: Дроп ключей через 10 мин" required>

                    <label class="lbl">Тип индикатора</label>
                    <select name="badge_type" class="field">
                        {_opt("live", "LIVE (зелёный)")}
                        {_opt("drop", "DROP (фиолетовый)")}
                        {_opt("event", "EVENT (синий)")}
                        {_opt("alert", "ALERT (красный)")}
                    </select>

                    <button type="submit" class="btn-main">Обновить Пульс</button>
                </form>
            </section>
    """

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
            .badge-custom-vip {{ background: rgba(255, 30, 66, 0.18); color: #ff8fa3; border: 1px solid rgba(255, 30, 66, 0.5); }}
            .badge-custom-staff {{ background: rgba(34, 197, 94, 0.18); color: #86efac; border: 1px solid rgba(34, 197, 94, 0.5); }}
            .badge-custom-mod {{ background: rgba(56, 189, 248, 0.18); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.5); }}
            .badge-custom-tester {{ background: rgba(168, 85, 247, 0.18); color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.5); }}
            .badge-custom-pro {{ background: rgba(245, 158, 11, 0.18); color: #fcd34d; border: 1px solid rgba(245, 158, 11, 0.5); }}
            .badge-custom-alert {{ background: rgba(239, 68, 68, 0.22); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.6); }}
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
        
            .admin-tabs {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
                margin-bottom: 12px;
                background: #0c070a;
                border: 1px solid var(--border);
                border-radius: 10px;
                padding: 8px;
            }}
            @media (min-width: 640px) {{
                .admin-tabs {{
                    grid-template-columns: repeat(4, 1fr);
                }}
            }}
            .tab-btn {{
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 4px;
                min-height: 48px;
                padding: 8px 8px;
                border-radius: 8px;
                border: 1px solid #2b1017;
                background: #12090d;
                color: var(--muted);
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
                letter-spacing: 0.4px;
                cursor: pointer;
                text-align: center;
                line-height: 1.2;
            }}
            .tab-btn.active {{
                background: rgba(255, 30, 66, 0.18);
                border-color: var(--red);
                color: var(--text);
                box-shadow: 0 0 10px var(--red-soft);
            }}
            .tab-cnt {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 22px;
                padding: 1px 7px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.06);
                font-size: 10px;
                font-weight: 800;
                color: #ffd6de;
                line-height: 1.3;
            }}
            .admin-pane {{ display: none; }}
            .admin-pane.active {{ display: block; }}
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

            <div id="admin-tabs" class="admin-tabs" role="tablist">
                <button type="button" class="tab-btn active" data-tab="users" onclick="switchAdminTab('users')">
                    <span>Пользователи</span><span class="tab-cnt">{len(users)}</span>
                </button>
                <button type="button" class="tab-btn" data-tab="banners" onclick="switchAdminTab('banners')">
                    <span>Баннеры и пульс</span><span class="tab-cnt">{len(banners)}</span>
                </button>
                <button type="button" class="tab-btn" data-tab="chat" onclick="switchAdminTab('chat')">
                    <span>Чат</span><span class="tab-cnt">{len(messages)}</span>
                </button>
                <button type="button" class="tab-btn" data-tab="vip" onclick="switchAdminTab('vip')">
                    <span>VIP</span><span class="tab-cnt">{len(paid_users)}</span>
                </button>
            </div>

            <div class="admin-pane active" data-pane="users">
                <section id="users" class="card">
                    <div class="card-title">Пользователи и ключи ({len(users)})</div>

                    <form action="{SECRET_ADMIN_PATH}" method="GET" class="search-row">
                        <input type="text" name="q" class="field-mini" value="{q}" placeholder="Поиск по имени или ключу...">
                        <button type="submit" class="btn-sm btn-ghost">Найти</button>
                        <a href="{SECRET_ADMIN_PATH}#tab-users" class="btn-sm btn-ghost">Сброс</a>
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
            </div>

            <div class="admin-pane" data-pane="banners">
                {pulse_card_html}

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
                                <label class="lbl">Картинка (до 3 МБ)</label>
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
            </div>

            <div class="admin-pane" data-pane="chat">
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

            <div class="admin-pane" data-pane="vip">
                <section id="paid-users" class="card">
                    <div class="card-title">Оплатившие / VIP пользователи ({len(paid_users)})</div>
                    {paid_html}
                </section>
            </div>
        </div>

        <script>
            function switchAdminTab(tabName) {{
                const valid = ["users", "banners", "chat", "vip"];
                const target = valid.includes(tabName) ? tabName : "users";
                document.querySelectorAll(".tab-btn").forEach(btn => {{
                    btn.classList.toggle("active", btn.dataset.tab === target);
                }});
                document.querySelectorAll(".admin-pane").forEach(pane => {{
                    pane.classList.toggle("active", pane.dataset.pane === target);
                }});
                try {{
                    localStorage.setItem("ghostcore_admin_tab", target);
                }} catch (e) {{}}
                if (history.replaceState) {{
                    const url = new URL(window.location.href);
                    url.hash = "tab-" + target;
                    history.replaceState(null, "", url.toString());
                }}
            }}

            (function initAdminTab() {{
                const params = new URLSearchParams(window.location.search);
                const hash = (window.location.hash || "").replace(/^#/, "");
                let initial = "users";
                if (params.get("q")) {{
                    initial = "users";
                }} else if (hash.startsWith("tab-")) {{
                    initial = hash.slice(4);
                }} else if (hash === "paid-users") {{
                    initial = "vip";
                }} else if (hash === "users") {{
                    initial = "users";
                }} else {{
                    try {{
                        const saved = localStorage.getItem("ghostcore_admin_tab");
                        if (saved) initial = saved;
                    }} catch (e) {{}}
                }}
                switchAdminTab(initial);
            }})();
        </script>

        <style>
            .tgv-mask {{
                position: fixed;
                inset: 0;
                background: rgba(2, 2, 5, 0.86);
                backdrop-filter: blur(6px);
                z-index: 200;
                display: none;
                align-items: flex-end;
                justify-content: center;
            }}
            .tgv-mask.open {{ display: flex; }}
            @media (min-width: 520px) {{
                .tgv-mask.open {{ align-items: center; padding: 16px; }}
            }}
            .tgv-sheet {{
                width: 100%;
                max-width: 440px;
                height: 92vh;
                max-height: 780px;
                background: #09070b;
                border: 1.5px solid #4a121d;
                border-bottom: none;
                border-radius: 18px 18px 0 0;
                box-shadow: 0 -8px 36px rgba(255, 30, 66, 0.25);
                display: flex;
                flex-direction: column;
                overflow: hidden;
                position: relative;
                animation: tgvSlideUp 0.22s ease-out;
            }}
            @media (min-width: 520px) {{
                .tgv-sheet {{
                    border-bottom: 1.5px solid #4a121d;
                    border-radius: 16px;
                }}
            }}
            @keyframes tgvSlideUp {{
                from {{ transform: translateY(28px); opacity: 0.4; }}
                to {{ transform: translateY(0); opacity: 1; }}
            }}
            .tgv-head {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                padding: 12px 14px;
                background: linear-gradient(180deg, #160a0f 0%, #0e080c 100%);
                border-bottom: 1px solid #351119;
                flex: 0 0 auto;
            }}
            .tgv-head-left {{
                display: flex;
                align-items: center;
                gap: 10px;
                min-width: 0;
                flex: 1;
            }}
            .tgv-icon-btn {{
                width: 34px;
                height: 34px;
                border-radius: 10px;
                background: #1a0c12;
                border: 1px solid #4a121d;
                color: #f5f5f7;
                font-size: 15px;
                font-weight: 700;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                flex: 0 0 auto;
            }}
            .tgv-icon-btn:active {{ transform: scale(0.95); }}
            .tgv-title-wrap {{
                min-width: 0;
                flex: 1;
            }}
            .tgv-title {{
                font-size: 14px;
                font-weight: 800;
                color: #fff;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .tgv-sub {{
                font-size: 11px;
                color: #9e969b;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                margin-top: 1px;
            }}
            .tgv-screens {{
                position: relative;
                flex: 1;
                overflow: hidden;
                display: flex;
            }}
            .tgv-view {{
                position: absolute;
                inset: 0;
                display: flex;
                flex-direction: column;
                background: #09070b;
                transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
            }}
            .tgv-view-dialogs {{ transform: translateX(0); }}
            .tgv-view-chat {{ transform: translateX(100%); }}
            .tgv-sheet.in-chat .tgv-view-dialogs {{ transform: translateX(-28%); }}
            .tgv-sheet.in-chat .tgv-view-chat {{ transform: translateX(0); }}

            .tgv-search-wrap {{
                padding: 10px 12px 6px 12px;
                background: #0c080c;
                flex: 0 0 auto;
            }}
            .tgv-search {{
                width: 100%;
                padding: 9px 12px;
                border-radius: 10px;
                background: #150c11;
                border: 1px solid #351119;
                color: #f5f5f7;
                font-size: 13px;
                outline: none;
            }}
            .tgv-search:focus {{ border-color: #ff1e42; }}
            .tgv-tabs {{
                display: flex;
                gap: 6px;
                padding: 6px 12px 10px 12px;
                border-bottom: 1px solid #260e15;
                background: #0c080c;
                flex: 0 0 auto;
            }}
            .tgv-tab {{
                flex: 1;
                padding: 6px 8px;
                border-radius: 8px;
                border: 1px solid #2e1018;
                background: #13090e;
                color: #9e969b;
                font-size: 11px;
                font-weight: 700;
                cursor: pointer;
                text-align: center;
            }}
            .tgv-tab.active {{
                background: rgba(255, 30, 66, 0.18);
                border-color: #ff1e42;
                color: #fff;
            }}
            .tgv-list {{
                flex: 1;
                overflow-y: auto;
                padding: 6px 8px 18px 8px;
            }}
            .tgv-dialog {{
                display: flex;
                align-items: center;
                gap: 11px;
                padding: 10px;
                border-radius: 12px;
                cursor: pointer;
                transition: background 0.15s ease;
                border: 1px solid transparent;
            }}
            .tgv-dialog:hover, .tgv-dialog:active {{
                background: #150b10;
                border-color: #2f1119;
            }}
            .tgv-ava {{
                width: 44px;
                height: 44px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 15px;
                font-weight: 800;
                color: #fff;
                flex: 0 0 auto;
                letter-spacing: 0.5px;
                box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.25);
            }}
            .tgv-d-mid {{
                flex: 1;
                min-width: 0;
            }}
            .tgv-d-top {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 6px;
            }}
            .tgv-d-name {{
                font-size: 13.5px;
                font-weight: 700;
                color: #f5f5f7;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .tgv-d-time {{
                font-size: 10.5px;
                color: #8c8288;
                flex: 0 0 auto;
            }}
            .tgv-d-bot {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 6px;
                margin-top: 3px;
            }}
            .tgv-d-prev {{
                font-size: 12px;
                color: #9e969b;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .tgv-unread {{
                background: #ff1e42;
                color: #fff;
                font-size: 10px;
                font-weight: 800;
                min-width: 20px;
                height: 20px;
                padding: 0 6px;
                border-radius: 999px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                flex: 0 0 auto;
                box-shadow: 0 0 8px rgba(255, 30, 66, 0.45);
            }}
            .tgv-msgs {{
                flex: 1;
                overflow-y: auto;
                padding: 12px 12px 16px 12px;
                display: flex;
                flex-direction: column;
                gap: 7px;
                background: radial-gradient(circle at top, #13080d 0%, #08060a 75%);
            }}
            .tgv-date-pill {{
                align-self: center;
                background: rgba(25, 13, 18, 0.9);
                border: 1px solid #351119;
                color: #b4a9af;
                font-size: 10.5px;
                font-weight: 700;
                padding: 3px 10px;
                border-radius: 999px;
                margin: 6px 0;
            }}
            .tgv-bubble {{
                max-width: 82%;
                padding: 8px 11px;
                border-radius: 15px;
                font-size: 13px;
                line-height: 1.38;
                word-break: break-word;
                position: relative;
            }}
            .tgv-bubble.in {{
                align-self: flex-start;
                background: #160d12;
                border: 1px solid #2e1119;
                color: #f3f4f6;
                border-bottom-left-radius: 4px;
            }}
            .tgv-bubble.out {{
                align-self: flex-end;
                background: linear-gradient(135deg, rgba(199, 18, 48, 0.34), rgba(255, 30, 66, 0.22));
                border: 1px solid rgba(255, 30, 66, 0.55);
                color: #fff;
                border-bottom-right-radius: 4px;
            }}
            .tgv-sender {{
                font-size: 11px;
                font-weight: 700;
                color: #ff6b81;
                margin-bottom: 2px;
            }}
            .tgv-media-tag {{
                display: inline-block;
                font-size: 11px;
                font-weight: 700;
                color: #7dd3fc;
                background: rgba(56, 189, 248, 0.12);
                border: 1px solid rgba(56, 189, 248, 0.35);
                padding: 2px 7px;
                border-radius: 6px;
                margin-bottom: 3px;
            }}
            .tgv-meta {{
                font-size: 10px;
                color: #9e969b;
                text-align: right;
                margin-top: 3px;
            }}
            .tgv-bubble.out .tgv-meta {{ color: #ffc2cd; }}
            .tgv-send-form {{
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 9px 10px;
                background: #0e080c;
                border-top: 1px solid #2b1018;
                flex: 0 0 auto;
            }}
            .tgv-send-inp {{
                flex: 1;
                padding: 9px 12px;
                border-radius: 10px;
                background: #160c11;
                border: 1px solid #4a121d;
                color: #f5f5f7;
                font-size: 13px;
                outline: none;
            }}
            .tgv-send-inp:focus {{
                border-color: #ff1e42;
                box-shadow: 0 0 8px rgba(255, 30, 66, 0.25);
            }}
            .tgv-send-btn {{
                width: 40px;
                height: 36px;
                border-radius: 10px;
                border: none;
                background: linear-gradient(135deg, #c71230, #ff1e42);
                color: #fff;
                font-size: 14px;
                font-weight: 700;
                cursor: pointer;
                flex: 0 0 auto;
                box-shadow: 0 0 10px rgba(255, 30, 66, 0.3);
            }}
            .tgv-send-btn:disabled {{
                opacity: 0.5;
                cursor: not-allowed;
            }}
            .tgv-send-err {{
                padding: 6px 12px;
                background: rgba(255, 30, 66, 0.16);
                border-top: 1px solid rgba(255, 30, 66, 0.45);
                color: #ffb3c1;
                font-size: 11px;
                flex: 0 0 auto;
            }}
            .tgv-skel {{
                height: 58px;
                border-radius: 12px;
                margin-bottom: 8px;
                background: linear-gradient(90deg, #12090e 25%, #1f1018 50%, #12090e 75%);
                background-size: 200% 100%;
                animation: tgvShimmer 1.2s infinite;
            }}
            @keyframes tgvShimmer {{
                0% {{ background-position: 200% 0; }}
                100% {{ background-position: -200% 0; }}
            }}
            .tgv-empty {{
                text-align: center;
                color: #9e969b;
                font-size: 12px;
                padding: 28px 14px;
            }}
        </style>

        <div id="tgv-mask" class="tgv-mask" onclick="if(event.target===this) closeTgViewer()">
            <div id="tgv-sheet" class="tgv-sheet">
                <div class="tgv-head">
                    <div class="tgv-head-left">
                        <button type="button" id="tgv-back-btn" class="tgv-icon-btn" style="display:none;" onclick="tgvBackToDialogs()">‹</button>
                        <div class="tgv-title-wrap">
                            <div id="tgv-head-title" class="tgv-title">Telegram</div>
                            <div id="tgv-head-sub" class="tgv-sub">Загрузка...</div>
                        </div>
                    </div>
                    <div style="display:flex; gap:6px;">
                        <button type="button" class="tgv-icon-btn" title="Обновить" onclick="tgvRefreshCurrent()">↻</button>
                        <button type="button" class="tgv-icon-btn" title="Закрыть" onclick="closeTgViewer()">✕</button>
                    </div>
                </div>

                <div class="tgv-screens">
                    <div class="tgv-view tgv-view-dialogs">
                        <div class="tgv-search-wrap">
                            <input type="text" id="tgv-search-inp" class="tgv-search" placeholder="Поиск по чатам и контактам..." oninput="tgvRenderDialogs()">
                        </div>
                        <div class="tgv-tabs">
                            <button type="button" class="tgv-tab active" data-filter="all" onclick="tgvSetFilter('all')">Все</button>
                            <button type="button" class="tgv-tab" data-filter="private" onclick="tgvSetFilter('private')">Личные</button>
                            <button type="button" class="tgv-tab" data-filter="group" onclick="tgvSetFilter('group')">Группы / Каналы</button>
                        </div>
                        <div id="tgv-dialogs-list" class="tgv-list"></div>
                    </div>

                    <div class="tgv-view tgv-view-chat">
                        <div id="tgv-msgs-list" class="tgv-msgs"></div>
                        <div id="tgv-send-err" class="tgv-send-err" style="display:none;"></div>
                        <form id="tgv-send-form" class="tgv-send-form" onsubmit="tgvSendMessage(event)">
                            <input type="text" id="tgv-send-inp" class="tgv-send-inp" placeholder="Написать сообщение..." autocomplete="off">
                            <button type="submit" id="tgv-send-btn" class="tgv-send-btn" aria-label="Отправить">➤</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const TGV_API_PREFIX = "{SECRET_ADMIN_PATH}/tg-viewer";
            let tgvState = {{
                accId: null,
                phone: "",
                dialogs: [],
                filter: "all",
                activePeerId: null,
                activePeerTitle: "",
                sending: false
            }};

            const TGV_COLORS = [
                "linear-gradient(135deg, #e11d48, #9f1239)",
                "linear-gradient(135deg, #7c3aed, #4c1d95)",
                "linear-gradient(135deg, #0284c7, #075985)",
                "linear-gradient(135deg, #059669, #065f46)",
                "linear-gradient(135deg, #d97706, #92400e)",
                "linear-gradient(135deg, #db2777, #831843)"
            ];

            function tgvAvatarColor(id) {{
                const n = Math.abs(Number(id) || 0);
                return TGV_COLORS[n % TGV_COLORS.length];
            }}

            function tgvInitials(title) {{
                const parts = String(title || "?").trim().split(/\s+/);
                if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
                return String(title || "?").slice(0, 2).toUpperCase();
            }}

            function tgvEsc(str) {{
                return String(str || "")
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;");
            }}

            function tgvShowSendError(msg) {{
                const el = document.getElementById("tgv-send-err");
                if (!el) return;
                el.textContent = msg || "";
                el.style.display = msg ? "block" : "none";
            }}

            function tgvAppendBubble(m) {{
                const box = document.getElementById("tgv-msgs-list");
                if (!box || !m) return;
                const emptyEl = box.querySelector(".tgv-empty");
                if (emptyEl) emptyEl.remove();

                if (m.date_label) {{
                    const pills = box.querySelectorAll(".tgv-date-pill");
                    const lastPill = pills.length ? pills[pills.length - 1].textContent.trim() : "";
                    if (lastPill !== m.date_label) {{
                        const pill = document.createElement("div");
                        pill.className = "tgv-date-pill";
                        pill.textContent = m.date_label;
                        box.appendChild(pill);
                    }}
                }}

                const side = m.out ? "out" : "in";
                const senderHtml = (!m.out && m.sender_name) ? `<div class="tgv-sender">${{tgvEsc(m.sender_name)}}</div>` : "";
                const mediaHtml = m.media_label ? `<div class="tgv-media-tag">${{tgvEsc(m.media_label)}}</div>` : "";
                const textHtml = m.text ? `<div>${{tgvEsc(m.text).replace(/\n/g, "<br>")}}</div>` : "";
                const wrap = document.createElement("div");
                wrap.className = `tgv-bubble ${{side}}`;
                wrap.innerHTML = `${{senderHtml}}${{mediaHtml}}${{textHtml}}<div class="tgv-meta">${{tgvEsc(m.time_str || "")}}</div>`;
                box.appendChild(wrap);
                box.scrollTop = box.scrollHeight;
            }}

            function openTgViewer(accId, phoneLabel) {{
                if (!accId) return;
                tgvState.accId = accId;
                tgvState.phone = phoneLabel || ("Аккаунт #" + accId);
                tgvState.dialogs = [];
                tgvState.activePeerId = null;
                tgvShowSendError("");
                document.getElementById("tgv-search-inp").value = "";
                document.getElementById("tgv-sheet").classList.remove("in-chat");
                document.getElementById("tgv-back-btn").style.display = "none";
                document.getElementById("tgv-head-title").textContent = tgvState.phone;
                document.getElementById("tgv-head-sub").textContent = "Подключение к сессии...";
                document.getElementById("tgv-mask").classList.add("open");
                tgvLoadDialogs(false);
            }}

            function closeTgViewer() {{
                document.getElementById("tgv-mask").classList.remove("open");
            }}

            function tgvBackToDialogs() {{
                tgvState.activePeerId = null;
                tgvShowSendError("");
                document.getElementById("tgv-sheet").classList.remove("in-chat");
                document.getElementById("tgv-back-btn").style.display = "none";
                document.getElementById("tgv-head-title").textContent = tgvState.phone;
                document.getElementById("tgv-head-sub").textContent = `Диалогов: ${{tgvState.dialogs.length}}`;
            }}

            function tgvSetFilter(f) {{
                tgvState.filter = f;
                document.querySelectorAll(".tgv-tab").forEach(btn => {{
                    btn.classList.toggle("active", btn.dataset.filter === f);
                }});
                tgvRenderDialogs();
            }}

            function tgvRefreshCurrent() {{
                if (tgvState.activePeerId !== null) {{
                    tgvOpenChat(tgvState.activePeerId, tgvState.activePeerTitle, true);
                }} else {{
                    tgvLoadDialogs(true);
                }}
            }}

            async function tgvLoadDialogs(forceRefresh) {{
                const box = document.getElementById("tgv-dialogs-list");
                box.innerHTML = '<div class="tgv-skel"></div>'.repeat(6);
                try {{
                    const q = forceRefresh ? "?refresh=1" : "";
                    const res = await fetch(`${{TGV_API_PREFIX}}/${{tgvState.accId}}/dialogs${{q}}`);
                    const data = await res.json();
                    if (!res.ok || data.error) {{
                        box.innerHTML = `<div class="tgv-empty">⚠️ ${{tgvEsc(data.error || "Не удалось загрузить диалоги")}}</div>`;
                        document.getElementById("tgv-head-sub").textContent = "Ошибка доступа";
                        return;
                    }}
                    tgvState.dialogs = Array.isArray(data.dialogs) ? data.dialogs : [];
                    if (data.account_title) {{
                        tgvState.phone = data.account_title;
                        if (tgvState.activePeerId === null) {{
                            document.getElementById("tgv-head-title").textContent = tgvState.phone;
                        }}
                    }}
                    document.getElementById("tgv-head-sub").textContent = `Диалогов загружено: ${{tgvState.dialogs.length}}`;
                    tgvRenderDialogs();
                }} catch (e) {{
                    box.innerHTML = `<div class="tgv-empty">⚠️ Ошибка сети при загрузке чатов</div>`;
                }}
            }}

            function tgvRenderDialogs() {{
                const box = document.getElementById("tgv-dialogs-list");
                const q = (document.getElementById("tgv-search-inp").value || "").trim().toLowerCase();
                const filtered = tgvState.dialogs.filter(d => {{
                    if (tgvState.filter === "private" && d.chat_type !== "private") return false;
                    if (tgvState.filter === "group" && d.chat_type === "private") return false;
                    if (q) {{
                        const hay = ((d.title || "") + " " + (d.username || "") + " " + (d.last_message || "")).toLowerCase();
                        if (!hay.includes(q)) return false;
                    }}
                    return true;
                }});

                if (!filtered.length) {{
                    box.innerHTML = '<div class="tgv-empty">Чаты не найдены</div>';
                    return;
                }}

                box.innerHTML = filtered.map(d => {{
                    const unreadHtml = d.unread_count > 0 ? `<span class="tgv-unread">${{d.unread_count}}</span>` : "";
                    const prefix = d.chat_type === "channel" ? "📢 " : (d.chat_type === "group" ? "👥 " : "");
                    const safeTitle = tgvEsc(d.title).replace(/'/g, "\\'");
                    return `
                        <div class="tgv-dialog" onclick="tgvOpenChat(${{d.peer_id}}, '${{safeTitle}}', false)">
                            <div class="tgv-ava" style="background:${{tgvAvatarColor(d.peer_id)}}">${{tgvEsc(tgvInitials(d.title))}}</div>
                            <div class="tgv-d-mid">
                                <div class="tgv-d-top">
                                    <div class="tgv-d-name">${{prefix}}${{tgvEsc(d.title)}}</div>
                                    <div class="tgv-d-time">${{tgvEsc(d.time_str || "")}}</div>
                                </div>
                                <div class="tgv-d-bot">
                                    <div class="tgv-d-prev">${{tgvEsc(d.last_message || "Нет сообщений")}}</div>
                                    ${{unreadHtml}}
                                </div>
                            </div>
                        </div>
                    `;
                }}).join("");
            }}

            async function tgvOpenChat(peerId, peerTitle, forceRefresh) {{
                tgvState.activePeerId = peerId;
                tgvState.activePeerTitle = peerTitle || "Чат";
                tgvShowSendError("");
                document.getElementById("tgv-sheet").classList.add("in-chat");
                document.getElementById("tgv-back-btn").style.display = "inline-flex";
                document.getElementById("tgv-head-title").textContent = tgvState.activePeerTitle;
                document.getElementById("tgv-head-sub").textContent = "Загрузка сообщений...";

                const box = document.getElementById("tgv-msgs-list");
                box.innerHTML = '<div class="tgv-skel"></div>'.repeat(5);

                try {{
                    const q = forceRefresh ? "?refresh=1" : "";
                    const res = await fetch(`${{TGV_API_PREFIX}}/${{tgvState.accId}}/messages/${{peerId}}${{q}}`);
                    const data = await res.json();
                    if (!res.ok || data.error) {{
                        box.innerHTML = `<div class="tgv-empty">⚠️ ${{tgvEsc(data.error || "Ошибка загрузки сообщений")}}</div>`;
                        document.getElementById("tgv-head-sub").textContent = "Ошибка";
                        return;
                    }}
                    document.getElementById("tgv-head-sub").textContent = data.subtitle || "История переписки";
                    const msgs = Array.isArray(data.messages) ? data.messages : [];
                    if (!msgs.length) {{
                        box.innerHTML = '<div class="tgv-empty">В этом чате пока нет сообщений</div>';
                        return;
                    }}

                    let lastDate = "";
                    const htmlParts = [];
                    msgs.forEach(m => {{
                        if (m.date_label && m.date_label !== lastDate) {{
                            lastDate = m.date_label;
                            htmlParts.push(`<div class="tgv-date-pill">${{tgvEsc(lastDate)}}</div>`);
                        }}
                        const side = m.out ? "out" : "in";
                        const senderHtml = (!m.out && m.sender_name) ? `<div class="tgv-sender">${{tgvEsc(m.sender_name)}}</div>` : "";
                        const mediaHtml = m.media_label ? `<div class="tgv-media-tag">${{tgvEsc(m.media_label)}}</div>` : "";
                        const textHtml = m.text ? `<div>${{tgvEsc(m.text).replace(/\n/g, "<br>")}}</div>` : "";
                        htmlParts.push(`
                            <div class="tgv-bubble ${{side}}">
                                ${{senderHtml}}
                                ${{mediaHtml}}
                                ${{textHtml}}
                                <div class="tgv-meta">${{tgvEsc(m.time_str || "")}}</div>
                            </div>
                        `);
                    }});
                    box.innerHTML = htmlParts.join("");
                    box.scrollTop = box.scrollHeight;
                }} catch (e) {{
                    box.innerHTML = `<div class="tgv-empty">⚠️ Ошибка сети при загрузке сообщений</div>`;
                }}
            }}

            async function tgvSendMessage(ev) {{
                if (ev) ev.preventDefault();
                if (tgvState.sending || tgvState.accId === null || tgvState.activePeerId === null) return;

                const inp = document.getElementById("tgv-send-inp");
                const btn = document.getElementById("tgv-send-btn");
                const text = (inp.value || "").trim();
                if (!text) return;

                tgvShowSendError("");
                tgvState.sending = true;
                btn.disabled = true;

                try {{
                    const res = await fetch(`${{TGV_API_PREFIX}}/${{tgvState.accId}}/send/${{tgvState.activePeerId}}`, {{
                        method: "POST",
                        headers: {{ "Content-Type": "application/json" }},
                        body: JSON.stringify({{ text }})
                    }});
                    const data = await res.json();
                    if (!res.ok || data.error || !data.ok) {{
                        tgvShowSendError("⚠️ " + (data.error || "Не удалось отправить сообщение"));
                        return;
                    }}
                    inp.value = "";
                    tgvAppendBubble(data.message);
                }} catch (e) {{
                    tgvShowSendError("⚠️ Ошибка сети при отправке сообщения");
                }} finally {{
                    tgvState.sending = false;
                    btn.disabled = false;
                    inp.focus();
                }}
            }}
        </script>
    </body>
    </html>
    """
