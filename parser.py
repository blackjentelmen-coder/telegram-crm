import asyncio
from typing import List
from telethon import TelegramClient
from telethon.errors import FloodWaitError

async def run_parser(
    clients: List[TelegramClient],
    targets: List[str], # База чатов для парсинга
    parser_mode: str = "active", # "all" (все участники) или "active" (те, кто писал в чат)
    limit: int = 500 # Динамическая глубина сообщений или лимит участников, заданная пользователем
):
    """
    Полный боевой модуль парсера GHOSTCORE:
    - Режим "all": собирает участников открытого чата до указанного лимита.
    - Режим "active": сканирует заданное количество сообщений (limit) и собирает авторов, которые реально писали в чат.
    - Корректно обрабатывает скрытые списки участников.
    """
    results = {
        "success": 0,
        "failed": 0,
        "total_parsed": 0,
        "users": [],
        "details": []
    }

    if not clients or not targets:
        return {"error": "Нет активных аккаунтов или не указаны чаты для парсинга!"}

    client = clients[0]
    collected_users = set()

    for target in targets:
        target_clean = target.strip()
        if not target_clean:
            continue

        try:
            entity = await client.get_entity(target_clean)
            
            # ВАРИАНТ А: Сбор активных по сообщениям (с учетом выбранной пользователем глубины limit)
            if parser_mode == "active":
                msg_count = 0
                async for message in client.iter_messages(entity, limit=limit):
                    if message.sender and not message.sender.bot and not message.sender.deleted:
                        sender = message.sender
                        identifier = f"@{sender.username}" if sender.username else str(sender.id)
                        collected_users.add(identifier)
                        msg_count += 1
                
                if msg_count == 0:
                    results["details"].append({"chat": target_clean, "warning": "В чате нет доступных сообщений для сбора активных"})
                else:
                    results["success"] += 1
                    results["details"].append({"chat": target_clean, "status": "active_parsed", "messages_checked": msg_count})

            # ВАРИАНТ Б: Сбор всех участников (с лимитом на количество)
            else:
                try:
                    count = 0
                    async for user in client.iter_participants(entity):
                        if not user or user.bot or user.deleted:
                            continue
                        identifier = f"@{user.username}" if user.username else str(user.id)
                        collected_users.add(identifier)
                        count += 1
                        
                        if count >= limit:
                            break
                            
                    results["success"] += 1
                    results["details"].append({"chat": target_clean, "status": "all_parsed", "count": count})
                
                except Exception:
                    results["failed"] += 1
                    results["details"].append({
                        "chat": target_clean, 
                        "error": "Список участников скрыт. Переключитесь на режим «Активные (по смс)»."
                    })

        except FloodWaitError as e:
            results["details"].append({"chat": target_clean, "error": f"FloodWait {e.seconds}s"})
            await asyncio.sleep(e.seconds)
            break

        except Exception as e:
            results["failed"] += 1
            results["details"].append({"chat": target_clean, "error": str(e)})

    results["users"] = list(collected_users)
    results["total_parsed"] = len(results["users"])

    return results
