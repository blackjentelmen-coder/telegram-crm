import os
import asyncio
import qrcode
import io
import base64
import uuid
import time
import sqlite3
import urllib.request
import urllib.parse
from typing import List
from fastapi import FastAPI, Form, Request, Response, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    PeerFloodError,
    FloodWaitError,
)

from database import (
    init_db,
    get_db,
    get_client_profile,
    update_client_profile,
    get_banner,
    update_banner_db,
    add_chat_message,
    get_recent_chat_messages,
    clear_chat_messages_db,
    delete_chat_message_db,
    get_user_by_key,
    set_tg_account_status,
    add_site_notification,
    get_site_notifications,
    mark_notifications_read,
    create_or_update_spam_campaign,
    save_spam_campaign_progress,
    get_latest_spam_campaign,
    get_spam_campaign_by_id,
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_SPAM,
    ACCOUNT_STATUS_SESSION_LOST,
)
from ghost_logger import log_ok, log_warn, log_err
from security import (
    encrypt_secret,
    decrypt_secret,
    sign_value,
    verify_signed_value,
    check_rate_limit,
    record_failed_attempt,
    clear_failed_attempts,
    get_client_ip,
    validate_image_bytes,
)
import admin

import spammer
import inviter
import parser

API_ID = int(os.getenv("GHOSTCORE_TG_API_ID", "38977487"))
API_HASH = os.getenv("GHOSTCORE_TG_API_HASH", "a6afb062a4c36f6d7b2cb87d27385672")
BOT_TOKEN = os.getenv("GHOSTCORE_BOT_TOKEN", "")
SECRET_PATH = admin.SECRET_ADMIN_PATH
CLIENT_COOKIE_NAME = "client_session"
IS_HTTPS = os.getenv("GHOSTCORE_HTTPS", "0").lower() in ("1", "true", "yes")

os.makedirs("static/banners", exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

init_db()
app.include_router(admin.router)

qr_sessions = {}

def _insert_client_tg_account(cursor, client_key: str, phone: str, session_string: str, phone_code_hash, status: str, status_reason: str, checked_at: str = ""):
    cols = [r["name"] for r in cursor.execute("PRAGMA table_info(client_tg_accounts)").fetchall()]
    base_map = {
        "client_key": client_key,
        "phone": phone,
        "session_string": session_string,
        "phone_code_hash": phone_code_hash,
        "status": status,
        "status_reason": status_reason,
        "checked_at": checked_at,
    }
    if "session_name" in cols:
        base_map["session_name"] = phone or f"tg_{int(time.time())}"
    present = {k: v for k, v in base_map.items() if k in cols}
    placeholders = ", ".join(["?"] * len(present))
    col_names = ", ".join(present.keys())
    cursor.execute(f"INSERT INTO client_tg_accounts ({col_names}) VALUES ({placeholders})", tuple(present.values()))


import socks as _socks


def parse_proxy_tuple(raw_proxy: str):
    txt = (raw_proxy or "").strip()
    if not txt:
        return None
    if "://" not in txt and txt.count(":") == 3:
        parts = txt.split(":")
        host, port, user, pwd = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
        txt = f"socks5://{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@{host}:{port}"
    elif "://" not in txt and txt.count(":") == 1:
        host, port = txt.split(":", 1)
        txt = f"socks5://{host.strip()}:{port.strip()}"

    parsed = urllib.parse.urlparse(txt)
    scheme = (parsed.scheme or "socks5").lower()
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return None

    if scheme in ("socks5", "socks5h"):
        ptype = _socks.SOCKS5
    elif scheme in ("socks4", "socks4a"):
        ptype = _socks.SOCKS4
    elif scheme in ("http", "https"):
        ptype = _socks.HTTP
    else:
        ptype = _socks.SOCKS5

    user = urllib.parse.unquote(parsed.username) if parsed.username else None
    pwd = urllib.parse.unquote(parsed.password) if parsed.password else None
    return (ptype, host, int(port), True, user, pwd)


def format_proxy_display(raw_proxy: str) -> str:
    txt = (raw_proxy or "").strip()
    if not txt:
        return ""
    if "://" not in txt and txt.count(":") == 3:
        parts = txt.split(":")
        return f"socks5://{parts[0].strip()}:{parts[1].strip()}"
    parsed = urllib.parse.urlparse(txt if "://" in txt else f"socks5://{txt}")
    scheme = (parsed.scheme or "socks5").lower()
    if parsed.hostname and parsed.port:
        return f"{scheme}://{parsed.hostname}:{parsed.port}"
    return txt[:48]


def get_account_proxy_tuple(acc_row: dict):
    enc = (acc_row or {}).get("proxy_str") or ""
    if not enc:
        return None
    plain = decrypt_secret(enc)
    return parse_proxy_tuple(plain)



def get_session_key_from_request(request: Request) -> str | None:
    raw = request.cookies.get(CLIENT_COOKIE_NAME)
    verified = verify_signed_value(raw)
    return verified.strip().upper() if verified else None


def get_session_key_from_ws(websocket: WebSocket) -> str | None:
    raw = websocket.cookies.get(CLIENT_COOKIE_NAME)
    verified = verify_signed_value(raw)
    return verified.strip().upper() if verified else None


def classify_tg_error(err: Exception):
    if isinstance(
        err,
        (
            AuthKeyUnregisteredError,
            AuthKeyDuplicatedError,
            SessionRevokedError,
            UserDeactivatedError,
            UserDeactivatedBanError,
        ),
    ):
        return ACCOUNT_STATUS_SESSION_LOST, "Слетела или отозвана сессия"

    if isinstance(err, (PeerFloodError, FloodWaitError)):
        return ACCOUNT_STATUS_SPAM, "Спам-ограничение / флуд Telegram"

    low = str(err).lower()
    if any(k in low for k in ("auth_key_unregistered", "session_revoked", "user_deactivated", "not authorized")):
        return ACCOUNT_STATUS_SESSION_LOST, "Слетела сессия"
    if any(k in low for k in ("peer_flood", "floodwait", "spam", "too many requests")):
        return ACCOUNT_STATUS_SPAM, "Спам-ограничение"

    return None, str(err)[:160]


async def check_and_sync_account(acc_row: dict, client_key: str) -> dict:
    acc_id = acc_row["id"]
    phone = acc_row.get("phone") or f"#{acc_id}"
    session_str = decrypt_secret(acc_row.get("session_string") or "")

    if not session_str:
        set_tg_account_status(acc_id, ACCOUNT_STATUS_SESSION_LOST, "Пустая строка сессии")
        log_warn(f"Аккаунт {phone}: сессия пуста -> {ACCOUNT_STATUS_SESSION_LOST}")
        return {**acc_row, "status": ACCOUNT_STATUS_SESSION_LOST, "status_reason": "Пустая строка сессии", "client": None}

    proxy_cfg = get_account_proxy_tuple(acc_row)
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH, proxy=proxy_cfg)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            set_tg_account_status(acc_id, ACCOUNT_STATUS_SESSION_LOST, "Сессия не авторизована")
            add_site_notification(
                client_key,
                "Слетела сессия",
                f"Аккаунт {phone} потерял авторизацию.",
                level="error",
            )
            log_warn(f"Аккаунт {phone}: не авторизован -> {ACCOUNT_STATUS_SESSION_LOST}")
            return {**acc_row, "status": ACCOUNT_STATUS_SESSION_LOST, "status_reason": "Сессия не авторизована", "client": None}

        set_tg_account_status(acc_id, ACCOUNT_STATUS_ACTIVE, "Сессия активна")
        log_ok(f"Аккаунт {phone}: {ACCOUNT_STATUS_ACTIVE}")
        return {**acc_row, "status": ACCOUNT_STATUS_ACTIVE, "status_reason": "Сессия активна", "client": client}

    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        mapped_status, reason = classify_tg_error(e)
        if mapped_status:
            set_tg_account_status(acc_id, mapped_status, reason)
            add_site_notification(
                client_key,
                mapped_status,
                f"Аккаунт {phone}: {reason}",
                level="error",
            )
            log_warn(f"Аккаунт {phone}: {reason} -> {mapped_status}")
            return {**acc_row, "status": mapped_status, "status_reason": reason, "client": None}

        log_err(f"Аккаунт {phone}: ошибка проверки ({reason})")
        return {**acc_row, "status_reason": reason, "client": None}


def notify_user_sync(client_key: str, message: str, level: str = "info"):
    add_site_notification(client_key, "GHOSTCORE", message, level=level)
    if not BOT_TOKEN:
        return
    user = get_user_by_key(client_key)
    tg_id = user.get("tg_user_id") if user else None
    if not tg_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": int(tg_id),
        "text": f"🔔 GHOSTCORE\n\n{message}",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:
        log_err(f"Уведомление в бот не отправлено: {e}")


def init_banners_table():
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
    if "tg_user_id" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN tg_user_id INTEGER")
    if "tg_username" not in cols:
        db.execute("ALTER TABLE access_keys ADD COLUMN tg_username TEXT NOT NULL DEFAULT ''")
    db.commit()
    db.close()


init_banners_table()


def init_pulse_table():
    db = get_db()
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
    db.execute(
        """
        INSERT OR IGNORE INTO live_pulse (id, status_text, badge_type, updated_at)
        VALUES (1, 'ONLINE // СИСТЕМА АКТИВНА', 'live', ?)
        """,
        (time.time(),),
    )
    db.commit()
    db.close()


init_pulse_table()


def get_live_pulse() -> dict:
    init_pulse_table()
    db = get_db()
    row = db.execute(
        "SELECT status_text, badge_type, updated_at FROM live_pulse WHERE id = 1"
    ).fetchone()
    db.close()
    if not row:
        return {"status_text": "ONLINE // СИСТЕМА АКТИВНА", "badge_type": "live", "updated_at": 0}
    return dict(row)


@app.get("/api/pulse")
def api_get_pulse():
    return JSONResponse({"pulse": get_live_pulse()})


@app.post(f"{SECRET_PATH}/pulse/update")
def admin_update_pulse(
    request: Request,
    status_text: str = Form(...),
    badge_type: str = Form("live"),
):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    allowed = {"live", "drop", "event", "alert"}
    clean_badge = badge_type if badge_type in allowed else "live"
    clean_text = status_text.strip()[:120] or "ONLINE // СИСТЕМА АКТИВНА"

    init_pulse_table()
    db = get_db()
    db.execute(
        """
        UPDATE live_pulse
        SET status_text = ?, badge_type = ?, updated_at = ?
        WHERE id = 1
        """,
        (clean_text, clean_badge, time.time()),
    )
    db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_PATH}?message=Пульс+обновлён", status_code=303)


