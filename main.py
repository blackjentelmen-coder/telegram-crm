import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Импортируем роутер авторизации и наш парсер
from api.telethon_auth import router as auth_router
from parser import TelegramParser

app = FastAPI(title="Telegram CRM", version="1.0")

# Подключаем роутер авторизации
app.include_router(auth_router)

DB_FILE = "crm_database.db"

class Registerkey(BaseModel):
    access_key: str
    tier: str = "light"

class AccountAdd(BaseModel):
    phone: str
    proxy: str = ""

class ParseRequest(BaseModel):
    account_phone: str      # Телефон аккаунта, с которого запускаем парсинг
    target: str             # Ссылка на чат, канал или конкретное сообщение
    limit_messages: Optional[int] = 5000 # Глубина истории (5000, 10000, 20000)

# Функция проверки ключа и получения ID пользователя
def get_current_user_id(x_access_key: str = Header(...)) -> int:
    if len(x_access_key) < 12:
        raise HTTPException(status_code=400, detail="Ключ должен содержать минимум 12 символов.")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE access_key = ?", (x_access_key,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=403, detail="Неверный ключ доступа.")
    return user[0]

# --- ГЛАВНАЯ СТРАНИЦА (WEB-ИНТЕРФЕЙС) ---
@app.get("/", response_class=HTMLResponse)
async def admin_panel():
    return """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram CRM</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen p-4">
    <div class="max-w-md mx-auto space-y-6">
        <!-- Шапка -->
        <header class="bg-gray-800 p-4 rounded-xl shadow-lg border border-gray-700">
            <h1 class="text-xl font-bold text-blue-400">Telegram CRM</h1>
            <p class="text-xs text-gray-400 mt-1">Мультиаккаунтная система управления</p>
        </header>

        <!-- Настройка ключа -->
        <section class="bg-gray-800 p-4 rounded-xl shadow-lg border border-gray-700 space-y-3">
            <h2 class="text-sm font-semibold text-gray-300">Ключ доступа (Access Key)</h2>
            <div class="flex gap-2">
                <input type="password" id="accessKey" placeholder="Введите ваш ключ" 
                    class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500">
                <button onclick="saveKey()" class="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition">Сохранить</button>
            </div>
        </section>

        <!-- Аккаунты -->
        <section class="bg-gray-800 p-4 rounded-xl shadow-lg border border-gray-700 space-y-3">
            <div class="flex justify-between items-center">
                <h2 class="text-sm font-semibold text-gray-300">Аккаунты Telegram</h2>
                <button onclick="loadAccounts()" class="text-xs text-blue-400 hover:underline">Обновить</button>
            </div>
            <div id="accountsList" class="space-y-2 text-sm text-gray-400">
                Нажмите «Обновить» или сохраните ключ...
            </div>
            
            <!-- Добавить аккаунт -->
            <div class="border-t border-gray-700 pt-3 mt-3 space-y-2">
                <h3 class="text-xs font-medium text-gray-400">Привязать аккаунт</h3>
                <input type="text" id="accPhone" placeholder="Телефон (+...)" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm">
                <input type="text" id="accProxy" placeholder="Прокси (необязательно)" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm">
                <button onclick="addAccount()" class="w-full bg-green-600 hover:bg-green-500 text-white py-2 rounded-lg text-sm font-medium transition">Добавить аккаунт</button>
            </div>
        </section>

        <!-- Парсер -->
        <section class="bg-gray-800 p-4 rounded-xl shadow-lg border border-gray-700 space-y-3">
            <h2 class="text-sm font-semibold text-gray-300">Запуск парсера</h2>
            <input type="text" id="parsePhone" placeholder="Телефон аккаунта-исполнителя" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm">
            <input type="text" id="parseTarget" placeholder="Ссылка на чат / канал (@chat)" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm">
            <input type="number" id="parseLimit" placeholder="Лимит сообщений (например, 5000)" value="5000" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm">
            <button onclick="runParser()" class="w-full bg-purple-600 hover:bg-purple-500 text-white py-2 rounded-lg text-sm font-medium transition">Запустить парсинг</button>
            <div id="parseResult" class="text-xs text-gray-300 bg-gray-900 p-2 rounded-lg hidden"></div>
        </section>
    </div>

    <script>
        document.getElementById('accessKey').value = localStorage.getItem('crm_key') || '';

        function saveKey() {
            const key = document.getElementById('accessKey').value;
            localStorage.setItem('crm_key', key);
            alert('Ключ сохранен!');
            loadAccounts();
        }

        async function loadAccounts() {
            const key = localStorage.getItem('crm_key');
            if (!key) return alert('Введите ключ доступа!');
            
            try {
                const res = await fetch('/accounts', {
                    headers: { 'x-access-key': key }
                });
                const data = await res.json();
                const list = document.getElementById('accountsList');
                if (data.accounts && data.accounts.length > 0) {
                    list.innerHTML = data.accounts.map(acc => `
                        <div class="bg-gray-900 p-2.5 rounded-lg border border-gray-700 flex justify-between items-center text-xs">
                            <div>
                                <div class="font-medium text-gray-200">${acc.phone}</div>
                                <div class="text-gray-500">Статус: ${acc.status}</div>
                            </div>
                        </div>
                    `).join('');
                } else {
                    list.innerHTML = '<p class="text-xs text-gray-500">Нет привязанных аккаунтов</p>';
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function addAccount() {
            const key = localStorage.getItem('crm_key');
            const phone = document.getElementById('accPhone').value;
            const proxy = document.getElementById('accProxy').value;
            
            const res = await fetch('/accounts/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'x-access-key': key },
                body: JSON.stringify({ phone, proxy })
            });
            const data = await res.json();
            alert(data.message || 'Готово');
            loadAccounts();
        }

        async function runParser() {
            const key = localStorage.getItem('crm_key');
            const account_phone = document.getElementById('parsePhone').value;
            const target = document.getElementById('parseTarget').value;
            const limit_messages = parseInt(document.getElementById('parseLimit').value);

            const resDiv = document.getElementById('parseResult');
            resDiv.classList.remove('hidden');
            resDiv.innerText = 'Запуск парсера...';

            try {
                const res = await fetch('/parser/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'x-access-key': key },
                    body: JSON.stringify({ account_phone, target, limit_messages })
                });
                const data = await res.json();
                resDiv.innerText = JSON.stringify(data, null, 2);
            } catch (e) {
                resDiv.innerText = 'Ошибка выполнения парсинга';
            }
        }

        if(localStorage.getItem('crm_key')) {
            loadAccounts();
        }
    </script>
</body>
</html>
    """

