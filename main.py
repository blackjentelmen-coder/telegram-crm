import secrets
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Telegram CRM Lite")

DB_FILE = "crm.db"
KEY_FILE = "access_key.txt"


def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            access_key TEXT UNIQUE NOT NULL,
            tier TEXT DEFAULT 'light'
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'active'
        )
    """)
  conn.commit()

  # Автоматическая генерация случайного уникального ключа
  cursor.execute("SELECT access_key FROM access_keys LIMIT 1")
  existing = cursor.fetchone()

  if not existing:
    while True:
      new_key = secrets.token_hex(16)
      try:
        cursor.execute(
            "INSERT INTO access_keys (access_key, tier) VALUES (?, ?)",
            (new_key, "light"),
        )
        conn.commit()
        # Сохраняем ключ в файл, чтобы его можно было забрать с телефона через GitHub
        with open(KEY_FILE, "w") as f:
          f.write(new_key)
        break
      except sqlite3.IntegrityError:
        continue
  else:
    # Если ключ уже был в базе, но файла нет — создаем файл с текущим ключом
    try:
      with open(KEY_FILE, "w") as f:
        f.write(existing[0])
    except Exception:
      pass

  conn.close()


@app.on_event("startup")
def startup_event():
  init_db()


class AuthRequest(BaseModel):
  access_key: str


class AccountRequest(BaseModel):
  phone: str
  access_key: str


@app.post("/auth/verify")
def verify_key(data: AuthRequest):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT tier FROM access_keys WHERE access_key = ?", (data.access_key,)
  )
  row = cursor.fetchone()
  conn.close()

  if not row:
    raise HTTPException(status_code=403, detail="Неверный ключ доступа")
  return {"success": True, "tier": row[0]}


@app.post("/accounts/add")
def add_account(data: AccountRequest):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()

  cursor.execute(
      "SELECT 1 FROM access_keys WHERE access_key = ?", (data.access_key,)
  )
  if not cursor.fetchone():
    conn.close()
    raise HTTPException(status_code=403, detail="Неверный ключ доступа")

  try:
    cursor.execute(
        "INSERT INTO telegram_accounts (phone) VALUES (?)", (data.phone,)
    )
    conn.commit()
  except sqlite3.IntegrityError:
    conn.close()
    return {"success": True, "message": "Этот номер уже привязан"}

  conn.close()
  return {"success": True, "message": f"Аккаунт {data.phone} успешно добавлен"}


@app.get("/", response_class=HTMLResponse)
def mobile_ui():
  return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram CRM Lite</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 16px; margin: 0; }
            .card { background: #1e293b; padding: 20px; border-radius: 12px; margin-bottom: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
            h2, h3 { margin-top: 0; color: #38bdf8; }
            input, button { width: 100%; padding: 14px; margin-top: 10px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #fff; font-size: 16px; box-sizing: border-box; outline: none; }
            input:focus { border-color: #38bdf8; }
            button { background: #3b82f6; border: none; font-weight: bold; cursor: pointer; }
            button:active { background: #2563eb; }
            .success { color: #4ade80; margin-top: 10px; font-size: 14px; }
            .error { color: #f87171; margin-top: 10px; font-size: 14px; }
        </style>
    </head>
    <body>
        <h2>Telegram CRM</h2>
        
        <div class="card" id="auth-card">
            <h3>Авторизация</h3>
            <input type="password" id="accessKey" placeholder="Введите ключ доступа">
            <button onclick="saveKey()">Войти</button>
            <div id="auth-status"></div>
        </div>

        <div class="card" id="app-card" style="display:none;">
            <h3>Привязка аккаунта</h3>
            <input type="text" id="phone" placeholder="Номер (+380...)">
            <button onclick="addAccount()">Добавить аккаунт</button>
            <div id="acc-status"></div>
        </div>

        <script>
            document.addEventListener("DOMContentLoaded", () => {
                const savedKey = localStorage.getItem("crm_key");
                if (savedKey) {
                    document.getElementById("accessKey").value = savedKey;
                    verifyKeySilently(savedKey);
                }
            });

            function saveKey() {
                const key = document.getElementById("accessKey").value.trim();
                if (!key) return;
                fetch('/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key})
                }).then(res => res.json()).then(data => {
                    if (data.success) {
                        localStorage.setItem("crm_key", key);
                        document.getElementById("auth-status").innerHTML = '<div class="success">Успешно! Панель разблокирована.</div>';
                        document.getElementById("app-card").style.display = 'block';
                    } else {
                        document.getElementById("auth-status").innerHTML = '<div class="error">Неверный ключ доступа</div>';
                    }
                }).catch(() => {
                    document.getElementById("auth-status").innerHTML = '<div class="error">Ошибка связи с сервером</div>';
                });
            }

            function verifyKeySilently(key) {
                fetch('/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key})
                }).then(res => res.json()).then(data => {
                    if (data.success) {
                        document.getElementById("app-card").style.display = 'block';
                    }
                });
            }

            function addAccount() {
                const key = localStorage.getItem("crm_key");
                const phone = document.getElementById("phone").value.trim();
                if (!phone) return;
                fetch('/accounts/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key, phone: phone})
                }).then(res => res.json()).then(data => {
                    if (data.success) {
                        document.getElementById("acc-status").innerHTML = `<div class="success">${data.message}</div>`;
                        document.getElementById("phone").value = '';
                    } else {
                        document.getElementById("acc-status").innerHTML = `<div class="error">Ошибка добавления</div>`;
                    }
                });
            }
        </script>
    </body>
    </html>
    """