def get_active_home_banners() -> List[dict]:
    init_banners_table()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, title, text, url, image_url, duration_sec, is_active, created_at
        FROM home_banners
        WHERE is_active = 1
        ORDER BY id ASC
        """
    ).fetchall()
    db.close()

    result = []
    for r in rows:
        item = dict(r)
        dur = int(item.get("duration_sec") or 8)
        item["duration_sec"] = max(2, min(dur, 3600))
        if item.get("text") or item.get("image_url") or item.get("title"):
            result.append(item)
    return result


def get_all_home_banners() -> List[dict]:
    init_banners_table()
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


async def save_uploaded_banner_image(image: UploadFile) -> str:
    if not image or not image.filename:
        return ""
    raw_bytes = await image.read(3 * 1024 * 1024 + 1)
    ext = validate_image_bytes(raw_bytes, max_bytes=3 * 1024 * 1024)
    if not ext:
        return ""
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join("static", "banners", filename)
    with open(filepath, "wb") as buffer:
        buffer.write(raw_bytes)
    return f"/static/banners/{filename}"


class ChatConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, payload: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(payload)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


chat_manager = ChatConnectionManager()


def get_authorized_client(client_key: str | None):
    if not client_key:
        return None
    init_banners_table()
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, key_code, client_name, status_text, status, is_muted, is_paid, badge_label, badge_style FROM access_keys WHERE key_code = ?",
            (client_key.strip().upper(),),
        ).fetchone()
    except sqlite3.OperationalError:
        row = db.execute(
            "SELECT id, key_code, client_name, status_text, status FROM access_keys WHERE key_code = ?",
            (client_key.strip().upper(),),
        ).fetchone()
    db.close()
    if not row:
        return None
    data = dict(row)
    if (data.get("status") or "").lower() == "banned":
        return None
    data["is_muted"] = int(data.get("is_muted") or 0)
    data["is_paid"] = int(data.get("is_paid") or 0)
    data["badge_label"] = (data.get("badge_label") or "").strip()
    data["badge_style"] = (data.get("badge_style") or "vip").lower()
    return data


def is_admin_authenticated(request: Request) -> bool:
    return admin.is_admin_logged_in(request)




def get_author_badge_map() -> dict:
    db = get_db()
    mapping = {}
    try:
        rows = db.execute(
            "SELECT client_name, key_code, badge_label, badge_style FROM access_keys WHERE COALESCE(TRIM(badge_label), '') != ''"
        ).fetchall()
        for r in rows:
            info = {
                "badge_label": (r["badge_label"] or "").strip(),
                "badge_style": (r["badge_style"] or "vip").lower(),
            }
            cname = (r["client_name"] or "").strip().lower()
            kcode = (r["key_code"] or "").strip().lower()
            if cname:
                mapping[cname] = info
            if kcode:
                mapping[kcode] = info
    except sqlite3.OperationalError:
        pass
    finally:
        db.close()
    return mapping


@app.get("/api/home-data")
def api_home_data(request: Request):
    client_key = get_session_key_from_request(request)
    user = get_authorized_client(client_key)
    messages = get_recent_chat_messages(limit=50) if user else []
    if user and messages:
        bmap = get_author_badge_map()
        enriched = []
        for m in messages:
            item = dict(m)
            binfo = bmap.get((item.get("author") or "").strip().lower())
            if binfo:
                item["badge_label"] = binfo["badge_label"]
                item["badge_style"] = binfo["badge_style"]
            enriched.append(item)
        messages = enriched
    banners = get_active_home_banners()
    first_banner = banners[0] if banners else None
    return JSONResponse({
        "pulse": get_live_pulse(),
        "banner": first_banner,
        "banners": banners,
        "messages": messages,
        "authenticated": bool(user),
        "is_muted": bool(user and user.get("is_muted")),
        "is_paid": bool(user and user.get("is_paid")),
    })


@app.get("/api/accounts")
def api_get_accounts(request: Request):
    client_key = get_session_key_from_request(request)
    user = get_authorized_client(client_key)
    if not user:
        return JSONResponse({"authenticated": False, "accounts": []}, status_code=401)
    db = get_db()
    rows = db.execute(
        """
        SELECT id, phone, status, status_reason, checked_at, proxy_str
        FROM client_tg_accounts
        WHERE client_key = ?
        ORDER BY id DESC
        """,
        (client_key,),
    ).fetchall()
    db.close()
    out = []
    for r in rows:
        item = dict(r)
        raw_p = decrypt_secret(item.pop("proxy_str", "") or "")
        item["proxy_display"] = format_proxy_display(raw_p)
        item["proxy_raw"] = raw_p
        out.append(item)
    return JSONResponse({"authenticated": True, "accounts": out})


@app.get("/api/spam-campaign")
def api_get_spam_campaign(request: Request):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return JSONResponse({"campaign": None}, status_code=401)
    return JSONResponse({"campaign": get_latest_spam_campaign(client_key)})


@app.post("/cabinet/check-account")
async def check_single_account(request: Request, acc_id: int = Form(...)):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    db = get_db()
    row = db.execute(
        "SELECT * FROM client_tg_accounts WHERE id = ? AND client_key = ?",
        (acc_id, client_key),
    ).fetchone()
    db.close()

    if not row:
        return RedirectResponse(url="/cabinet?message=Аккаунт не найден", status_code=303)

    checked = await check_and_sync_account(dict(row), client_key)
    if checked.get("client"):
        try:
            await checked["client"].disconnect()
        except Exception:
            pass

    status_label = checked.get("status") or "Неизвестно"
    reason_part = f" ({checked['status_reason']})" if checked.get("status_reason") else ""
    msg = urllib.parse.quote(f"Статус аккаунта: {status_label}{reason_part}")
    return RedirectResponse(url=f"/cabinet?message={msg}#accounts", status_code=303)


@app.get("/api/notifications")
def api_get_notifications(request: Request):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return JSONResponse({"notifications": []}, status_code=401)
    return JSONResponse({"notifications": get_site_notifications(client_key)})


@app.post("/api/notifications/read")
def api_read_notifications(request: Request):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return JSONResponse({"ok": False}, status_code=401)
    mark_notifications_read(client_key)
    return JSONResponse({"ok": True})


@app.get("/api/admin/banners")
def api_admin_banners(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return JSONResponse({"banners": get_all_home_banners()})


@app.post(f"{SECRET_PATH}/banners/add")
async def admin_add_banner(
    request: Request,
    title: str = Form("GHOSTCORE"),
    text: str = Form(""),
    url: str = Form(""),
    duration_sec: int = Form(8),
    active: str = Form("on"),
    image: UploadFile = File(None),
):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    image_url = await save_uploaded_banner_image(image)
    clean_duration = max(2, min(int(duration_sec or 8), 3600))
    is_active = 1 if active in ("on", "1", "true", "yes") else 0

    init_banners_table()
    db = get_db()
    db.execute(
        """
        INSERT INTO home_banners (title, text, url, image_url, duration_sec, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title.strip()[:80] or "GHOSTCORE",
            text.strip()[:300],
            url.strip()[:300],
            image_url,
            clean_duration,
            is_active,
            time.time(),
        ),
    )
    db.commit()
    db.close()

    return RedirectResponse(url=f"{SECRET_PATH}?message=Баннер+добавлен", status_code=303)


@app.post(f"{SECRET_PATH}/banners/toggle")
async def admin_toggle_banner(request: Request, banner_id: int = Form(...)):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    init_banners_table()
    db = get_db()
    row = db.execute("SELECT is_active FROM home_banners WHERE id = ?", (banner_id,)).fetchone()
    if row:
        next_val = 0 if int(row["is_active"]) == 1 else 1
        db.execute("UPDATE home_banners SET is_active = ? WHERE id = ?", (next_val, banner_id))
        db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_PATH}?message=Статус+баннера+обновлён", status_code=303)


@app.post(f"{SECRET_PATH}/banners/delete")
async def admin_delete_banner(request: Request, banner_id: int = Form(...)):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    init_banners_table()
    db = get_db()
    row = db.execute("SELECT image_url FROM home_banners WHERE id = ?", (banner_id,)).fetchone()
    if row and row["image_url"] and row["image_url"].startswith("/static/banners/"):
        old_path = os.path.join("static", "banners", os.path.basename(row["image_url"]))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    db.execute("DELETE FROM home_banners WHERE id = ?", (banner_id,))
    db.commit()
    db.close()
    return RedirectResponse(url=f"{SECRET_PATH}?message=Баннер+удалён", status_code=303)


