import os
import shutil
import asyncio
import qrcode
import io
import base64
import uuid
import time
from typing import List
from fastapi import FastAPI, Form, Request, Response, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
from telethon.sessions import StringSession

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
)
import admin

import spammer
import inviter
import parser

API_ID = 38977487
API_HASH = "a6afb062a4c36f6d7b2cb87d27385672"

os.makedirs("static/banners", exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

init_db()
app.include_router(admin.router)

qr_sessions = {}


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
    db.commit()
    db.close()


init_banners_table()


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
    if result:
        return result

    legacy = get_banner()
    if legacy and legacy.get("active") and (legacy.get("text") or legacy.get("image_url")):
        return [{
            "id": 0,
            "title": legacy.get("title") or "GHOSTCORE",
            "text": legacy.get("text") or "",
            "url": legacy.get("url") or "",
            "image_url": legacy.get("image_url") or "",
            "duration_sec": 8,
            "is_active": 1,
        }]
    return []


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


def save_uploaded_banner_image(image: UploadFile) -> str:
    if not image or not image.filename:
        return ""
    ext = os.path.splitext(image.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        return ""
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join("static", "banners", filename)
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
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


def get_authorized_client(client_key: str):
    if not client_key:
        return None
    db = get_db()
    row = db.execute(
        "SELECT id, key_code, client_name, status_text, status FROM access_keys WHERE key_code = ?",
        (client_key.strip().upper(),),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def is_admin_authenticated(request: Request) -> bool:
    cookie_names = [
        "admin_session",
        "admin_token",
        "is_admin",
        "admin_auth",
        "session",
    ]
    for name in cookie_names:
        val = request.cookies.get(name)
        if val and val.strip().lower() in ("1", "true", "yes", "admin", "ok"):
            return True
        if val and len(val.strip()) > 0 and name != "client_session":
            return True
    return False


@app.get("/api/home-data")
def api_home_data(request: Request):
    client_key = request.cookies.get("client_session")
    user = get_authorized_client(client_key)
    messages = get_recent_chat_messages(limit=50) if user else []
    banners = get_active_home_banners()
    first_banner = banners[0] if banners else None
    return JSONResponse({
        "banner": first_banner,
        "banners": banners,
        "messages": messages,
        "authenticated": bool(user),
    })


@app.get("/api/admin/banners")
def api_admin_banners(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return JSONResponse({"banners": get_all_home_banners()})


@app.post("/admin/banners/add")
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

    image_url = save_uploaded_banner_image(image)
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
            title.strip() or "GHOSTCORE",
            text.strip(),
            url.strip(),
            image_url,
            clean_duration,
            is_active,
            time.time(),
        ),
    )
    db.commit()
    db.close()

    return RedirectResponse(url="/admin?message=Баннер+добавлен", status_code=303)


@app.post("/admin/banners/toggle")
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
    return RedirectResponse(url="/admin?message=Статус+баннера+обновлён", status_code=303)


@app.post("/admin/banners/delete")
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
    return RedirectResponse(url="/admin?message=Баннер+удалён", status_code=303)


@app.get("/api/profile")
def api_get_profile(request: Request):
    client_key = request.cookies.get("client_session")
    profile = get_client_profile(client_key)
    if not profile:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({"authenticated": True, "profile": profile})


@app.post("/cabinet/update-profile")
async def update_profile_route(request: Request):
    client_key = request.cookies.get("client_session")
    profile = get_client_profile(client_key)
    if not profile:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    new_name = str(form.get("client_name", ""))
    new_status = str(form.get("status_text", ""))
    update_client_profile(client_key, new_name, new_status)
    return RedirectResponse(url="/cabinet?message=Профиль+сохранён#profile", status_code=303)


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    client_key = websocket.cookies.get("client_session")
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
            saved = add_chat_message(author=author, text=raw_text[:1000])
            await chat_manager.broadcast({
                "type": "message",
                "id": saved["id"],
                "author": saved["author"],
                "text": saved["text"],
                "created_at": saved["created_at"],
            })
    except WebSocketDisconnect:
        chat_manager.disconnect(websocket)
    except Exception:
        chat_manager.disconnect(websocket)


@app.post("/admin/banner/update")
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
    new_image_url = save_uploaded_banner_image(image) if (image and image.filename) else None

    if new_image_url:
        current_banner = get_banner()
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
        title=title.strip() or "Промо",
        text=text.strip(),
        url=url.strip(),
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
                title.strip() or "Промо",
                text.strip(),
                url.strip(),
                new_image_url or "",
                clean_duration,
                time.time(),
            ),
        )
        db.commit()
        db.close()

    return RedirectResponse(url="/admin?message=Баннер+обновлён", status_code=303)


