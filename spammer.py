import asyncio
import random
from typing import List, Dict
from telethon import TelegramClient
from telethon.tl import functions
from telethon.tl.types import InputPhoneContact
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    UserIsBlockedError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    UserIdInvalidError,
    InputUserDeactivatedError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)

from database import (
    set_tg_account_status,
    ACCOUNT_STATUS_SPAM,
    ACCOUNT_STATUS_SESSION_LOST,
)
from ghost_logger import log_ok, log_warn, log_err

NONEXISTENT_ERRORS = (
    UsernameNotOccupiedError,
    UsernameInvalidError,
    UserIdInvalidError,
    InputUserDeactivatedError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
)

SESSION_DEAD_ERRORS = (
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)


def _is_nonexistent_target(err: Exception) -> bool:
    if isinstance(err, NONEXISTENT_ERRORS):
        return True
    low = str(err).lower()
    return any(
        mark in low
        for mark in (
            "username not occupied",
            "username invalid",
            "user id invalid",
            "user deactivated",
            "no user has",
            "nobody is using this username",
            "chat write forbidden",
            "channel private",
            "could not find the input entity",
            "cannot find any entity",
        )
    )


async def run_spammer(
    clients: List[TelegramClient],
    targets: List[str],
    target_type: str = "chats",
    spam_mode: str = "text",
    spam_order: str = "sequential",
    texts: List[str] = None,
    forward_links: List[str] = None,
    limit_per_account: int = 20,
    delay_sec: int = 5,
    already_sent: List[str] = None,
    already_nonexistent: List[str] = None,
    account_meta: List[Dict] = None,
) -> dict:
    sent_list = list(already_sent or [])
    nonexistent_list = list(already_nonexistent or [])
    failed_accounts = []

    results = {
        "success": 0,
        "failed": 0,
        "flood_wait": 0,
        "sent_targets": sent_list,
        "nonexistent_targets": nonexistent_list,
        "remaining_targets": [],
        "failed_accounts": failed_accounts,
        "details": [],
    }

    done_set = set(sent_list) | set(nonexistent_list)
    remaining = [t.strip() for t in (targets or []) if t and t.strip() and t.strip() not in done_set]
    results["remaining_targets"] = list(remaining)

    if not clients:
        return {"error": "Нет активных аккаунтов!", **results}

    if not remaining:
        return {"error": "Все цели уже обработаны (в списке отправленных или не существующих).", **results}

    content_items = []
    if spam_mode == "text" and texts:
        content_items = [t.strip() for t in texts if t and t.strip()]
    elif spam_mode == "forward" and forward_links:
        content_items = [l.strip() for l in forward_links if l and l.strip()]

    if not content_items:
        return {"error": "Список постов или ссылок пуст!", **results}

    is_random = (spam_order or "").strip().lower() in ("rand", "random")
    safe_delay = max(0, int(delay_sec or 0))
    safe_limit = max(1, int(limit_per_account or 1))
    meta_map = {idx: m for idx, m in enumerate(account_meta or [])}

    for acc_idx, client in enumerate(clients):
        if not remaining:
            break

        sent_by_this_account = 0
        acc_info = meta_map.get(acc_idx, {})
        acc_id = acc_info.get("id")

        try:
            me = await client.get_me()
            phone = acc_info.get("phone") or (f"+{me.phone}" if me and me.phone else str(getattr(me, "id", "Unknown")))
        except Exception:
            phone = acc_info.get("phone") or "Unknown"

        while sent_by_this_account < safe_limit and remaining:
            if is_random:
                target = random.choice(remaining)
            else:
                target = remaining[0]

            content = (
                random.choice(content_items)
                if is_random
                else content_items[(len(sent_list) + sent_by_this_account) % len(content_items)]
            )

            try:
                target_clean = target.strip()

                if spam_mode == "forward" and "t.me/" in content:
                    parts = content.split("t.me/")[-1].split("/")
                    if len(parts) >= 2:
                        source_chat = parts[0]
                        msg_id = int(parts[1])
                        await client.forward_messages(target_clean, msg_id, source_chat)
                    else:
                        raise ValueError(f"Неверный формат ссылки на пост: {content}")

                elif spam_mode == "text":
                    if target_type in ("people", "users") and target_clean.startswith("+"):
                        try:
                            contact = InputPhoneContact(
                                client_id=0,
                                phone=target_clean,
                                first_name="User",
                                last_name="",
                            )
                            imported = await client(
                                functions.contacts.ImportContactsRequest(contacts=[contact])
                            )
                            if imported.users:
                                await client.send_message(imported.users[0], content)
                            else:
                                raise UserIdInvalidError(argument=None)
                        except Exception as contact_err:
                            if _is_nonexistent_target(contact_err):
                                raise
                            await client.send_message(target_clean, content)
                    else:
                        await client.send_message(target_clean, content)

                if target_clean in remaining:
                    remaining.remove(target_clean)
                if target_clean not in sent_list:
                    sent_list.append(target_clean)

                results["success"] += 1
                sent_by_this_account += 1
                results["details"].append({
                    "account": phone,
                    "target": target_clean,
                    "status": "sent",
                })

                if safe_delay > 0:
                    await asyncio.sleep(safe_delay)

            except NONEXISTENT_ERRORS as e:
                if target in remaining:
                    remaining.remove(target)
                if target not in nonexistent_list:
                    nonexistent_list.append(target)
                results["failed"] += 1
                results["details"].append({
                    "account": phone,
                    "target": target,
                    "status": "nonexistent",
                    "error": type(e).__name__,
                })
                log_warn(f"Цель не существует ({target}): удалена из очереди")

            except SESSION_DEAD_ERRORS as e:
                if acc_id:
                    set_tg_account_status(acc_id, ACCOUNT_STATUS_SESSION_LOST, "Слетела сессия при рассылке")
                failed_accounts.append({"phone": phone, "reason": ACCOUNT_STATUS_SESSION_LOST})
                log_warn(f"Аккаунт {phone}: слетела сессия -> передаём остаток следующему")
                break

            except FloodWaitError as e:
                results["flood_wait"] += 1
                if acc_id:
                    set_tg_account_status(acc_id, ACCOUNT_STATUS_SPAM, f"FloodWait {e.seconds}s")
                failed_accounts.append({"phone": phone, "reason": f"Спам / FloodWait {e.seconds}s"})
                results["details"].append({
                    "account": phone,
                    "error": f"FloodWait {e.seconds}s",
                })
                log_warn(f"Аккаунт {phone}: FloodWait {e.seconds} сек. -> передаём остаток следующему")
                break

            except (PeerFloodError, UserIsBlockedError) as e:
                results["failed"] += 1
                if acc_id:
                    set_tg_account_status(acc_id, ACCOUNT_STATUS_SPAM, f"Ограничение: {type(e).__name__}")
                failed_accounts.append({"phone": phone, "reason": f"Спам / {type(e).__name__}"})
                results["details"].append({
                    "account": phone,
                    "error": type(e).__name__,
                })
                log_warn(f"Аккаунт {phone}: {type(e).__name__} -> передаём остаток следующему")
                break

            except Exception as e:
                if _is_nonexistent_target(e):
                    if target in remaining:
                        remaining.remove(target)
                    if target not in nonexistent_list:
                        nonexistent_list.append(target)
                    results["failed"] += 1
                    log_warn(f"Цель не существует ({target}): удалена из очереди")
                else:
                    results["failed"] += 1
                    results["details"].append({
                        "account": phone,
                        "target": target,
                        "error": str(e),
                    })
                    log_err(f"Спамер ({phone} -> {target}): {e}")
                    await asyncio.sleep(2)

    results["remaining_targets"] = list(remaining)
    results["sent_targets"] = list(sent_list)
    results["nonexistent_targets"] = list(nonexistent_list)
    results["failed_accounts"] = list(failed_accounts)

    log_ok(
        f"Спамер: отправлено={len(sent_list)}, не существует={len(nonexistent_list)}, "
        f"осталось={len(remaining)}, сбойных акков={len(failed_accounts)}"
    )
    return results