@app.post(f"{SECRET_PATH}/banner/disable")
def admin_disable_legacy_banner(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    current_banner = get_banner() or {}
    update_banner_db(
        is_active=False,
        title=current_banner.get("title") or "GHOSTCORE",
        text=current_banner.get("text") or "",
        url=current_banner.get("url") or "",
        image_url=current_banner.get("image_url") or "",
    )
    return RedirectResponse(url=f"{SECRET_PATH}?message=Старый+баннер+отключён", status_code=303)


@app.post(f"{SECRET_PATH}/banner/clear-legacy")
def admin_clear_legacy_banner(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    current_banner = get_banner() or {}
    old_img = current_banner.get("image_url", "")
    if old_img.startswith("/static/banners/"):
        old_path = os.path.join("static", "banners", os.path.basename(old_img))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    update_banner_db(
        is_active=False,
        title="",
        text="",
        url="",
        image_url="",
    )
    return RedirectResponse(url=f"{SECRET_PATH}?message=Старый+баннер+очищен", status_code=303)


@app.get("/api/profile")
def api_get_profile(request: Request):
    client_key = get_session_key_from_request(request)
    user = get_authorized_client(client_key)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    profile = get_client_profile(client_key)
    if not profile:
        return JSONResponse({"authenticated": False}, status_code=401)
    profile["is_paid"] = bool(user.get("is_paid"))
    return JSONResponse({"authenticated": True, "profile": profile})


@app.post("/cabinet/update-profile")
async def update_profile_route(request: Request):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/?error=Доступ+заблокирован", status_code=303)
    profile = get_client_profile(client_key)
    if not profile:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    new_name = str(form.get("client_name", "")).strip()[:40]
    new_status = str(form.get("status_text", ""))[:80]

    if new_name:
        db = get_db()
        dup = db.execute(
            "SELECT id FROM access_keys WHERE LOWER(TRIM(client_name)) = LOWER(?) AND key_code != ?",
            (new_name, client_key),
        ).fetchone()
        db.close()
        if dup:
            return RedirectResponse(url="/cabinet?message=Это+имя+уже+занято#profile", status_code=303)

    update_client_profile(client_key, new_name or profile.get("client_name", ""), new_status)
    return RedirectResponse(url="/cabinet?message=Профиль+сохранён#profile", status_code=303)


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    client_key = get_session_key_from_ws(websocket)
    user = get_authorized_client(client_key)
    if not user:
        await websocket.close(code=1008)
        return

    author = user["client_name"] if user.get("client_name") else user["key_code"]

    await chat_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            raw_text = str(data.get("text", "")).strip()
            if not raw_text:
                continue

            fresh_user = get_authorized_client(client_key)
            if not fresh_user:
                await websocket.send_json({
                    "type": "system",
                    "text": "Ваш аккаунт заблокирован.",
                })
                await websocket.close(code=1008)
                return

            if int(fresh_user.get("is_muted") or 0) == 1:
                await websocket.send_json({
                    "type": "system",
                    "text": "Вы замучены в чате администратором.",
                })
                continue

            saved = add_chat_message(author=author, text=raw_text[:1000])
            b_label = (fresh_user.get("badge_label") or "").strip()
            b_style = (fresh_user.get("badge_style") or "vip").lower()
            await chat_manager.broadcast({
                "type": "message",
                "id": saved["id"],
                "author": saved["author"],
                "text": saved["text"],
                "created_at": saved["created_at"],
                "badge_label": b_label,
                "badge_style": b_style,
            })
    except WebSocketDisconnect:
        chat_manager.disconnect(websocket)
    except Exception as e:
        log_warn(f"WebSocket чат отключён: {e}")
        chat_manager.disconnect(websocket)


@app.post(f"{SECRET_PATH}/banner/update")
async def admin_update_banner(
    request: Request,
    title: str = Form("Промо"),
    text: str = Form(""),
    url: str = Form(""),
    duration_sec: int = Form(8),
    active: str = Form("off"),
    image: UploadFile = File(None),
):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    is_active = (active == "on")
    new_image_url = await save_uploaded_banner_image(image) if (image and image.filename) else None

    if new_image_url:
        current_banner = get_banner() or {}
        old_img = current_banner.get("image_url", "")
        if old_img.startswith("/static/banners/"):
            old_path = os.path.join("static", "banners", os.path.basename(old_img))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

    update_banner_db(
        is_active=is_active,
        title=title.strip()[:80] or "Промо",
        text=text.strip()[:300],
        url=url.strip()[:300],
        image_url=new_image_url,
    )

    if is_active and (text.strip() or new_image_url):
        clean_duration = max(2, min(int(duration_sec or 8), 3600))
        init_banners_table()
        db = get_db()
        db.execute(
            """
            INSERT INTO home_banners (title, text, url, image_url, duration_sec, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                title.strip()[:80] or "Промо",
                text.strip()[:300],
                url.strip()[:300],
                new_image_url or "",
                clean_duration,
                time.time(),
            ),
        )
        db.commit()
        db.close()

    return RedirectResponse(url=f"{SECRET_PATH}?message=Баннер+обновлён", status_code=303)


@app.post(f"{SECRET_PATH}/banner/clear-image")
def admin_clear_banner_image(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    current_banner = get_banner() or {}
    old_img = current_banner.get("image_url", "")
    if old_img.startswith("/static/banners/"):
        old_path = os.path.join("static", "banners", os.path.basename(old_img))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    update_banner_db(
        is_active=current_banner.get("active", False),
        title=current_banner.get("title", ""),
        text=current_banner.get("text", ""),
        url=current_banner.get("url", ""),
        image_url="",
    )
    return RedirectResponse(url=f"{SECRET_PATH}?message=Картинка+удалена", status_code=303)


@app.post(f"{SECRET_PATH}/chat/clear")
async def admin_clear_chat(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    clear_chat_messages_db()
    await chat_manager.broadcast({"type": "clear"})
    return RedirectResponse(url=f"{SECRET_PATH}?message=Чат+очищен", status_code=303)


@app.post(f"{SECRET_PATH}/chat/delete")
async def admin_delete_chat_message(request: Request, msg_id: int = Form(...)):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    delete_chat_message_db(msg_id)
    await chat_manager.broadcast({"type": "delete", "id": msg_id})
    return RedirectResponse(url=f"{SECRET_ADMIN_PATH}?message=Сообщение+удалено", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home_page(request: Request, error: str = None):
    err_html = f'<div class="err-box">{error}</div>' if error else ""
    client_key = get_session_key_from_request(request)
    user = get_authorized_client(client_key)
    is_auth = bool(user)

    drawer_links = (
        """
        <a href="/cabinet" class="drawer-link drawer-hot">Личный кабинет</a>
        <a href="/cabinet#spammer" class="drawer-link">Спамер</a>
        <a href="/cabinet#inviter" class="drawer-link">Инвайтер</a>
        <a href="/cabinet#parser" class="drawer-link">Парсер</a>
        <a href="/logout-client" class="drawer-link drawer-dim">Выйти из аккаунта</a>
        """
        if is_auth else
        """
        <a href="#guest-auth" class="drawer-link drawer-hot" onclick="closeDrawer()">Вход / Регистрация</a>
        """
    )

    chat_section_html = (
        """
        <section class="frame-card">
            <div class="frame-title">ACTIVE TRANSMISSIONS</div>
            <div id="chat-notice" class="chat-notice" style="display:none;"></div>
            <div id="chat-box" class="tx-list"></div>
            <form id="chat-form" class="tx-form">
                <input id="chat-input" type="text" class="field-box" placeholder="Type your message..." autocomplete="off" required>
                <button type="submit" class="btn-send" aria-label="Отправить">➤</button>
            </form>
        </section>
        """
        if is_auth else
        f"""
        <section id="guest-auth" class="frame-card">
            <div class="frame-title">SECURE ACCESS</div>
            {err_html}
            <form action="/login" method="POST" style="margin-bottom: 10px;">
                <input type="text" name="key_code" class="field-box" placeholder="KEY CODE" required style="text-transform: uppercase;">
                <button type="submit" class="btn-submit" style="width: 100%; margin-top: 8px;">SUBMIT ACCESS</button>
            </form>
            <div class="or-line">OR REGISTER NEW KEY</div>
            <form action="/register" method="POST">
                <input type="text" name="client_name" class="field-box" placeholder="YOUR NAME" required>
                <button type="submit" class="btn-ghost" style="width: 100%; margin-top: 8px;">REGISTER & GET KEY</button>
            </form>
        </section>
        """
    )

    chat_script = (
        """
        const chatBox = document.getElementById("chat-box");
        const chatForm = document.getElementById("chat-form");
        const chatInput = document.getElementById("chat-input");
        const chatNotice = document.getElementById("chat-notice");

        function showChatNotice(msg) {
            if (!chatNotice) return;
            chatNotice.textContent = msg;
            chatNotice.style.display = msg ? "block" : "none";
        }

        function appendMessage(id, author, text, badgeLabel, badgeStyle) {
            if (!chatBox) return;
            const item = document.createElement("div");
            item.className = "tx-item";
            if (id !== undefined && id !== null) item.dataset.msgId = String(id);

            if (badgeLabel) {
                const badge = document.createElement("span");
                const st = (badgeStyle || "vip").toLowerCase();
                badge.className = "chat-badge chat-badge-" + st;
                badge.textContent = badgeLabel;
                item.appendChild(badge);
            }

            const nick = document.createElement("span");
            nick.className = "tx-nick";
            nick.textContent = (author || "Anon") + ":";

            const msgText = document.createElement("span");
            msgText.textContent = " " + text;

            item.appendChild(nick);
            item.appendChild(msgText);
            chatBox.appendChild(item);
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        async function loadHomeData() {
            try {
                const res = await fetch("/api/home-data");
                if (!res.ok) return;
                const data = await res.json();
                if (data.pulse) renderPulse(data.pulse);
                startBannerRotation(Array.isArray(data.banners) ? data.banners : []);
                if (data.is_muted) {
                    showChatNotice("Вы замучены в чате администратором.");
                } else {
                    showChatNotice("");
                }
                if (data.authenticated && chatBox && Array.isArray(data.messages)) {
                    chatBox.innerHTML = "";
                    data.messages.forEach(m => appendMessage(m.id, m.author, m.text, m.badge_label, m.badge_style));
                }
            } catch (e) {
                console.error(e);
            }
        }

        const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(`${wsProtocol}//${location.host}/ws/chat`);

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                if (payload.type === "system") {
                    showChatNotice(payload.text || "Ограничение чата.");
                    return;
                }
                if (payload.type === "clear") { if (chatBox) chatBox.innerHTML = ""; return; }
                if (payload.type === "delete") {
                    const target = chatBox ? chatBox.querySelector(`[data-msg-id="${payload.id}"]`) : null;
                    if (target) target.remove();
                    return;
                }
                appendMessage(payload.id, payload.author, payload.text, payload.badge_label, payload.badge_style);
            } catch (e) {
                appendMessage(null, "System", event.data);
            }
        };

        if (chatForm) {
            chatForm.addEventListener("submit", (e) => {
                e.preventDefault();
                const text = chatInput.value.trim();
                if (!text || ws.readyState !== WebSocket.OPEN) return;
                ws.send(JSON.stringify({ text }));
                chatInput.value = "";
            });
        }

        loadHomeData();
        """
        if is_auth else
        """
        async function loadBannerOnly() {
            try {
                const res = await fetch("/api/home-data");
                if (!res.ok) return;
                const data = await res.json();
                if (data.pulse) renderPulse(data.pulse);
                startBannerRotation(Array.isArray(data.banners) ? data.banners : []);
            } catch (e) {
                console.error(e);
            }
        }
        loadBannerOnly();
        """
    )

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GHOSTCORE</title>
        <style>
            :root {{
                --bg-main: #060609;
                --panel-bg: #110b0e;
                --panel-border: #4a121d;
                --neon-red: #ff1e42;
                --neon-red-soft: rgba(255, 30, 66, 0.28);
                --text-main: #f5f5f7;
                --text-muted: #9e969b;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                padding: 12px;
                min-height: 100vh;
                background: radial-gradient(circle at top, #18070c 0%, var(--bg-main) 65%);
                color: var(--text-main);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }}
            .shell {{
                width: 100%;
                max-width: 420px;
                margin: 0 auto;
            }}
            .nav-bar {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 8px 4px 14px 4px;
            }}
            .burger-btn {{
                width: 38px;
                height: 38px;
                background: transparent;
                border: 1px solid rgba(255, 30, 66, 0.25);
                border-radius: 8px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                gap: 4px;
                cursor: pointer;
                padding: 0;
            }}
            .burger-btn span {{
                width: 18px;
                height: 2px;
                background: var(--text-main);
                border-radius: 2px;
            }}
            .logo-title {{
                font-size: 20px;
                font-weight: 800;
                letter-spacing: 2px;
                color: var(--neon-red);
                text-shadow: 0 0 12px var(--neon-red-soft);
                text-transform: uppercase;
            }}
            .avatar-btn {{
                width: 38px;
                height: 38px;
                border-radius: 9px;
                border: 1.5px solid var(--neon-red);
                box-shadow: 0 0 10px var(--neon-red-soft);
                background: #1b0b10;
                color: var(--neon-red);
                display: flex;
                align-items: center;
                justify-content: center;
                text-decoration: none;
                font-size: 16px;
                font-weight: 700;
            }}
            .drawer-mask {{
                position: fixed;
                inset: 0;
                background: rgba(0, 0, 0, 0.7);
                z-index: 80;
                display: none;
            }}
            .drawer-mask.open {{ display: block; }}
            .drawer {{
                position: fixed;
                top: 0;
                left: 0;
                width: 270px;
                max-width: 82vw;
                height: 100vh;
                background: #0d080b;
                border-right: 1px solid var(--panel-border);
                z-index: 90;
                padding: 16px;
                transform: translateX(-100%);
                transition: transform 0.2s ease;
                display: flex;
                flex-direction: column;
                gap: 8px;
            }}
            .drawer.open {{ transform: translateX(0); }}
            .drawer-top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding-bottom: 10px;
                border-bottom: 1px solid var(--panel-border);
            }}
            .drawer-close {{
                background: #1a0d12;
                border: 1px solid var(--panel-border);
                color: var(--text-main);
                border-radius: 6px;
                width: 28px;
                height: 28px;
                cursor: pointer;
            }}
            .drawer-cap {{
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: var(--text-muted);
                margin-top: 6px;
            }}
            .drawer-link {{
                display: block;
                padding: 10px 12px;
                border-radius: 8px;
                text-decoration: none;
                color: var(--text-main);
                background: #160c10;
                border: 1px solid #2d1017;
                font-size: 13px;
                font-weight: 600;
            }}
            .drawer-hot {{
                border-color: var(--neon-red);
                background: rgba(255, 30, 66, 0.14);
            }}
            .drawer-dim {{ color: var(--text-muted); }}
            .top-zone {{
                margin-bottom: 12px;
            }}
            .pulse-bar {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                padding: 8px 12px;
                border-radius: 10px;
                margin-bottom: 8px;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.4px;
                background: #14090e;
                border: 1px solid var(--neon-red);
                box-shadow: 0 0 10px var(--neon-red-soft);
            }}
            .pulse-left {{
                display: flex;
                align-items: center;
                gap: 8px;
                min-width: 0;
            }}
            .pulse-dot {{
                width: 8px;
                height: 8px;
                border-radius: 50%;
                flex: 0 0 auto;
                background: var(--neon-red);
                box-shadow: 0 0 8px var(--neon-red);
                animation: pulseBlink 1.6s infinite ease-in-out;
            }}
            @keyframes pulseBlink {{
                0%, 100% {{ opacity: 1; transform: scale(1); }}
                50% {{ opacity: 0.45; transform: scale(0.85); }}
            }}
            .pulse-tag {{
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                padding: 2px 6px;
                border-radius: 999px;
                border: 1px solid currentColor;
                flex: 0 0 auto;
            }}
            .pulse-live {{ border-color: #22c55e; box-shadow: 0 0 10px rgba(34, 197, 94, 0.25); }}
            .pulse-live .pulse-dot {{ background: #22c55e; box-shadow: 0 0 8px #22c55e; }}
            .pulse-live .pulse-tag {{ color: #22c55e; }}
            .pulse-drop {{ border-color: #a855f7; box-shadow: 0 0 10px rgba(168, 85, 247, 0.28); }}
            .pulse-drop .pulse-dot {{ background: #a855f7; box-shadow: 0 0 8px #a855f7; }}
            .pulse-drop .pulse-tag {{ color: #c084fc; }}
            .pulse-event {{ border-color: #38bdf8; box-shadow: 0 0 10px rgba(56, 189, 248, 0.25); }}
            .pulse-event .pulse-dot {{ background: #38bdf8; box-shadow: 0 0 8px #38bdf8; }}
            .pulse-event .pulse-tag {{ color: #38bdf8; }}
            .pulse-alert {{ border-color: var(--neon-red); box-shadow: 0 0 10px var(--neon-red-soft); }}
            .pulse-alert .pulse-dot {{ background: var(--neon-red); box-shadow: 0 0 8px var(--neon-red); }}
            .pulse-alert .pulse-tag {{ color: var(--neon-red); }}
            .banner-card {{
                display: none;
                position: relative;
                border-radius: 12px;
                overflow: hidden;
                border: 1.5px solid var(--neon-red);
                box-shadow: 0 0 14px var(--neon-red-soft);
                background: #13080c;
                text-decoration: none;
                color: var(--text-main);
                margin-bottom: 8px;
                opacity: 1;
                transform: translateY(0) scale(1);
                transition: opacity 0.35s ease, transform 0.35s ease;
            }}
            .banner-card.banner-fade-out {{
                opacity: 0;
                transform: translateY(-6px) scale(0.985);
            }}
            .banner-img {{
                width: 100%;
                max-height: 170px;
                object-fit: cover;
                display: none;
            }}
            .banner-overlay {{
                padding: 11px 13px;
                background: linear-gradient(90deg, rgba(12, 5, 8, 0.92) 0%, rgba(12, 5, 8, 0.55) 100%);
            }}
            .banner-top-row {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
            }}
            .banner-title {{
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                color: var(--text-main);
            }}
            .banner-timer-pill {{
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.6px;
                color: var(--neon-red);
                background: rgba(255, 30, 66, 0.14);
                border: 1px solid rgba(255, 30, 66, 0.45);
                border-radius: 999px;
                padding: 2px 7px;
                white-space: nowrap;
            }}
            .banner-sub {{
                font-size: 12px;
                color: #e5d4d8;
                margin-top: 3px;
            }}
            .banner-progress-track {{
                width: 100%;
                height: 2px;
                background: rgba(255, 30, 66, 0.16);
                overflow: hidden;
            }}
            .banner-progress-bar {{
                height: 100%;
                width: 100%;
                background: linear-gradient(90deg, #c71230, var(--neon-red));
                transform-origin: left center;
                transition: none;
            }}
            .frame-card {{
                background: var(--panel-bg);
                border: 1.5px solid var(--panel-border);
                border-radius: 12px;
                padding: 13px;
                margin-bottom: 12px;
                box-shadow: inset 0 0 18px rgba(255, 30, 66, 0.06);
            }}
            .frame-title {{
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1px;
                text-transform: uppercase;
                color: var(--text-main);
                margin-bottom: 10px;
            }}
            .chat-notice {{
                font-size: 11px;
                color: #fcd34d;
                background: rgba(245, 158, 11, 0.14);
                border: 1px solid rgba(245, 158, 11, 0.4);
                border-radius: 7px;
                padding: 6px 9px;
                margin-bottom: 8px;
            }}
            .tx-list {{
                min-height: 180px;
                max-height: 320px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 7px;
                margin-bottom: 10px;
                padding: 6px;
                background: #0a0608;
                border: 1px solid #2c0e15;
                border-radius: 8px;
            }}
            .tx-item {{
                background: #1a0d12;
                border: 1px solid #351119;
                border-radius: 7px;
                padding: 7px 10px;
                font-size: 13px;
                line-height: 1.35;
                word-break: break-word;
            }}
            .tx-nick {{
                color: var(--neon-red);
                font-weight: 700;
                margin-right: 5px;
            }}
            .chat-badge {{
                display: inline-block;
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.6px;
                text-transform: uppercase;
                padding: 1px 6px;
                border-radius: 999px;
                margin-right: 6px;
                vertical-align: middle;
            }}
            .chat-badge-vip {{ background: rgba(255, 30, 66, 0.18); color: #ff8fa3; border: 1px solid rgba(255, 30, 66, 0.5); }}
            .chat-badge-staff {{ background: rgba(34, 197, 94, 0.18); color: #86efac; border: 1px solid rgba(34, 197, 94, 0.5); }}
            .chat-badge-mod {{ background: rgba(56, 189, 248, 0.18); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.5); }}
            .chat-badge-tester {{ background: rgba(168, 85, 247, 0.18); color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.5); }}
            .chat-badge-pro {{ background: rgba(245, 158, 11, 0.18); color: #fcd34d; border: 1px solid rgba(245, 158, 11, 0.5); }}
            .chat-badge-alert {{ background: rgba(239, 68, 68, 0.22); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.6); }}
            .tx-form {{
                display: flex;
                gap: 8px;
                align-items: center;
            }}
            .field-box {{
                width: 100%;
                padding: 9px 12px;
                background: #14090d;
                border: 1px solid #571522;
                border-radius: 7px;
                color: var(--text-main);
                font-size: 13px;
                outline: none;
            }}
            .field-box:focus {{
                border-color: var(--neon-red);
                box-shadow: 0 0 8px var(--neon-red-soft);
            }}
            .btn-send {{
                width: 44px;
                height: 36px;
                flex: 0 0 auto;
                border: none;
                border-radius: 7px;
                background: linear-gradient(135deg, #c71230, var(--neon-red));
                color: #fff;
                font-size: 15px;
                font-weight: 700;
                cursor: pointer;
                box-shadow: 0 0 10px var(--neon-red-soft);
            }}
            .btn-submit {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 10px 14px;
                border: none;
                border-radius: 7px;
                background: linear-gradient(135deg, #c71230, var(--neon-red));
                color: #fff;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1px;
                text-transform: uppercase;
                cursor: pointer;
                box-shadow: 0 0 12px var(--neon-red-soft);
                white-space: nowrap;
                text-decoration: none;
            }}
            .btn-ghost {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 10px 14px;
                border-radius: 7px;
                background: #180c11;
                border: 1px solid #4a121d;
                color: var(--text-main);
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                cursor: pointer;
                white-space: nowrap;
                text-decoration: none;
            }}
            .or-line {{
                text-align: center;
                font-size: 10px;
                letter-spacing: 1px;
                color: var(--text-muted);
                margin: 8px 0;
            }}
            .err-box {{
                color: var(--neon-red);
                font-size: 12px;
                text-align: center;
                margin-bottom: 8px;
            }}
        </style>
    </head>
    <body>
        <div id="drawer-mask" class="drawer-mask" onclick="closeDrawer()"></div>
        <aside id="side-drawer" class="drawer">
            <div class="drawer-top">
                <span class="logo-title" style="font-size: 15px;">GHOSTCORE</span>
                <button type="button" class="drawer-close" onclick="closeDrawer()">✕</button>
            </div>
            <div class="drawer-cap">Навигация</div>
            <a href="/" class="drawer-link" onclick="closeDrawer()">Главная</a>
            {drawer_links}
        </aside>

        <div class="shell">
            <header class="nav-bar">
                <button type="button" class="burger-btn" aria-label="Меню" onclick="openDrawer()">
                    <span></span>
                    <span></span>
                    <span></span>
                </button>
                <div class="logo-title">GHOSTCORE</div>
                <a href="/cabinet" class="avatar-btn" title="Кабинет">👤</a>
            </header>

            <div class="top-zone">
                <div id="live-pulse-bar" class="pulse-bar pulse-live" style="display:none;">
                    <div class="pulse-left">
                        <span class="pulse-dot"></span>
                        <span id="live-pulse-text"></span>
                    </div>
                    <span id="live-pulse-tag" class="pulse-tag">LIVE</span>
                </div>
                <a id="ad-banner" href="#" target="_blank" class="banner-card">
                    <img id="ad-image" class="banner-img" src="" alt="Banner">
                    <div id="ad-body" class="banner-overlay">
                        <div class="banner-top-row">
                            <div id="ad-title" class="banner-title">GHOSTCORE</div>
                            <span id="ad-counter" class="banner-timer-pill" style="display:none;"></span>
                        </div>
                        <div id="ad-text" class="banner-sub"></div>
                    </div>
                    <div id="ad-progress-track" class="banner-progress-track" style="display:none;">
                        <div id="ad-progress-bar" class="banner-progress-bar"></div>
                    </div>
                </a>
            </div>

            {chat_section_html}
        </div>

        <script>
            const drawer = document.getElementById("side-drawer");
            const mask = document.getElementById("drawer-mask");

            function openDrawer() {{
                drawer.classList.add("open");
                mask.classList.add("open");
            }}
            function closeDrawer() {{
                drawer.classList.remove("open");
                mask.classList.remove("open");
            }}

            let bannerTimerId = null;
            let bannerQueue = [];
            let bannerIndex = 0;

            function renderBannerItem(banner, index, total) {{
                const bannerEl = document.getElementById("ad-banner");
                const imgEl = document.getElementById("ad-image");
                const bodyEl = document.getElementById("ad-body");
                const titleEl = document.getElementById("ad-title");
                const textEl = document.getElementById("ad-text");
                const counterEl = document.getElementById("ad-counter");
                const trackEl = document.getElementById("ad-progress-track");
                const barEl = document.getElementById("ad-progress-bar");

                if (!banner || (!banner.text && !banner.image_url && !banner.title)) {{
                    bannerEl.style.display = "none";
                    return;
                }}

                if (banner.url) bannerEl.href = banner.url;
                else bannerEl.removeAttribute("href");

                if (banner.image_url) {{
                    imgEl.src = banner.image_url;
                    imgEl.style.display = "block";
                }} else {{
                    imgEl.style.display = "none";
                    imgEl.src = "";
                }}

                titleEl.textContent = banner.title || "GHOSTCORE";
                textEl.textContent = banner.text || "";
                bodyEl.style.display = "block";

                if (total > 1) {{
                    const sec = Math.max(2, Number(banner.duration_sec || 8));
                    counterEl.textContent = `${{index + 1}}/${{total}} • ${{sec}}s`;
                    counterEl.style.display = "inline-block";
                    trackEl.style.display = "block";

                    barEl.style.transition = "none";
                    barEl.style.transform = "scaleX(1)";
                    void barEl.offsetWidth;
                    barEl.style.transition = `transform ${{sec}}s linear`;
                    barEl.style.transform = "scaleX(0)";
                }} else {{
                    counterEl.style.display = "none";
                    trackEl.style.display = "none";
                }}

                bannerEl.style.display = "block";
                bannerEl.classList.remove("banner-fade-out");
            }}

            function startBannerRotation(banners) {{
                if (bannerTimerId) {{
                    clearTimeout(bannerTimerId);
                    bannerTimerId = null;
                }}
                bannerQueue = Array.isArray(banners) ? banners : [];
                bannerIndex = 0;

                const bannerEl = document.getElementById("ad-banner");
                if (!bannerQueue.length) {{
                    bannerEl.style.display = "none";
                    return;
                }}

                const step = () => {{
                    const current = bannerQueue[bannerIndex % bannerQueue.length];
                    renderBannerItem(current, bannerIndex % bannerQueue.length, bannerQueue.length);

                    if (bannerQueue.length > 1) {{
                        const sec = Math.max(2, Number(current.duration_sec || 8));
                        bannerTimerId = setTimeout(() => {{
                            bannerEl.classList.add("banner-fade-out");
                            setTimeout(() => {{
                                bannerIndex = (bannerIndex + 1) % bannerQueue.length;
                                step();
                            }}, 340);
                        }}, sec * 1000);
                    }}
                }};

                step();
            }}

            function renderPulse(pulse) {{
                const bar = document.getElementById("live-pulse-bar");
                const txt = document.getElementById("live-pulse-text");
                const tag = document.getElementById("live-pulse-tag");
                if (!bar || !txt || !tag) return;
                if (!pulse || !pulse.status_text) {{
                    bar.style.display = "none";
                    return;
                }}
                const badge = (pulse.badge_type || "live").toLowerCase();
                bar.className = "pulse-bar pulse-" + badge;
                txt.textContent = pulse.status_text;
                tag.textContent = badge.toUpperCase();
                bar.style.display = "flex";
            }}

            async function refreshPulse() {{
                try {{
                    const res = await fetch("/api/pulse", {{ cache: "no-store" }});
                    if (!res.ok) return;
                    const data = await res.json();
                    renderPulse(data.pulse);
                }} catch (e) {{}}
            }}

            refreshPulse();
            setInterval(refreshPulse, 15000);

            {chat_script}
        </script>
    </body>
    </html>
    """


@app.post("/register", response_class=HTMLResponse)
def register_client(request: Request, client_name: str = Form(...)):
    ip = get_client_ip(request.headers, request.client.host if request.client else None)
    allowed, wait_sec = check_rate_limit(f"register:{ip}", max_attempts=5, window_sec=120, block_sec=300)
    if not allowed:
        return RedirectResponse(url=f"/?error=Слишком+много+запросов.+Ждите+{wait_sec}+сек", status_code=303)

    clean_name = client_name.strip()[:40]
    if not clean_name:
        return RedirectResponse(url="/?error=Введите имя", status_code=303)

    init_banners_table()
    db = get_db()
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT id FROM access_keys WHERE LOWER(TRIM(client_name)) = LOWER(?)",
        (clean_name,),
    ).fetchone()
    if existing:
        db.close()
        record_failed_attempt(f"register:{ip}")
        return RedirectResponse(url="/?error=Пользователь с таким именем уже существует", status_code=303)

    new_key = str(uuid.uuid4())[:8].upper()
    cursor.execute(
        "INSERT INTO access_keys (key_code, status, client_name, is_paid) VALUES (?, 'active', ?, 0)",
        (new_key, clean_name),
    )
    db.commit()
    db.close()
    clear_failed_attempts(f"register:{ip}")

    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GHOSTCORE | Ваш ключ доступа</title>
        <style>
            :root {{
                --bg-main: #060609;
                --panel-bg: #110b0e;
                --panel-border: #4a121d;
                --neon-red: #ff1e42;
                --neon-red-soft: rgba(255, 30, 66, 0.28);
                --text-main: #f5f5f7;
                --text-muted: #9e969b;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                padding: 14px;
                min-height: 100vh;
                background: radial-gradient(circle at top, #18070c 0%, var(--bg-main) 65%);
                color: var(--text-main);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .card {{
                width: 100%;
                max-width: 400px;
                background: var(--panel-bg);
                border: 1.5px solid var(--panel-border);
                border-radius: 12px;
                padding: 18px;
                box-shadow: 0 0 20px var(--neon-red-soft);
            }}
            h2 {{
                margin: 0 0 8px 0;
                font-size: 16px;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: var(--neon-red);
            }}
            p {{
                margin: 0 0 14px 0;
                font-size: 12px;
                color: var(--text-muted);
                line-height: 1.4;
            }}
            .key-box {{
                background: #090507;
                border: 1.5px solid var(--neon-red);
                border-radius: 8px;
                padding: 12px;
                text-align: center;
                font-family: monospace;
                font-size: 20px;
                font-weight: 800;
                letter-spacing: 2px;
                color: #fff;
                margin-bottom: 12px;
                user-select: all;
            }}
            .btn {{
                width: 100%;
                padding: 11px 14px;
                border-radius: 8px;
                border: none;
                font-size: 12px;
                font-weight: 800;
                text-transform: uppercase;
                letter-spacing: 0.8px;
                cursor: pointer;
                text-decoration: none;
                display: block;
                text-align: center;
                margin-top: 8px;
            }}
            .btn-red {{
                background: linear-gradient(135deg, #c71230, var(--neon-red));
                color: #fff;
                box-shadow: 0 0 12px var(--neon-red-soft);
            }}
            .btn-dark {{
                background: #180c11;
                border: 1px solid var(--panel-border);
                color: var(--text-main);
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Ключ создан</h2>
            <p>Аккаунт <b>{clean_name}</b> зарегистрирован. Сохраните ваш ключ — он понадобится для повторного входа:</p>
            <div id="reg-key" class="key-box">{new_key}</div>
            <button type="button" class="btn btn-dark" onclick="copyKey()">Скопировать ключ</button>
            <a href="/cabinet" class="btn btn-red">Перейти в кабинет</a>
            <a href="/" class="btn btn-dark">На главную (чат)</a>
        </div>
        <script>
            function copyKey() {{
                const keyText = document.getElementById("reg-key").textContent.trim();
                if (navigator.clipboard) {{
                    navigator.clipboard.writeText(keyText).catch(() => {{}});
                }}
            }}
        </script>
    </body>
    </html>
    """
    resp = HTMLResponse(content=html)
    resp.set_cookie(
        key=CLIENT_COOKIE_NAME,
        value=sign_value(new_key),
        httponly=True,
        samesite="lax",
        secure=IS_HTTPS,
        path="/",
    )
    return resp


@app.post("/login")
def login_client(request: Request, key_code: str = Form(...)):
    ip = get_client_ip(request.headers, request.client.host if request.client else None)
    bucket = f"client_login:{ip}"
    allowed, wait_sec = check_rate_limit(bucket)
    if not allowed:
        return RedirectResponse(url=f"/?error=Слишком+много+попыток.+Ждите+{wait_sec}+сек", status_code=303)

    clean_key = key_code.strip().upper()
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM access_keys WHERE key_code = ?", (clean_key,))
    key_row = cursor.fetchone()
    db.close()

    if not key_row:
        record_failed_attempt(bucket)
        return RedirectResponse(url="/?error=Неверный ключ доступа", status_code=303)

    if (key_row["status"] or "").lower() == "banned":
        record_failed_attempt(bucket)
        return RedirectResponse(url="/?error=Аккаунт заблокирован", status_code=303)

    if key_row["status"] == "free":
        db = get_db()
        cursor = db.cursor()
        cursor.execute("UPDATE access_keys SET status = 'active' WHERE id = ?", (key_row["id"],))
        db.commit()
        db.close()

    clear_failed_attempts(bucket)
    resp = RedirectResponse(url="/cabinet", status_code=303)
    resp.set_cookie(
        key=CLIENT_COOKIE_NAME,
        value=sign_value(clean_key),
        httponly=True,
        samesite="lax",
        secure=IS_HTTPS,
        path="/",
    )
    return resp


@app.get("/cabinet", response_class=HTMLResponse)
def client_cabinet(request: Request):
    client_key = get_session_key_from_request(request)
    if not client_key:
        return RedirectResponse(url="/", status_code=303)

    key_row = get_authorized_client(client_key)
    if not key_row:
        resp = RedirectResponse(url="/?error=Аккаунт заблокирован", status_code=303)
        resp.delete_cookie(key=CLIENT_COOKIE_NAME, path="/")
        return resp

    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/cabinet/start-qr", response_class=HTMLResponse)
async def start_qr_auth(request: Request):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    try:
        qr = await client.qr_login()
        session_id = uuid.uuid4().hex
        qr_sessions[session_id] = {"client": client, "qr": qr, "owner_key": client_key, "created_at": time.time()}

        img = qrcode.make(qr.url)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()

        return f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>GHOSTCORE | QR Авторизация</title>
            <style>
                :root {{
                    --bg-color: #050507;
                    --card-bg: #0f0f13;
                    --neon-red: #ff1e42;
                    --neon-red-glow: rgba(255, 30, 66, 0.4);
                    --border-color: #261118;
                    --text-main: #f3f4f6;
                    --text-muted: #9ca3af;
                }}
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: var(--bg-color);
                    color: var(--text-main);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 15px;
                    box-sizing: border-box;
                }}
                .card {{
                    background: var(--card-bg);
                    border: 1px solid var(--border-color);
                    padding: 24px;
                    border-radius: 12px;
                    width: 100%;
                    max-width: 300px;
                    text-align: center;
                    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.7);
                    box-sizing: border-box;
                }}
                h3 {{
                    margin-top: 0;
                    margin-bottom: 10px;
                    font-size: 14px;
                    color: var(--text-main);
                    letter-spacing: 0.5px;
                    text-transform: uppercase;
                }}
                img {{
                    background: white;
                    padding: 8px;
                    border-radius: 8px;
                    margin: 10px 0;
                    width: 160px;
                    height: 160px;
                }}
                .btn-action {{
                    background: linear-gradient(135deg, #cc1433, #ff1e42);
                    color: white;
                    border: none;
                    padding: 11px;
                    width: 100%;
                    border-radius: 8px;
                    cursor: pointer;
                    font-weight: bold;
                    font-size: 12px;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                    box-shadow: 0 0 10px var(--neon-red-glow);
                    text-decoration: none;
                    display: inline-block;
                    box-sizing: border-box;
                    margin-top: 8px;
                }}
                .btn-back {{
                    display: inline-block;
                    margin-top: 12px;
                    color: var(--text-muted);
                    text-decoration: none;
                    font-size: 11px;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                }}
                .btn-back:hover {{ color: var(--text-main); }}
            </style>
        </head>
        <body>
            <div class="card">
                <h3>Вход по QR-коду</h3>
                <p style="font-size: 10px; color: var(--text-muted); text-transform: uppercase; line-height: 1.3; margin-bottom: 10px;">
                    Откройте Telegram:<br><b style="color: var(--text-main);">Настройки → Устройства → Подключить</b>
                </p>
                <img src="data:image/png;base64,{img_str}" alt="QR Code">
                <p style="font-size: 10px; color: var(--neon-red); text-transform: uppercase; letter-spacing: 1px; font-weight: bold; margin: 8px 0;">Ожидание сканирования...</p>
                <a href="/cabinet/check-qr?sid={session_id}" class="btn-action">Проверить вход</a>
                <br>
                <a href="/cabinet" class="btn-back">← Назад в кабинет</a>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        await client.disconnect()
        return RedirectResponse(url=f"/cabinet?message=Ошибка запуска QR: {str(e)}", status_code=303)


@app.get("/cabinet/check-qr", response_class=HTMLResponse)
async def check_qr_auth(request: Request, sid: str):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    if sid not in qr_sessions:
        msg = urllib.parse.quote("QR-сессия истекла или сервер перезапускался. Откройте QR заново.")
        return RedirectResponse(url=f"/cabinet?message={msg}#accounts", status_code=303)

    data = qr_sessions[sid]
    if data.get("owner_key") != client_key:
        return RedirectResponse(url="/cabinet#accounts", status_code=303)

    client = data["client"]
    qr = data["qr"]

    try:
        if not client.is_connected():
            await client.connect()

        authorized = await client.is_user_authorized()
        if not authorized:
            try:
                await asyncio.wait_for(qr.wait(), timeout=4)
            except asyncio.TimeoutError:
                authorized = await client.is_user_authorized()
            else:
                authorized = True

        if not authorized:
            msg = urllib.parse.quote("Вход по QR ещё не подтверждён в Telegram. Подтвердите вход и нажмите «Проверить вход».")
            return RedirectResponse(url=f"/cabinet?message={msg}#accounts", status_code=303)

        me = await client.get_me()
        phone_str = f"+{me.phone}" if me and me.phone else "QR-Аккаунт"
        raw_session = client.session.save()
        final_session_str = encrypt_secret(raw_session)

        db = get_db()
        cursor = db.cursor()
        existing = cursor.execute(
            "SELECT id FROM client_tg_accounts WHERE client_key = ? AND phone = ?",
            (client_key, phone_str),
        ).fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE client_tg_accounts
                SET session_string = ?, status = ?, status_reason = 'Сессия активна', checked_at = ?
                WHERE id = ?
                """,
                (final_session_str, ACCOUNT_STATUS_ACTIVE, time.strftime("%Y-%m-%d %H:%M:%S"), existing["id"]),
            )
        else:
            _insert_client_tg_account(
                cursor,
                client_key=client_key,
                phone=phone_str,
                session_string=final_session_str,
                phone_code_hash=None,
                status=ACCOUNT_STATUS_ACTIVE,
                status_reason="Сессия активна",
                checked_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        db.commit()
        db.close()

        qr_sessions.pop(sid, None)
        try:
            await client.disconnect()
        except Exception:
            pass

        log_ok(f"QR-аккаунт подключён: {phone_str}")
        notify_user_sync(client_key, f"Подключён новый TG-аккаунт по QR: {phone_str}")
        msg = urllib.parse.quote(f"QR-код успешно сканирован! Аккаунт: {phone_str}")
        return RedirectResponse(url=f"/cabinet?message={msg}#accounts", status_code=303)
    except Exception as e:
        log_err(f"Ошибка QR-входа: {e}")
        msg = urllib.parse.quote(f"Ошибка QR: {str(e)}")
        return RedirectResponse(url=f"/cabinet?message={msg}#accounts", status_code=303)


@app.post("/cabinet/add-account")
async def add_client_account(request: Request, phone: str = Form(...)):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    clean_phone = phone.strip()
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    try:
        sent = await client.send_code_request(clean_phone)
        phone_code_hash = encrypt_secret(sent.phone_code_hash)
        session_str = encrypt_secret(client.session.save())
        await client.disconnect()

        db = get_db()
        cursor = db.cursor()
        _insert_client_tg_account(
            cursor,
            client_key=client_key,
            phone=clean_phone,
            session_string=session_str,
            phone_code_hash=phone_code_hash,
            status="Ожидает код",
            status_reason="Ожидание кода подтверждения",
            checked_at="",
        )
        db.commit()
        db.close()

        log_ok(f"Запрошен код для {clean_phone}")
        return RedirectResponse(url="/cabinet?message=Код отправлен!", status_code=303)
    except Exception as e:
        await client.disconnect()
        log_err(f"Ошибка запроса кода для {clean_phone}: {e}")
        return RedirectResponse(url=f"/cabinet?message=Ошибка запроса кода: {str(e)}", status_code=303)


@app.post("/cabinet/verify-code")
async def verify_client_code(request: Request, acc_id: int = Form(...), code: str = Form(...)):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM client_tg_accounts WHERE id = ? AND client_key = ?", (acc_id, client_key))
    acc = cursor.fetchone()
    db.close()

    if not acc:
        return RedirectResponse(url="/cabinet?message=Аккаунт не найден", status_code=303)

    plain_session = decrypt_secret(acc["session_string"] or "")
    plain_hash = decrypt_secret(acc["phone_code_hash"] or "")
    client = TelegramClient(StringSession(plain_session), API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(phone=acc["phone"], code=code.strip(), phone_code_hash=plain_hash)
        final_session_str = encrypt_secret(client.session.save())
        await client.disconnect()

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            UPDATE client_tg_accounts
            SET session_string = ?, phone_code_hash = '', status = ?, status_reason = 'Сессия активна', checked_at = ?
            WHERE id = ?
            """,
            (final_session_str, ACCOUNT_STATUS_ACTIVE, time.strftime("%Y-%m-%d %H:%M:%S"), acc_id),
        )
        db.commit()
        db.close()

        log_ok(f"Аккаунт {acc['phone']} авторизован ({ACCOUNT_STATUS_ACTIVE})")
        notify_user_sync(client_key, f"TG-аккаунт {acc['phone']} успешно авторизован в кабинете.")
        return RedirectResponse(url="/cabinet?message=Аккаунт успешно авторизован!", status_code=303)
    except Exception as e:
        await client.disconnect()
        log_err(f"Ошибка входа по коду {acc['phone']}: {e}")
        return RedirectResponse(url=f"/cabinet?message=Ошибка авторизации: {str(e)}", status_code=303)


@app.post("/cabinet/set-account-proxy")
def set_account_proxy(request: Request, acc_id: int = Form(...), proxy_url: str = Form("")):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    clean_proxy = (proxy_url or "").strip()
    if clean_proxy and parse_proxy_tuple(clean_proxy) is None:
        err = urllib.parse.quote("Неверный формат прокси. Пример: socks5://user:pass@ip:port или ip:port:user:pass")
        return RedirectResponse(url=f"/cabinet?message={err}#accounts", status_code=303)

    enc_proxy = encrypt_secret(clean_proxy) if clean_proxy else ""
    db = get_db()
    cur = db.execute(
        "UPDATE client_tg_accounts SET proxy_str = ? WHERE id = ? AND client_key = ?",
        (enc_proxy, acc_id, client_key),
    )
    db.commit()
    db.close()

    if cur.rowcount == 0:
        return RedirectResponse(url="/cabinet?message=Аккаунт+не+найден#accounts", status_code=303)

    msg = urllib.parse.quote("Прокси сохранён!" if clean_proxy else "Прокси отключён")
    return RedirectResponse(url=f"/cabinet?message={msg}#accounts", status_code=303)


@app.post("/cabinet/delete-account")
def delete_client_account(request: Request, acc_id: int = Form(...)):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return RedirectResponse(url="/", status_code=303)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM client_tg_accounts WHERE id = ? AND client_key = ?", (acc_id, client_key))
    db.commit()
    db.close()

    return RedirectResponse(url="/cabinet?message=Аккаунт удален", status_code=303)


@app.post("/cabinet/run-task")
async def run_client_task(request: Request):
    client_key = get_session_key_from_request(request)
    user = get_authorized_client(client_key)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    if int(user.get("is_paid") or 0) != 1:
        return RedirectResponse(url="/cabinet?message=Для запуска задач нужен оплаченный тариф (VIP)", status_code=303)

    form = await request.form()
    task_type = form.get("task_type")

    raw_selected_ids = form.getlist("account_ids[]") or form.getlist("account_ids")
    selected_ids = {int(x) for x in raw_selected_ids if str(x).strip().isdigit()}

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM client_tg_accounts WHERE client_key = ? AND status = ?",
        (client_key, ACCOUNT_STATUS_ACTIVE),
    )
    active_accs = [dict(r) for r in cursor.fetchall()]
    if selected_ids:
        active_accs = [a for a in active_accs if int(a["id"]) in selected_ids]

    if not active_accs:
        res_text = "Ошибка: Выбранные аккаунты неактивны или не найдены!" if selected_ids else "Ошибка: Нет активных авторизованных аккаунтов!"
    else:
        clients = []
        valid_meta = []
        for acc in active_accs:
            checked = await check_and_sync_account(acc, client_key)
            if checked.get("client"):
                clients.append(checked["client"])
                valid_meta.append({"id": checked["id"], "phone": checked.get("phone") or ""})

        if not clients:
            res_text = "Ошибка: Ни один из сохраненных аккаунтов не авторизован в Telegram!"
        else:
            try:
                if task_type == "spammer":
                    resume_flag = str(form.get("resume_campaign", "")).lower() in ("1", "true", "on", "yes")
                    raw_camp_id = str(form.get("campaign_id", "")).strip()
                    camp_id = int(raw_camp_id) if raw_camp_id.isdigit() else None

                    targets_raw = form.get("spam_targets", "").strip()
                    targets = [t.strip() for t in targets_raw.split("\n") if t.strip()]
                    target_type = form.get("target_type", "chats")
                    spam_mode = form.get("spam_mode", "text")
                    spam_order = form.get("spam_order", "sequential")
                    delay_sec = int(form.get("spam_delay", 5))
                    limit_per_account = int(form.get("spam_limit", 20))

                    texts = form.getlist("spam_texts[]") if spam_mode == "text" else None
                    forward_links = form.getlist("spam_links[]") if spam_mode == "forward" else None
                    content_items = [x.strip() for x in ((texts or []) if spam_mode == "text" else (forward_links or [])) if x and x.strip()]

                    existing_camp = get_spam_campaign_by_id(camp_id, client_key) if camp_id else None
                    if not existing_camp and resume_flag:
                        existing_camp = get_latest_spam_campaign(client_key)

                    if existing_camp and (resume_flag or not targets):
                        camp = existing_camp
                        use_targets = camp["remaining_targets"]
                        use_content = content_items or camp["content_items"]
                        use_type = camp["target_type"]
                        use_mode = camp["spam_mode"]
                        use_order = camp["spam_order"]
                        use_delay = int(form.get("spam_delay") or camp["delay_sec"] or 5)
                        use_limit = int(form.get("spam_limit") or camp["limit_per_account"] or 20)
                    else:
                        camp = create_or_update_spam_campaign(
                            client_key=client_key,
                            targets=targets,
                            content_items=content_items,
                            target_type=target_type,
                            spam_mode=spam_mode,
                            spam_order=spam_order,
                            delay_sec=delay_sec,
                            limit_per_account=limit_per_account,
                            campaign_id=camp_id,
                        )
                        use_targets = camp["remaining_targets"]
                        use_content = camp["content_items"]
                        use_type = camp["target_type"]
                        use_mode = camp["spam_mode"]
                        use_order = camp["spam_order"]
                        use_delay = camp["delay_sec"]
                        use_limit = camp["limit_per_account"]

                    res = await spammer.run_spammer(
                        clients=clients,
                        targets=use_targets,
                        target_type=use_type,
                        spam_mode=use_mode,
                        spam_order=use_order,
                        texts=use_content if use_mode == "text" else None,
                        forward_links=use_content if use_mode == "forward" else None,
                        limit_per_account=use_limit,
                        delay_sec=use_delay,
                        already_sent=camp["sent_targets"],
                        already_nonexistent=camp["nonexistent_targets"],
                        account_meta=valid_meta,
                    )

                    saved_camp = save_spam_campaign_progress(
                        campaign_id=camp["id"],
                        remaining_targets=res.get("remaining_targets", []),
                        sent_targets=res.get("sent_targets", []),
                        nonexistent_targets=res.get("nonexistent_targets", []),
                        failed_accounts=res.get("failed_accounts", []),
                    )

                    res_text = (
                        f"Рассылка #{saved_camp['id']} ({saved_camp['status']}): "
                        f"отправлено={len(saved_camp['sent_targets'])}, "
                        f"не существует={len(saved_camp['nonexistent_targets'])}, "
                        f"осталось={len(saved_camp['remaining_targets'])}"
                    )

                elif task_type == "inviter":
                    target_chat = form.get("inviter_target", "").strip()
                    sources_raw = form.get("inviter_sources", "").strip()
                    sources = [s.strip() for s in sources_raw.split("\n") if s.strip()] if sources_raw else None

                    direct_users_raw = form.get("inviter_direct_users", "").strip()
                    direct_users = [u.strip() for u in direct_users_raw.split("\n") if u.strip()] if direct_users_raw else None

                    limit_per_account = int(form.get("inviter_limit", 10))
                    delay_min = int(form.get("inviter_delay_min", 10))
                    delay_max = int(form.get("inviter_delay_max", 20))

                    res = await inviter.run_inviter(
                        clients=clients,
                        target_chat=target_chat,
                        sources=sources,
                        direct_users=direct_users,
                        limit_per_account=limit_per_account,
                        delay_min=delay_min,
                        delay_max=delay_max,
                    )
                    if res.get("flood_wait", 0) > 0:
                        for acc in active_accs:
                            set_tg_account_status(acc["id"], ACCOUNT_STATUS_SPAM, "Словлен FloodWait при инвайте")
                            log_warn(f"Аккаунт {acc['phone']}: FloodWait -> {ACCOUNT_STATUS_SPAM}")
                    res_text = f"Инвайтер завершен. Добавлено: {res.get('success', 0)}, Ошибок: {res.get('failed', 0)}, FloodWait: {res.get('flood_wait', 0)}"

                elif task_type == "parser":
                    targets_raw = form.get("parser_targets", "").strip()
                    targets = [t.strip() for t in targets_raw.split("\n") if t.strip()]
                    parser_mode = form.get("parser_mode", "active")
                    parser_limit = int(form.get("parser_limit", 500))

                    res = await parser.run_parser(
                        clients=clients,
                        targets=targets,
                        parser_mode=parser_mode,
                        limit=parser_limit,
                    )
                    parsed_users = res.get("users", [])
                    res_text = f"Парсер завершен. Собрано уникальных пользователей: {len(parsed_users)}. Успешно чатов: {res.get('success', 0)}"
                else:
                    res_text = f"Неизвестный тип задачи: {task_type}"

            except Exception as e:
                mapped_status, reason = classify_tg_error(e)
                if mapped_status:
                    for acc in active_accs:
                        set_tg_account_status(acc["id"], mapped_status, reason)
                log_err(f"Задача {task_type}: {e}")
                res_text = f"Ошибка выполнения задачи: {str(e)}"

            for client in clients:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    cursor.execute(
        "INSERT INTO client_tasks (client_key, task_type, target_link, result_text, created_at) VALUES (?, ?, ?, ?, ?)",
        (client_key, task_type, "Мульти-база", res_text, time.time()),
    )
    db.commit()
    db.close()

    log_ok(f"Задача {task_type} завершена: {res_text}")
    notify_user_sync(client_key, f"Задача ({task_type}): {res_text}")
    return RedirectResponse(url="/cabinet?message=Задача обработана!#spammer", status_code=303)


@app.get("/logout-client")
def logout_client():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(key=CLIENT_COOKIE_NAME, path="/")
    return resp


# =====================================================================
# TELEGRAM ACCOUNT VIEWER (READ-ONLY UI FOR CLIENT & ADMIN)
# =====================================================================
from datetime import datetime as _dt

_tg_viewer_locks: dict[int, asyncio.Lock] = {}
_tg_viewer_cache: dict[tuple, tuple[float, dict]] = {}
TG_VIEWER_CACHE_TTL = 25.0


def _get_tg_viewer_lock(acc_id: int) -> asyncio.Lock:
    lock = _tg_viewer_locks.get(acc_id)
    if lock is None:
        lock = asyncio.Lock()
        _tg_viewer_locks[acc_id] = lock
    return lock


def _format_tg_media_label(msg) -> str:
    if not getattr(msg, "media", None):
        return ""
    if getattr(msg, "photo", None):
        return "📷 Фото"
    if getattr(msg, "voice", None):
        return "🎤 Голосовое сообщение"
    if getattr(msg, "video_note", None):
        return "⭕ Видеосообщение"
    if getattr(msg, "video", None):
        return "🎬 Видео"
    if getattr(msg, "sticker", None):
        alt = ""
        try:
            alt = msg.file.emoji or ""
        except Exception:
            pass
        return f"💟 Стикер {alt}".strip()
    if getattr(msg, "gif", None):
        return "🎞 GIF"
    if getattr(msg, "document", None):
        fname = ""
        try:
            fname = msg.file.name or ""
        except Exception:
            pass
        return f"📎 Файл{(': ' + fname[:28]) if fname else ''}"
    if getattr(msg, "poll", None):
        return "📊 Опрос"
    if getattr(msg, "geo", None):
        return "📍 Геопозиция"
    return "📦 Вложение"


def _format_tg_timestamp(dt_obj) -> tuple[str, str]:
    if not dt_obj:
        return "", ""
    try:
        local_dt = dt_obj.astimezone()
        now_dt = _dt.now(local_dt.tzinfo)
        time_s = local_dt.strftime("%H:%M")
        if local_dt.date() == now_dt.date():
            date_s = "Сегодня"
        elif (now_dt.date() - local_dt.date()).days == 1:
            date_s = "Вчера"
        else:
            date_s = local_dt.strftime("%d.%m.%Y")
        return time_s, date_s
    except Exception:
        return "", ""


async def _fetch_tg_viewer_dialogs(acc_row: dict, force_refresh: bool = False) -> tuple[int, dict]:
    acc_id = int(acc_row["id"])
    cache_key = ("dialogs", acc_id)
    now = time.time()
    if not force_refresh and cache_key in _tg_viewer_cache:
        ts, cached_payload = _tg_viewer_cache[cache_key]
        if now - ts < TG_VIEWER_CACHE_TTL:
            return 200, cached_payload

    session_str = decrypt_secret(acc_row.get("session_string") or "")
    if not session_str:
        return 400, {"error": "Сессия аккаунта пуста или не авторизована"}

    lock = _get_tg_viewer_lock(acc_id)
    async with lock:
        if not force_refresh and cache_key in _tg_viewer_cache:
            ts, cached_payload = _tg_viewer_cache[cache_key]
            if time.time() - ts < TG_VIEWER_CACHE_TTL:
                return 200, cached_payload

        proxy_cfg = get_account_proxy_tuple(acc_row)
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH, proxy=proxy_cfg)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                set_tg_account_status(acc_id, ACCOUNT_STATUS_SESSION_LOST, "Сессия не авторизована")
                return 401, {"error": "Сессия аккаунта слетела или не авторизована"}

            me = await client.get_me()
            acc_title = acc_row.get("phone") or ""
            if me:
                full_n = " ".join(x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if x).strip()
                if full_n and acc_title:
                    acc_title = f"{full_n} ({acc_title})"
                elif full_n:
                    acc_title = full_n

            dialogs_out = []
            async for d in client.iter_dialogs(limit=40):
                ent = d.entity
                if getattr(d, "is_user", False):
                    ctype = "private"
                elif getattr(d, "is_channel", False) and not getattr(d, "is_group", False):
                    ctype = "channel"
                else:
                    ctype = "group"

                last_msg = d.message
                preview = ""
                time_str = ""
                if last_msg:
                    time_str, date_lbl = _format_tg_timestamp(getattr(last_msg, "date", None))
                    if date_lbl not in ("Сегодня", ""):
                        time_str = date_lbl
                    media_lbl = _format_tg_media_label(last_msg)
                    raw_txt = (getattr(last_msg, "message", "") or "").strip().replace("\n", " ")
                    if media_lbl and raw_txt:
                        preview = f"{media_lbl}: {raw_txt}"
                    elif media_lbl:
                        preview = media_lbl
                    else:
                        preview = raw_txt
                    if getattr(last_msg, "out", False) and preview:
                        preview = f"Вы: {preview}"

                dialogs_out.append({
                    "peer_id": int(d.id),
                    "title": d.name or "Без названия",
                    "username": getattr(ent, "username", "") or "",
                    "chat_type": ctype,
                    "unread_count": int(d.unread_count or 0),
                    "last_message": preview[:120],
                    "time_str": time_str,
                })

            payload = {
                "account_title": acc_title,
                "dialogs": dialogs_out,
            }
            _tg_viewer_cache[cache_key] = (time.time(), payload)
            return 200, payload
        except Exception as e:
            mapped_status, reason = classify_tg_error(e)
            if mapped_status:
                set_tg_account_status(acc_id, mapped_status, reason)
            return 502, {"error": f"Ошибка Telegram: {reason}"}
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


async def _fetch_tg_viewer_messages(acc_row: dict, peer_id: int, force_refresh: bool = False) -> tuple[int, dict]:
    acc_id = int(acc_row["id"])
    cache_key = ("messages", acc_id, int(peer_id))
    now = time.time()
    if not force_refresh and cache_key in _tg_viewer_cache:
        ts, cached_payload = _tg_viewer_cache[cache_key]
        if now - ts < TG_VIEWER_CACHE_TTL:
            return 200, cached_payload

    session_str = decrypt_secret(acc_row.get("session_string") or "")
    if not session_str:
        return 400, {"error": "Сессия аккаунта пуста или не авторизована"}

    lock = _get_tg_viewer_lock(acc_id)
    async with lock:
        if not force_refresh and cache_key in _tg_viewer_cache:
            ts, cached_payload = _tg_viewer_cache[cache_key]
            if time.time() - ts < TG_VIEWER_CACHE_TTL:
                return 200, cached_payload

        proxy_cfg = get_account_proxy_tuple(acc_row)
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH, proxy=proxy_cfg)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                set_tg_account_status(acc_id, ACCOUNT_STATUS_SESSION_LOST, "Сессия не авторизована")
                return 401, {"error": "Сессия аккаунта слетела или не авторизована"}

            entity = await client.get_entity(int(peer_id))
            uname = getattr(entity, "username", None)
            subtitle = f"@{uname}" if uname else "История переписки (последние 45)"

            raw_msgs = await client.get_messages(entity, limit=45)
            msgs_out = []
            for m in reversed(raw_msgs):
                if getattr(m, "action", None) and not getattr(m, "message", None):
                    continue
                t_str, d_lbl = _format_tg_timestamp(getattr(m, "date", None))
                sender_name = ""
                if not getattr(m, "out", False) and getattr(m, "sender", None):
                    s = m.sender
                    sender_name = " ".join(
                        x for x in [getattr(s, "first_name", ""), getattr(s, "last_name", "")] if x
                    ).strip() or getattr(s, "title", "") or ""
                msgs_out.append({
                    "id": int(m.id),
                    "out": bool(getattr(m, "out", False)),
                    "sender_name": sender_name,
                    "text": (getattr(m, "message", "") or ""),
                    "media_label": _format_tg_media_label(m),
                    "time_str": t_str,
                    "date_label": d_lbl,
                })

            payload = {
                "subtitle": subtitle,
                "messages": msgs_out,
            }
            _tg_viewer_cache[cache_key] = (time.time(), payload)
            return 200, payload
        except Exception as e:
            mapped_status, reason = classify_tg_error(e)
            if mapped_status:
                set_tg_account_status(acc_id, mapped_status, reason)
            return 502, {"error": f"Ошибка загрузки чата: {reason}"}
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


@app.get("/api/tg-viewer/{acc_id}/dialogs")
async def client_tg_viewer_dialogs(request: Request, acc_id: int, refresh: int = 0):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return JSONResponse({"error": "Не авторизован"}, status_code=401)
    db = get_db()
    row = db.execute(
        "SELECT * FROM client_tg_accounts WHERE id = ? AND client_key = ?",
        (acc_id, client_key),
    ).fetchone()
    db.close()
    if not row:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)
    code, payload = await _fetch_tg_viewer_dialogs(dict(row), force_refresh=bool(refresh))
    return JSONResponse(payload, status_code=code)


@app.get("/api/tg-viewer/{acc_id}/messages/{peer_id}")
async def client_tg_viewer_messages(request: Request, acc_id: int, peer_id: int, refresh: int = 0):
    client_key = get_session_key_from_request(request)
    if not get_authorized_client(client_key):
        return JSONResponse({"error": "Не авторизован"}, status_code=401)
    db = get_db()
    row = db.execute(
        "SELECT * FROM client_tg_accounts WHERE id = ? AND client_key = ?",
        (acc_id, client_key),
    ).fetchone()
    db.close()
    if not row:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)
    code, payload = await _fetch_tg_viewer_messages(dict(row), peer_id, force_refresh=bool(refresh))
    return JSONResponse(payload, status_code=code)


@app.get(f"{SECRET_PATH}/tg-viewer/{{acc_id}}/dialogs")
async def admin_tg_viewer_dialogs(request: Request, acc_id: int, refresh: int = 0):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Требуется доступ администратора"}, status_code=403)
    db = get_db()
    row = db.execute("SELECT * FROM client_tg_accounts WHERE id = ?", (acc_id,)).fetchone()
    db.close()
    if not row:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)
    code, payload = await _fetch_tg_viewer_dialogs(dict(row), force_refresh=bool(refresh))
    return JSONResponse(payload, status_code=code)


@app.get(f"{SECRET_PATH}/tg-viewer/{{acc_id}}/messages/{{peer_id}}")
async def admin_tg_viewer_messages(request: Request, acc_id: int, peer_id: int, refresh: int = 0):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Требуется доступ администратора"}, status_code=403)
    db = get_db()
    row = db.execute("SELECT * FROM client_tg_accounts WHERE id = ?", (acc_id,)).fetchone()
    db.close()
    if not row:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)
    code, payload = await _fetch_tg_viewer_messages(dict(row), peer_id, force_refresh=bool(refresh))
    return JSONResponse(payload, status_code=code)
