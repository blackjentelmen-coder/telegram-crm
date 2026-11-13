import os
import sqlite3
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import ChatAdminRequiredError, ChannelPrivateError

API_ID = 38977487
API_HASH = "a6afb062a4c36f6d7b2cb87d27385672"
DB_FILE = "crm_database.db"

class TelegramParser:
    def __init__(self, session_string: str = None):
        self.session = StringSession(session_string) if session_string else StringSession()
        self.client = TelegramClient(self.session, API_ID, API_HASH)

    async def connect(self):
        if not self.client.is_connected():
            await self.client.connect()

    async def disconnect(self):
        if self.client.is_connected():
            await self.client.disconnect()

    async def parse_chat_members(self, target: str, user_id: int, limit_messages: int = 5000):
        """
        Универсальный парсер:
        - target: ссылка на чат, канал или сообщение.
        - limit_messages: глубина анализа истории сообщений, если список участников скрыт (5000, 10000, 20000).
        """
        await self.connect()
        parsed_users = {}

        try:
            # 1. Проверяем, является ли ссылка ссылкой на конкретное сообщение
            if "/c/" in target or (target.count("/") >= 4 and "+" not in target):
                parts = target.split("/")
                post_id = int(parts[-1])
                chat_identifier = parts[-2] if "c" not in parts else int(f"-100{parts[parts.index('c') + 1]}")
                
                message = await self.client.get_message(chat_identifier, post_id)
                if message and message.sender:
                    user = message.sender
                    parsed_users[user.id] = {
                        "id": user.id,
                        "username": getattr(user, "username", None),
                        "first_name": getattr(user, "first_name", ""),
                        "phone": getattr(user, "phone", None)
                    }
                
                # Собираем комментарии к посту
                try:
                    async for comment in self.client.iter_messages(chat_identifier, reply_to=post_id):
                        if comment.sender and not comment.sender.bot:
                            u = comment.sender
                            parsed_users[u.id] = {
                                "id": u.id,
                                "username": getattr(u, "username", None),
                                "first_name": getattr(u, "first_name", ""),
                                "phone": getattr(u, "phone", None)
                            }
                except Exception:
                    pass

            else:
                # 2. Обычный чат / канал (публичный или закрытый)
                entity = await self.client.get_entity(target)
                
                # Пытаемся собрать участников напрямую
                try:
                    async for user in self.client.iter_participants(entity):
                        if not user.bot:
                            parsed_users[user.id] = {
                                "id": user.id,
                                "username": getattr(user, "username", None),
                                "first_name": getattr(user, "first_name", ""),
                                "phone": getattr(user, "phone", None)
                            }
                except (ChatAdminRequiredError, ChannelPrivateError, Exception):
                    # Если список участников скрыт, переключаемся на парсинг по истории сообщений
                    async for message in self.client.iter_messages(entity, limit=limit_messages):
                        if message.sender and not message.sender.bot:
                            user = message.sender
                            if user.id not in parsed_users:
                                parsed_users[user.id] = {
                                    "id": user.id,
                                    "username": getattr(user, "username", None),
                                    "first_name": getattr(user, "first_name", ""),
                                    "phone": getattr(user, "phone", None)
                                }

            users_list = list(parsed_users.values())
            self.save_to_db(users_list, user_id)
            return {"success": True, "count": len(users_list), "users": users_list}

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await self.disconnect()

    def save_to_db(self, users_list, user_id: int):
        """Сохранение спарсенных пользователей в локальную БД"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parsed_audience (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tg_id TEXT,
                username TEXT,
                first_name TEXT,
                phone TEXT,
                UNIQUE(user_id, tg_id)
            )
        """)
        
        for u in users_list:
            cursor.execute(
                """INSERT OR IGNORE INTO parsed_audience (user_id, tg_id, username, first_name, phone) 
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, str(u["id"]), u["username"], u["first_name"], u["phone"])
            )
        
        conn.commit()
        conn.close()