@app.post("/admin/banner/clear-image")
def admin_clear_banner_image(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    current_banner = get_banner()
    old_img = current_banner.get("image_url", "")
    if old_img.startswith("/static/banners/"):
        old_path = os.path.join("static", "banners", os.path.basename(old_img))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    update_banner_db(
        is_active=current_banner["active"],
        title=current_banner["title"],
        text=current_banner["text"],
        url=current_banner["url"],
        image_url="",
    )
    return RedirectResponse(url="/admin?message=Картинка+удалена", status_code=303)


@app.post("/admin/chat/clear")
async def admin_clear_chat(request: Request):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    clear_chat_messages_db()
    await chat_manager.broadcast({"type": "clear"})
    return RedirectResponse(url="/admin?message=Чат+очищен", status_code=303)


@app.post("/admin/chat/delete")
async def admin_delete_chat_message(request: Request, msg_id: int = Form(...)):
    if not is_admin_authenticated(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    delete_chat_message_db(msg_id)
    await chat_manager.broadcast({"type": "delete", "id": msg_id})
    return RedirectResponse(url="/admin?message=Сообщение+удалено", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home_page(request: Request, error: str = None):
    err_html = f'<div class="err-box">{error}</div>' if error else ""
    client_key = request.cookies.get("client_session")
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

        function appendMessage(id, author, text) {
            if (!chatBox) return;
            const item = document.createElement("div");
            item.className = "tx-item";
            if (id !== undefined && id !== null) item.dataset.msgId = String(id);

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
                startBannerRotation(Array.isArray(data.banners) ? data.banners : (data.banner ? [data.banner] : []));
                if (data.authenticated && chatBox && Array.isArray(data.messages)) {
                    chatBox.innerHTML = "";
                    data.messages.forEach(m => appendMessage(m.id, m.author, m.text));
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
                if (payload.type === "clear") { if (chatBox) chatBox.innerHTML = ""; return; }
                if (payload.type === "delete") {
                    const target = chatBox ? chatBox.querySelector(`[data-msg-id="${payload.id}"]`) : null;
                    if (target) target.remove();
                    return;
                }
                appendMessage(payload.id, payload.author, payload.text);
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
                startBannerRotation(Array.isArray(data.banners) ? data.banners : (data.banner ? [data.banner] : []));
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
            <div class="drawer-cap">Система</div>
            <a href="/admin" class="drawer-link drawer-dim">Админ-панель</a>
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

            {chat_script}
        </script>
    </body>
    </html>
    """


@app.post("/register", response_class=HTMLResponse)
def register_client(response: Response, client_name: str = Form(...)):
    clean_name = client_name.strip()
    new_key = str(uuid.uuid4())[:8].upper()

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO access_keys (key_code, status, client_name) VALUES (?, 'active', ?)",
        (new_key, clean_name),
    )
    db.commit()
    db.close()

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

    return FileResponse("index.html")


@app.get("/cabinet/start-qr", response_class=HTMLResponse)
async def start_qr_auth(request: Request):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    try:
        qr = await client.qr_login()
        session_id = uuid.uuid4().hex
        qr_sessions[session_id] = {"client": client, "qr": qr}

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
            <meta http-equiv="refresh" content="5">
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
    client_key = request.cookies.get("client_session")
    if not client_key or sid not in qr_sessions:
        return RedirectResponse(url="/cabinet", status_code=303)

    data = qr_sessions[sid]
    client = data["client"]
    qr = data["qr"]

    try:
        await qr.wait(timeout=3)
        me = await client.get_me()
        phone_str = f"+{me.phone}" if me and me.phone else "QR-Аккаунт"

        final_session_str = client.session.save()
        await client.disconnect()
        del qr_sessions[sid]

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO client_tg_accounts (client_key, phone, session_string, status) VALUES (?, ?, ?, 'Активен')",
            (client_key, phone_str, final_session_str),
        )
        db.commit()
        db.close()

        return RedirectResponse(url="/cabinet?message=QR-код успешно сканирован!", status_code=303)
    except asyncio.TimeoutError:
        return RedirectResponse(url="/cabinet/start-qr", status_code=303)
    except Exception as e:
        del qr_sessions[sid]
        return RedirectResponse(url=f"/cabinet?message=Ошибка QR: {str(e)}", status_code=303)


@app.post("/cabinet/add-account")
async def add_client_account(request: Request, phone: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)

    clean_phone = phone.strip()
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    try:
        sent = await client.send_code_request(clean_phone)
        phone_code_hash = sent.phone_code_hash
        session_str = client.session.save()
        await client.disconnect()

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO client_tg_accounts (client_key, phone, session_string, phone_code_hash, status) VALUES (?, ?, ?, ?, 'Ожидает код')",
            (client_key, clean_phone, session_str, phone_code_hash),
        )
        db.commit()
        db.close()

        return RedirectResponse(url="/cabinet?message=Код отправлен!", status_code=303)
    except Exception as e:
        await client.disconnect()
        return RedirectResponse(url=f"/cabinet?message=Ошибка запроса кода: {str(e)}", status_code=303)


@app.post("/cabinet/verify-code")
async def verify_client_code(request: Request, acc_id: int = Form(...), code: str = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM client_tg_accounts WHERE id = ? AND client_key = ?", (acc_id, client_key))
    acc = cursor.fetchone()
    db.close()

    if not acc:
        return RedirectResponse(url="/cabinet?message=Аккаунт не найден", status_code=303)

    client = TelegramClient(StringSession(acc["session_string"]), API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(phone=acc["phone"], code=code.strip(), phone_code_hash=acc["phone_code_hash"])
        final_session_str = client.session.save()
        await client.disconnect()

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE client_tg_accounts SET session_string = ?, status = 'Активен' WHERE id = ?",
            (final_session_str, acc_id),
        )
        db.commit()
        db.close()

        return RedirectResponse(url="/cabinet?message=Аккаунт успешно авторизован!", status_code=303)
    except Exception as e:
        await client.disconnect()
        return RedirectResponse(url=f"/cabinet?message=Ошибка авторизации: {str(e)}", status_code=303)


@app.post("/cabinet/delete-account")
def delete_client_account(request: Request, acc_id: int = Form(...)):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM client_tg_accounts WHERE id = ? AND client_key = ?", (acc_id, client_key))
    db.commit()
    db.close()

    return RedirectResponse(url="/cabinet?message=Аккаунт удален", status_code=303)


@app.post("/cabinet/run-task")
async def run_client_task(request: Request):
    client_key = request.cookies.get("client_session")
    if not client_key:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    task_type = form.get("task_type")

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM client_tg_accounts WHERE client_key = ? AND status = 'Активен'", (client_key,))
    active_accs = cursor.fetchall()

    if not active_accs:
        res_text = "Ошибка: Нет активных авторизованных аккаунтов!"
    else:
        clients = []
        for acc in active_accs:
            try:
                client = TelegramClient(StringSession(acc["session_string"]), API_ID, API_HASH)
                await client.connect()
                if await client.is_user_authorized():
                    clients.append(client)
            except Exception as e:
                print(f"⚠️ Ошибка инициализации клиента {acc['phone']}: {e}")

        if not clients:
            res_text = "Ошибка: Ни один из сохраненных аккаунтов не авторизован в Telegram!"
        else:
            try:
                if task_type == "spammer":
                    targets_raw = form.get("spam_targets", "").strip()
                    targets = [t.strip() for t in targets_raw.split("\n") if t.strip()]
                    target_type = form.get("target_type", "chats")
                    spam_mode = form.get("spam_mode", "text")
                    spam_order = form.get("spam_order", "sequential")
                    delay_sec = int(form.get("spam_delay", 5))
                    limit_per_account = int(form.get("spam_limit", 20))

                    texts = form.getlist("spam_texts[]") if spam_mode == "text" else None
                    forward_links = form.getlist("spam_links[]") if spam_mode == "forward" else None

                    res = await spammer.run_spammer(
                        clients=clients,
                        targets=targets,
                        target_type=target_type,
                        spam_mode=spam_mode,
                        spam_order=spam_order,
                        texts=texts,
                        forward_links=forward_links,
                        limit_per_account=limit_per_account,
                        delay_sec=delay_sec,
                    )
                    res_text = f"Спамер завершен. Успешно: {res.get('success', 0)}, Ошибок: {res.get('failed', 0)}, FloodWait: {res.get('flood_wait', 0)}"

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
                res_text = f"Ошибка выполнения задачи: {str(e)}"

            for client in clients:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    cursor.execute(
        "INSERT INTO client_tasks (client_key, task_type, target_link, result_text) VALUES (?, ?, ?, ?)",
        (client_key, task_type, "Мульти-база", res_text),
    )
    db.commit()
    db.close()

    return RedirectResponse(url="/cabinet?message=Задача обработана!", status_code=303)


@app.get("/logout-client")
def logout_client():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(key="client_session")
    return resp
