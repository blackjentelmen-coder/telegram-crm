import asyncio
from telethon import TelegramClient

async def parse_chat_comprehensive(
    client: TelegramClient, 
    chat_identifier: str, 
    limit: int = 5000, 
    target_user_id: int = None,
    extract_phones: bool = True
):
    """
    Универсальный сборщик данных из чата:
    - `limit`: пользователь сам задает, сколько последних сообщений просканировать.
    - Исключает любые повторения пользователей (уникальность по user_id).
    """
    try:
        chat = await client.get_entity(chat_identifier)
    except Exception as e:
        return {"error": f"Не удалось найти чат: {e}"}

    collected_users = {}  # Словарь для уникальных пользователей {user_id: data}
    messages_log = []

    # Определяем параметры итерации сообщений
    kwargs = {"limit": limit}
    if target_user_id:
        kwargs["from_user"] = target_user_id

    async for message in client.iter_messages(chat, **kwargs):
        sender = message.sender
        
        # Собираем данные об авторе сообщения без повторов
        if sender and hasattr(sender, "id"):
            uid = sender.id
            if uid not in collected_users:
                username = getattr(sender, "username", None)
                phone = getattr(sender, "phone", None) if extract_phones else None
                
                collected_users[uid] = {
                    "user_id": uid,
                    "username": f"@{username}" if username else None,
                    "profile_link": f"https://t.me/{username}" if username else f"tg://user?id={uid}",
                    "phone": f"+{phone}" if phone else "Скрыт / Нет",
                    "first_name": getattr(sender, "first_name", ""),
                    "last_name": getattr(sender, "last_name", "")
                }

        # Сохраняем информацию о сообщении
        if message.text:
            messages_log.append({
                "message_id": message.id,
                "sender_id": message.sender_id,
                "date": str(message.date),
                "text": message.text
            })

    return {
        "total_messages_scanned": len(messages_log),
        "unique_users_count": len(collected_users),
        "unique_users_extracted": list(collected_users.values()),
        "messages": messages_log
    }
