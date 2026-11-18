import asyncio
import random
from typing import List
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PeerFloodError, UserIsBlockedError

async def run_spammer(
    clients: List[TelegramClient], 
    targets: List, # Список чатов/пользователей для рассылки
    message_text: str = None, 
    forward_from_link: dict = None, # Если пересылаем готовый пост: {"chat": "...", "msg_id": 123}
    limit_per_account: int = 10, 
    delay_min: int = 15, 
    delay_max: int = 30
):
    """
    Модуль рассылки сообщений:
    - Поддерживает собственный текст или пересылку готового поста по ссылке.
    - Распределяет нагрузку по аккаунтам с лимитами и рандомными паузами.
    """
    results = {
        "success": 0,
        "failed": 0,
        "flood_wait": 0,
        "details": []
    }

    target_index = 0
    total_targets = len(targets)

    for client in clients:
        if target_index >= total_targets:
            break

        sent_by_this_account = 0
        
        try:
            me = await client.get_me()
            phone = me.phone
        except Exception:
            phone = "Unknown"

        while sent_by_this_account < limit_per_account and target_index < total_targets:
            target = targets[target_index]
            target_index += 1

            try:
                # Вариант А: Пересылка готового поста по ссылке
                if forward_from_link:
                    source_chat = forward_from_link["chat"]
                    msg_id = forward_from_link["msg_id"]
                    await client.forward_messages(target, msg_id, source_chat)
                
                # Вариант Б: Отправка собственного текста
                elif message_text:
                    await client.send_message(target, message_text)

                results["success"] += 1
                sent_by_this_account += 1
                results["details"].append({"account": phone, "target": str(target), "status": "sent"})

                # Рандомная задержка между отправками
                sleep_time = random.randint(delay_min, delay_max)
                await asyncio.sleep(sleep_time)

            except FloodWaitError as e:
                results["flood_wait"] += 1
                results["details"].append({"account": phone, "error": f"FloodWait {e.seconds}s"})
                print(f"⚠️ Аккаунт {phone} поймал Флуд-контроль на {e.seconds} сек.")
                break # Тормозим этот аккаунт, переходим к следующему

            except (PeerFloodError, UserIsBlockedError):
                results["failed"] += 1
                results["details"].append({"account": phone, "error": "Blocked / PeerFlood"})
                break

            except Exception as e:
                results["failed"] += 1
                results["details"].append({"account": phone, "target": str(target), "error": str(e)})
                await asyncio.sleep(5)

    return results
