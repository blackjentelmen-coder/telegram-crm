import asyncio
from typing import List
from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChatAdminRequiredError

async def run_parser(
    clients: List[TelegramClient],
    targets: List[str], # База чатов для парсинга
    parser_mode: str = "active" # "all" (все участники) или "active" (те, кто писал в чат)
):
    """
    Боевой модуль парсера GHOSTCORE:
    - Режим "all": собирает всех доступных участников открытого чата.
    - Режим "active": собирает только тех, кто реально писал сообщения в чате.
    - Корректно обрабатывает скрытые списки участников и ошибки доступа.
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
            
            # ВАРИАНТ А: Сбор только тех, кто писал в чат (активные по сообщениям)
            if parser_mode == "active":
                msg_count = 0
                # Проходим по недавним сообщениям в чате, чтобы вытащить авторов
                async for message in client.iter_messages(entity, limit=500):
                    if message.sender and not message.sender.bot and not message.sender.deleted:
                        sender = message.sender
                        identifier = f"@{sender.username}" if sender.username else str(sender.id)
                        collected_users.add(identifier)
                        msg_count += 1
                
                if msg_count == 0:
                    results["details"].append({"chat": target_clean, "warning": "В чате нет доступных сообщений для сбора активных"})
                else:
                    results["success"] += 1
                    results["details"].append({"chat": target_clean, "status": "active_parsed", "count": msg_count})

            # ВАРИАНТ Б: Сбор всех участников (если список открыт)
            else:
                try:
                    async for user in client.iter_participants(entity):
                        if not user or user.bot or user.deleted:
                            continue
                        identifier = f"@{user.username}" if user.username else str(user.id)
                        collected_users.add(identifier)
                        
                        if len(collected_users) >= 2000:
                            break
                            
                    results["success"] += 1
                    results["details"].append({"chat": target_clean, "status": "all_parsed"})
                
                except Exception as participant_err:
                    # Если список участников скрыт настройками чата
                    results["failed"] += 1
                    results["details"].append({
                        "chat": target_clean, 
                        "error": "Список участников скрыт или недоступен. Попробуйте режим «Активные» (сбор по сообщениям)."
                    })

        except FloodWaitError as e:
            results["details"].append({"chat": target_clean, "error": f"FloodWait {e.seconds}s"})
            print(f"⚠️ Парсер поймал FloodWait на {e.seconds} сек.")
            await asyncio.sleep(e.seconds)
            break

        except Exception as e:
            results["failed"] += 1
            results["details"].append({"chat": target_clean, "error": str(e)})

    results["users"] = list(collected_users)
    results["total_parsed"] = len(results["users"])

    return results
