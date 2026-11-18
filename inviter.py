import asyncio
import random
from typing import List
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, ChatAdminRequiredError, UserNotMutualContactError
from telethon.tl.functions.channels import InviteToChannelRequest

async def run_inviter(
    clients: List[TelegramClient],
    target_chat: str,   # Куда инвайтим
    sources: List[str] = None, # База чатов-доноров (если парсим оттуда)
    direct_users: List[str] = None, # Готовая база пользователей (если залили текстом)
    limit_per_account: int = 10,
    delay_min: int = 10,
    delay_max: int = 20
):
    """
    Боевой модуль инвайтера GHOSTCORE:
    - Поддерживает парсинг из чатов-доноров ИЛИ прямую загрузку готовой базы текстом.
    - Безопасно добавляет пользователей в целевой чат с рандомными паузами.
    - Обрабатывает ограничения приватности и флуд-контроль.
    """
    results = {
        "success": 0,
        "failed": 0,
        "flood_wait": 0,
        "details": []
    }

    if not clients or not target_chat:
        return {"error": "Не указаны активные аккаунты или целевой чат для инвайта!"}

    users_to_invite = []
    collector_client = clients[0]

    # Вариант 1: Если передана готовая база текстом (список юзернеймов/ссылок)
    if direct_users and len(direct_users) > 0:
        for item in direct_users:
            item_clean = item.strip()
            if item_clean:
                users_to_invite.append(item_clean)

    # Вариант 2: Если база текстом не заполнена, пробуем собрать из чатов-доноров
    elif sources and len(sources) > 0:
        try:
            for source in sources:
                source_clean = source.strip()
                if not source_clean:
                    continue
                try:
                    entity = await collector_client.get_entity(source_clean)
                    async for user in collector_client.iter_participants(entity, limit=50):
                        if user and not user.bot and not user.deleted:
                            identifier = f"@{user.username}" if user.username else user.id
                            users_to_invite.append(identifier)
                except Exception as e:
                    print(f"⚠️ Ошибка чтения донора {source_clean}: {e}")
        except Exception as e:
            return {"error": f"Ошибка сбора из доноров: {str(e)}"}

    if not users_to_invite:
        return {"error": "База для инвайта пуста! Заполните список пользователей или чаты-доноры."}

    # Уникализируем список
    user_list = list(dict.fromkeys(users_to_invite))
    
    user_index = 0
    total_users = len(user_list)

    for client in clients:
        if user_index >= total_users:
            break

        invited_by_this_account = 0
        
        try:
            me = await client.get_me()
            phone = me.phone or str(me.id)
        except Exception:
            phone = "Unknown"

        try:
            target_entity = await client.get_entity(target_chat.strip())
        except Exception as e:
            results["details"].append({"account": phone, "error": f"Не найден целевой чат: {str(e)}"})
            continue

        while invited_by_this_account < limit_per_account and user_index < total_users:
            target_user = user_list[user_index]
            user_index += 1

            try:
                # Получаем сущность пользователя для инвайта
                user_entity = await client.get_entity(target_user)

                await client(InviteToChannelRequest(
                    channel=target_entity,
                    users=[user_entity]
                ))

                results["success"] += 1
                invited_by_this_account += 1
                results["details"].append({"account": phone, "target": str(target_user), "status": "invited"})

                sleep_time = random.randint(delay_min, delay_max)
                await asyncio.sleep(sleep_time)

            except FloodWaitError as e:
                results["flood_wait"] += 1
                results["details"].append({"account": phone, "error": f"FloodWait {e.seconds}s"})
                print(f"⚠️ Аккаунт {phone} поймал Флуд-контроль на {e.seconds} сек.")
                break

            except UserPrivacyRestrictedError:
                results["failed"] += 1
                continue

            except (ChatAdminRequiredError, UserNotMutualContactError) as e:
                results["failed"] += 1
                results["details"].append({"account": phone, "error": f"Права администратора: {str(e)}"})
                break

            except Exception as e:
                results["failed"] += 1
                results["details"].append({"account": phone, "target": str(target_user), "error": str(e)})
                await asyncio.sleep(2)

    return results
