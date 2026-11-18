import asyncio
import random
from typing import List
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, PeerFloodError

async def run_inviter(
    clients: List[TelegramClient], 
    target_chat, 
    users_to_invite: List, 
    limit_per_account: int = 15, 
    delay_min: int = 10, 
    delay_max: int = 25, 
    mode: str = "random" # "random" (вразброс) или "sequential" (по очереди)
):
    """
    Массовый инвайтер с распределением по аккаунтам, лимитами и рандомными паузами.
    """
    results = {
        "success": 0,
        "failed": 0,
        "flood_wait": 0,
        "details": []
    }

    # Если режим "вразброс", перемешаем список пользователей и пул клиентов
    if mode == "random":
        random.shuffle(users_to_invite)
        random.shuffle(clients)

    user_index = 0
    total_users = len(users_to_invite)

    for client in clients:
        if user_index >= total_users:
            break

        invited_by_this_account = 0
        
        # Получаем информацию о клиенте для логов
        try:
            me = await client.get_me()
            phone = me.phone
        except Exception:
            phone = "Unknown"

        while invited_by_this_account < limit_per_account and user_index < total_users:
            target_user = users_to_invite[user_index]
            user_index += 1

            try:
                # Пытаемся добавить пользователя в чат/канал
                from telethon.tl.functions.channels import InviteToChannelRequest
                
                await client(InviteToChannelRequest(
                    channel=target_chat,
                    users=[target_user]
                ))

                results["success"] += 1
                invited_by_this_account += 1
                results["details"].append({"account": phone, "user": str(target_user), "status": "success"})

                # Рандомная задержка между инвайтами, чтобы не получить бан
                sleep_time = random.randint(delay_min, delay_max)
                await asyncio.sleep(sleep_time)

            except UserPrivacyRestrictedError:
                # Настройки приватности пользователя запрещают добавлять его в группы
                results["failed"] += 1
                results["details"].append({"account": phone, "user": str(target_user), "status": "privacy_restricted"})
            
            except FloodWaitError as e:
                # Аккаунт поймал флуд-контроль
                results["flood_wait"] += 1
                results["details"].append({"account": phone, "error": f"FloodWait {e.seconds}s"})
                print(f"⚠️ Аккаунт {phone} поймал FloodWait на {e.seconds} секунд. Переключаем/останавливаем.")
                break # Прерываем работу этого аккаунта и переходим к следующему

            except PeerFloodError:
                results["failed"] += 1
                results["details"].append({"account": phone, "error": "PeerFlood error"})
                break # Слишком много спама с этого аккаунта, лучше исключить его из текущей рассылки

            except Exception as e:
                results["failed"] += 1
                results["details"].append({"account": phone, "user": str(target_user), "error": str(e)})
                # Небольшая пауза при ошибке
                await asyncio.sleep(5)

    return results
