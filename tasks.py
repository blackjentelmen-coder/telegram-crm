import asyncio
from telethon import TelegramClient
from telethon.errors import ChatAdminRequiredError, UserPrivacyRestrictedError

async def analyze_chat_messages(
    client: TelegramClient, 
    chat_identifier: str, 
    limit: int = 5000, 
    target_user_id: int = None
):
    """
    Анализирует чат:
    - Если задан target_user_id, собирает сообщения конкретного человека.
    - Если target_user_id нет, сканирует последние `limit` сообщений (до 5000) 
      и собирает статистику/авторов, обходя ограничения скрытых участников.
    """
    try:
        chat = await client.get_entity(chat_identifier)
    except Exception as e:
        return {"error": f"Не удалось найти чат: {e}"}

    results = []
    users_found = set()

    # Сценарий А: Сбор по конкретному выбранному пользователю
    if target_user_id:
        async for message in client.iter_messages(chat, from_user=target_user_id, limit=limit):
            if message.text:
                results.append({
                    "message_id": message.id,
                    "date": str(message.date),
                    "text": message.text
                })
        return {"mode": "single_user", "user_id": target_user_id, "messages": results}

    # Сценарий Б: Анализ последних N сообщений (например, 5000) с автоопределением участников
    try:
        async for message in client.iter_messages(chat, limit=limit):
            if message.sender_id:
                users_found.add(message.sender_id)
            
            # Сохраняем базовую инфо о сообщениях для анализа
            if message.text:
                results.append({
                    "message_id": message.id,
                    "sender_id": message.sender_id,
                    "date": str(message.date),
                    "text": message.text
                })
    except Exception as e:
        return {"error": f"Ошибка при чтении сообщений: {e}"}

    return {
        "mode": "mass_analysis",
        "scanned_messages": len(results),
        "unique_active_senders": len(users_found),
        "messages": results[:100]  глейм/вырезка для превью, остальное в базу
    }
