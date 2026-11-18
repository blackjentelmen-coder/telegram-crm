import asyncio
import random
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PeerFloodError, UserIsBlockedError

async def run_spammer(
    clients: List[TelegramClient], 
    targets: List[str], # Список целей (чаты, @username, номера или линки)
    target_type: str = "chats", # "chats" или "people"
    spam_mode: str = "text", # "text" или "forward"
    spam_order: str = "sequential", # "sequential" (поочередно) или "rand" (вразнобой)
    texts: List[str] = None, # Список текстов (из наших плюсиков)
    forward_links: List[str] = None, # Список ссылок на посты ТГ для репоста
    limit_per_account: int = 20, 
    delay_sec: int = 5
):
    """
    Боевой модуль рассылки GHOSTCORE:
    - Поддерживает мульти-посты и списки ссылок (с добавлением через плюсики).
    - Учитывает порядок (поочередно / вразнобой).
    - Различает рассылку по чатам и по людям (номера / юзернеймы).
    - Защищен от флуд-контроля и банов.
    """
    results = {
        "success": 0,
        "failed": 0,
        "flood_wait": 0,
        "details": []
    }

    if not clients or not targets:
        return {"error": "Нет активных аккаунтов или пустая база целей!"}

    # Подготавливаем контент для отправки
    content_items = []
    if spam_mode == "text" and texts:
        content_items = [t.strip() for t in texts if t.strip()]
    elif spam_mode == "forward" and forward_links:
        content_items = [l.strip() for l in forward_links if l.strip()]

    if not content_items:
        return {"error": "Список постов или ссылок пуст!"}

    target_index = 0
    total_targets = len(targets)

    for client in clients:
        if target_index >= total_targets:
            break

        sent_by_this_account = 0
        
        try:
            me = await client.get_me()
            phone = me.phone or str(me.id)
        except Exception:
            phone = "Unknown"

        while sent_by_this_account < limit_per_account and target_index < total_targets:
            # Выбор цели в зависимости от порядка отправки
            if spam_order == "rand":
                target = random.choice(targets)
            else:
                target = targets[target_index]
                target_index += 1

            # Выбор контента (текст или репост) по порядку или случайно
            content = random.choice(content_items) if spam_order == "rand" else content_items[sent_by_this_account % len(content_items)]

            try:
                # Очищаем цель от лишних пробелов
                target_clean = target.strip()

                # Вариант А: Репост по ссылке на пост ТГ (формат: https://t.me/channel/123)
                if spam_mode == "forward" and "t.me/" in content:
                    parts = content.split("t.me/")[-1].split("/")
                    if len(parts) >= 2:
                        source_chat = parts[0]
                        msg_id = int(parts[1])
                        await client.forward_messages(target_clean, msg_id, source_chat)
                    else:
                        raise ValueError(f"Неверный формат ссылки на пост: {content}")

                # Вариант Б: Отправка текста (работает и для чатов, и для людей/номеров)
                elif spam_mode == "text":
                    # Если это номер телефона в режиме людей, Telethon может импортировать контакт или отправить по номеру
                    if target_type == "people" and target_clean.startswith("+"):
                        try:
                            from telethon.tl.types import InputPhoneContact
                            contact = InputPhoneContact(client_id=0, phone=target_clean, first_name="User", last_name="")
                            result = await client(functions.contacts.ImportContactsRequest(contacts=[contact]))
                            if result.users:
                                user_to_msg = result.users[0]
                                await client.send_message(user_to_msg, content)
                            else:
                                await client.send_message(target_clean, content)
                        except Exception:
                            await client.send_message(target_clean, content)
                    else:
                        await client.send_message(target_clean, content)

                results["success"] += 1
                sent_by_this_account += 1
                results["details"].append({"account": phone, "target": target_clean, "status": "sent"})

                # Пауза между сообщениями
                await asyncio.sleep(delay_sec)

            except FloodWaitError as e:
                results["flood_wait"] += 1
                results["details"].append({"account": phone, "error": f"FloodWait {e.seconds}s"})
                print(f"⚠️ Аккаунт {phone} поймал Флуд-контроль на {e.seconds} сек.")
                break # Тормозим этот аккаунт, переключаемся на следующий

            except (PeerFloodError, UserIsBlockedError):
                results["failed"] += 1
                results["details"].append({"account": phone, "error": "Blocked / PeerFlood"})
                break

            except Exception as e:
                results["failed"] += 1
                results["details"].append({"account": phone, "target": target, "error": str(e)})
                await asyncio.sleep(3)

    return results
