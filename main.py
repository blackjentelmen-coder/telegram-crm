import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Telegram CRM Full Lite")
DB_FILE = "crm.db"


def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()

  # 1. Таблица ключей доступа
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            access_key TEXT UNIQUE NOT NULL,
            tier TEXT DEFAULT 'light'
        )
    """)

  # 2. Таблица привязанных Telegram-аккаунтов
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  # 3. Таблица задач / рассылок
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        )
    """)

  conn.commit()
  conn.close()


@app.on_event("startup")
def startup_event():
  init_db()


# Схемы запросов
class KeyRequest(BaseModel):
  access_key: str


class AccountRequest(BaseModel):
  phone: str
  access_key: str


class TaskRequest(BaseModel):
  title: str
  message: str
  access_key: str


# Эндпоинты проверки и настройки
@app.get("/api/status")
def get_status():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("SELECT COUNT(*) FROM access_keys")
  count = cursor.fetchone()[0]
  conn.close()
  return {"has_keys": count > 0}


@app.post("/api/setup")
def setup_key(data: KeyRequest):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("SELECT COUNT(*) FROM access_keys")
  if cursor.fetchone()[0] > 0:
    conn.close()
    raise HTTPException(status_code=400, detail="Ключ уже настроен")
  if not data.access_key.strip():
    conn.close()
    raise HTTPException(status_code=400, detail="Ключ не может быть пустым")
  try:
    cursor.execute(
        "INSERT INTO access_keys (access_key, tier) VALUES (?, ?)",
        (data.access_key.strip(), "light"),
    )
    conn.commit()
  except sqlite3.IntegrityError:
    conn.close()
    raise HTTPException(status_code=400, detail="Такой ключ уже существует")
  conn.close()
  return {"success": True}


@app.post("/auth/verify")
def verify_key(data: KeyRequest):
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


# Управление аккаунтами
@app.get("/accounts/list")
def list_accounts(access_key: str):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT 1 FROM access_keys WHERE access_key = ?", (access_key,)
  )
  if not cursor.fetchone():
    conn.close()
    raise HTTPException(status_code=403, detail="Неверный ключ")

  cursor.execute("SELECT id, phone, status, created_at FROM telegram_accounts")
  rows = cursor.fetchall()
  conn.close()
  return [
      {"id": r[0], "phone": r[1], "status": r[2], "created_at": r[3]}
      for r in rows
  ]


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
    return {"success": False, "message": "Этот номер уже привязан"}
  conn.close()
  return {"success": True, "message": f"Аккаунт {data.phone} добавлен"}


@app.post("/accounts/delete")
def delete_account(account_id: int, access_key: str):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT 1 FROM access_keys WHERE access_key = ?", (access_key,)
  )
  if not cursor.fetchone():
    conn.close()
    raise HTTPException(status_code=403, detail="Неверный ключ")
  cursor.execute("DELETE FROM telegram_accounts WHERE id = ?", (account_id,))
  conn.commit()
  conn.close()
  return {"success": True}


# Управление задачами / рассылками
@app.get("/tasks/list")
def list_tasks(access_key: str):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT 1 FROM access_keys WHERE access_key = ?", (access_key,)
  )
  if not cursor.fetchone():
    conn.close()
    raise HTTPException(status_code=403, detail="Неверный ключ")

  cursor.execute("SELECT id, title, message, status FROM tasks")
  rows = cursor.fetchall()
  conn.close()
  return [
      {"id": r[0], "title": r[1], "message": r[2], "status": r[3]} for r in rows
  ]


@app.post("/tasks/add")
def add_task(data: TaskRequest):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT 1 FROM access_keys WHERE access_key = ?", (data.access_key,)
  )
  if not cursor.fetchone():
    conn.close()
    raise HTTPException(status_code=403, detail="Неверный ключ")
  cursor.execute(
      "INSERT INTO tasks (title, message) VALUES (?, ?)",
      (data.title, data.message),
  )
  conn.commit()
  conn.close()
  return {"success": True, "message": "Задача создана"}