@app.post("/auth/register")
def register_key(data: Registerkey):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (access_key, tier) VALUES (?, ?)", (data.access_key, data.tier))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Ключ зарегистрирован."}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Ключ уже существует.")

@app.get("/accounts")
def get_accounts(user_id: int = Depends(get_current_user_id)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone, proxy, status FROM accounts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {"accounts": [{"id": r[0], "phone": r[1], "proxy": r[2], "status": r[3]} for r in rows]}

@app.post("/accounts/add")
def add_account(data: AccountAdd, user_id: int = Depends(get_current_user_id)):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO accounts (user_id, phone, proxy, status) VALUES (?, ?, ?, ?)",
            (user_id, data.phone, data.proxy, "disconnected")
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": f"Аккаунт {data.phone} привязан к вашему профилю."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

@app.post("/parser/run")
async def run_parser(data: ParseRequest, user_id: int = Depends(get_current_user_id)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT session_string FROM accounts WHERE user_id = ? AND phone = ? AND status = 'active'",
        (user_id, data.account_phone)
    )
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        raise HTTPException(
            status_code=400,
            detail=f"Аккаунт {data.account_phone} не найден или не имеет активной сессии."
        )

    session_string = row[0]
    
    try:
        parser = TelegramParser(session_string=session_string)
        result = await parser.parse_chat_members(
            target=data.target,
            user_id=user_id,
            limit_messages=data.limit_messages
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))

        return {
            "success": True,
            "message": f"Успешно собрано уникальных пользователей: {result['count']}"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