# Мобильный интерфейс (скелет без стилей, только функционал)
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
            body { font-family: monospace; background: #111; color: #eee; padding: 10px; margin: 0; }
            .card { background: #222; border: 1px solid #441; padding: 12px; margin-bottom: 10px; }
            input, button, textarea { width: 100%; padding: 10px; margin-top: 5px; background: #000; color: #0f0; border: 1px solid #555; box-sizing: border-box; }
            button { background: #224; color: #fff; cursor: pointer; }
            hr { border-color: #444; }
            .error { color: #f55; }
            .success { color: #5f5; }
        </style>
    </head>
    <body>
        <h2>TG CRM (Functional Core)</h2>

        <!-- Настройка -->
        <div class="card" id="setup-card" style="display:none;">
            <h3>1. Первичная настройка</h3>
            <input type="text" id="masterKeyInput" placeholder="Придумайте мастер-ключ">
            <button onclick="doSetup()">Установить ключ</button>
            <div id="setup-msg"></div>
        </div>

        <!-- Вход -->
        <div class="card" id="auth-card" style="display:none;">
            <h3>Авторизация</h3>
            <input type="password" id="authKeyInput" placeholder="Введите мастер-ключ">
            <button onclick="doAuth()">Войти</button>
            <div id="auth-msg"></div>
        </div>

        <!-- Основная панель -->
        <div id="app-content" style="display:none;">
            <div class="card">
                <h3>Управление аккаунтами</h3>
                <input type="text" id="phoneInput" placeholder="Телефон (+380...)">
                <button onclick="addAccount()">Добавить аккаунт</button>
                <div id="acc-msg"></div>
                <hr>
                <div id="accountsList">Загрузка аккаунтов...</div>
            </div>

            <div class="card">
                <h3>Задачи / Рассылки</h3>
                <input type="text" id="taskTitle" placeholder="Название задачи">
                <textarea id="taskMsg" placeholder="Текст сообщения"></textarea>
                <button onclick="addTask()">Создать задачу</button>
                <div id="task-msg"></div>
                <hr>
                <div id="tasksList">Загрузка задач...</div>
            </div>
        </div>

        <script>
            document.addEventListener("DOMContentLoaded", () => {
                fetch('/api/status').then(res => res.json()).then(data => {
                    if (!data.has_keys) {
                        document.getElementById("setup-card").style.display = 'block';
                    } else {
                        const saved = localStorage.getItem("crm_key");
                        if (saved) {
                            verifyAndOpen(saved);
                        } else {
                            document.getElementById("auth-card").style.display = 'block';
                        }
                    }
                });
            });

            function doSetup() {
                const key = document.getElementById("masterKeyInput").value.trim();
                if (!key) return;
                fetch('/api/setup', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key})
                }).then(res => res.json()).then(data => {
                    if (data.success) {
                        localStorage.setItem("crm_key", key);
                        location.reload();
                    } else {
                        document.getElementById("setup-msg").innerHTML = '<span class="error">Ошибка</span>';
                    }
                });
            }

            function doAuth() {
                const key = document.getElementById("authKeyInput").value.trim();
                if (!key) return;
                verifyAndOpen(key);
            }

            function verifyAndOpen(key) {
                fetch('/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key})
                }).then(res => res.json()).then(data => {
                    if (data.success) {
                        localStorage.setItem("crm_key", key);
                        document.getElementById("setup-card").style.display = 'none';
                        document.getElementById("auth-card").style.display = 'none';
                        document.getElementById("app-content").style.display = 'block';
                        loadData();
                    } else {
                        document.getElementById("auth-msg").innerHTML = '<span class="error">Неверный ключ</span>';
                        document.getElementById("auth-card").style.display = 'block';
                    }
                });
            }

            function loadData() {
                const key = localStorage.getItem("crm_key");
                
                // Загрузка аккаунтов
                fetch('/accounts/list?access_key=' + encodeURIComponent(key))
                .then(res => res.json()).then(accounts => {
                    let html = '';
                    accounts.forEach(acc => {
                        html += `<div>📱 ${acc.phone} [${acc.status}] <button onclick="delAcc(${acc.id})" style="width:auto;padding:2px 6px;">X</button></div>`;
                    });
                    document.getElementById("accountsList").innerHTML = html || 'Нет аккаунтов';
                });

                // Загрузка задач
                fetch('/tasks/list?access_key=' + encodeURIComponent(key))
                .then(res => res.json()).then(tasks => {
                    let html = '';
                    tasks.forEach(t => {
                        html += `<div>📌 <b>${t.title}</b>: ${t.message} (${t.status})</div>`;
                    });
                    document.getElementById("tasksList").innerHTML = html || 'Нет задач';
                });
            }

            function addAccount() {
                const key = localStorage.getItem("crm_key");
                const phone = document.getElementById("phoneInput").value.trim();
                if (!phone) return;
                fetch('/accounts/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key, phone: phone})
                }).then(res => res.json()).then(data => {
                    document.getElementById("acc-msg").innerHTML = `<span class="${data.success ? 'success':'error'}">${data.message}</span>`;
                    if (data.success) { document.getElementById("phoneInput").value = ''; loadData(); }
                });
            }

            function delAcc(id) {
                const key = localStorage.getItem("crm_key");
                fetch(`/accounts/delete?account_id=${id}&access_key=` + encodeURIComponent(key), {method: 'POST'})
                .then(() => loadData());
            }

            function addTask() {
                const key = localStorage.getItem("crm_key");
                const title = document.getElementById("taskTitle").value.trim();
                const message = document.getElementById("taskMsg").value.trim();
                if (!title || !message) return;
                fetch('/tasks/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({access_key: key, title: title, message: message})
                }).then(res => res.json()).then(data => {
                    document.getElementById("task-msg").innerHTML = `<span class="success">${data.message}</span>`;
                    document.getElementById("taskTitle").value = '';
                    document.getElementById("taskMsg").value = '';
                    loadData();
                });
            }
        </script>
    </body>
    </html>
    """
