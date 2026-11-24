import asyncio
import os
import re
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

from database import (
    init_db,
    ensure_bot_schema,
    link_tg_to_key,
    get_user_by_tg_id,
    get_active_client_tg_accounts,
)

API_ID = 38977487
API_HASH = "a6afb062a4c36f6d7b2cb87d27385672"
BOT_TOKEN = os.getenv("GHOSTCORE_BOT_TOKEN", "ВСТАВЬ_СЮДА_BOT_TOKEN")

init_db()
ensure_bot_schema()

bot = TelegramClient("ghostcore_bridge_bot", API_ID, API_HASH)
user_states = {}


def parse_peer_target(raw: str):
    target = (raw or "").strip()
    if target.startswith("https://t.me/") or target.startswith("http://t.me/"):
        slug = target.split("t.me/", 1)[1].split("?", 1)[0].strip("/")
        if slug.startswith("+") or slug.startswith("joinchat/"):
            return None
        return slug
    if re.fullmatch(r"-?\d+", target):
        return int(target)
    return target


def main_menu_buttons(linked: bool):
    if not linked:
        return [
            [Button.inline("🔑 Привязать ключ доступа", b"cmd_link")],
        ]
    return [
        [Button.inline("💬 Написать с аккаунта сайта", b"cmd_send_from_acc")],
        [Button.inline("📱 Мои аккаунты сайта", b"cmd_accounts"), Button.inline("👤 Профиль", b"cmd_profile")],
        [Button.inline("🔑 Сменить ключ", b"cmd_link")],
    ]


@bot.on(events.NewMessage(pattern=r"^/start(?:\s+(.+))?$"))
async def on_start(event):
    sender = await event.get_sender()
    tg_id = event.sender_id
    username = getattr(sender, "username", "") or ""
    arg = (event.pattern_match.group(1) or "").strip()

    if arg:
        res = link_tg_to_key(arg, tg_id, username)
        if res["ok"]:
            u = res["user"]
            await event.respond(
                f"✅ Аккаунт привязан к ключу `{u['key_code']}`!\n"
                f"Имя: **{u.get('client_name') or 'Без имени'}**\n"
                f"Тариф: **{'VIP' if int(u.get('is_paid') or 0) == 1 else 'FREE'}**\n\n"
                f"Сюда будут приходить уведомления, и отсюда можно писать от лица привязанных на сайте аккаунтов.",
                buttons=main_menu_buttons(True),
            )
            return
        await event.respond(f"❌ {res['error']}", buttons=main_menu_buttons(False))
        return

    existing = get_user_by_tg_id(tg_id)
    if existing:
        await event.respond(
            f"👋 С возвращением, **{existing.get('client_name') or existing['key_code']}**!\n"
            f"Ключ: `{existing['key_code']}` • Тариф: **{'VIP' if int(existing.get('is_paid') or 0) == 1 else 'FREE'}**",
            buttons=main_menu_buttons(True),
        )
    else:
        await event.respond(
            "👋 Привет! Привяжите свой ключ доступа с сайта, чтобы получать уведомления "
            "и писать сообщения от имени ваших Telegram-аккаунтов.\n\n"
            "Отправьте ключ сообщением или нажмите кнопку ниже.",
            buttons=main_menu_buttons(False),
        )


@bot.on(events.CallbackQuery)
async def on_callback(event):
    tg_id = event.sender_id
    data = event.data

    if data == b"cmd_link":
        user_states[tg_id] = {"step": "awaiting_key"}
        await event.respond("Введите ваш ключ доступа с сайта (например, `A1B2C3D4`):")
        await event.answer()
        return

    user = get_user_by_tg_id(tg_id)
    if not user:
        await event.respond("Сначала привяжите ключ доступа.", buttons=main_menu_buttons(False))
        await event.answer()
        return

    if data == b"cmd_profile":
        paid_label = "VIP (оплачен)" if int(user.get("is_paid") or 0) == 1 else "FREE"
        await event.respond(
            f"👤 **Профиль**\n"
            f"Имя: **{user.get('client_name') or 'Без имени'}**\n"
            f"Ключ: `{user['key_code']}`\n"
            f"Тариф: **{paid_label}**",
            buttons=main_menu_buttons(True),
        )
        await event.answer()
        return

    if data == b"cmd_accounts":
        accs = get_active_client_tg_accounts(user["key_code"])
        if not accs:
            await event.respond(
                "У вас пока нет активных привязанных Telegram-аккаунтов на сайте.\n"
                "Добавьте их в личном кабинете на сайте (по номеру или QR).",
                buttons=main_menu_buttons(True),
            )
            await event.answer()
            return
        lines = ["📱 **Активные аккаунты с сайта:**\n"]
        for idx, a in enumerate(accs, start=1):
            lines.append(f"{idx}. `{a['phone']}` — {a['status']}")
        await event.respond("\n".join(lines), buttons=main_menu_buttons(True))
        await event.answer()
        return

    if data == b"cmd_send_from_acc":
        accs = get_active_client_tg_accounts(user["key_code"])
        if not accs:
            await event.respond(
                "❌ Нет активных аккаунтов на сайте. Сначала авторизуйте аккаунт в кабинете.",
                buttons=main_menu_buttons(True),
            )
            await event.answer()
            return

        btns = [
            [Button.inline(f"📤 {a['phone']}", f"pick_acc:{a['id']}".encode())]
            for a in accs
        ]
        await event.respond("Выберите аккаунт, от имени которого отправить сообщение:", buttons=btns)
        await event.answer()
        return

    if data.startswith(b"pick_acc:"):
        acc_id = int(data.decode().split(":", 1)[1])
        accs = get_active_client_tg_accounts(user["key_code"])
        chosen = next((a for a in accs if int(a["id"]) == acc_id), None)
        if not chosen:
            await event.respond("❌ Аккаунт не найден или неактивен.", buttons=main_menu_buttons(True))
            await event.answer()
            return

        user_states[tg_id] = {
            "step": "awaiting_peer",
            "client_key": user["key_code"],
            "acc": chosen,
        }
        await event.respond(
            f"Выбран аккаунт `{chosen['phone']}`.\n"
            f"Отправьте `@username`, ID или ссылку `https://t.me/...` получателя:"
        )
        await event.answer()
        return


@bot.on(events.NewMessage)
async def on_text_message(event):
    if event.text and event.text.startswith("/"):
        return

    tg_id = event.sender_id
    sender = await event.get_sender()
    username = getattr(sender, "username", "") or ""
    text = (event.raw_text or "").strip()
    if not text:
        return

    state = user_states.get(tg_id)

    if state and state.get("step") == "awaiting_key":
        user_states.pop(tg_id, None)
        res = link_tg_to_key(text, tg_id, username)
        if res["ok"]:
            u = res["user"]
            await event.respond(
                f"✅ Ключ `{u['key_code']}` успешно привязан к вашему Telegram!",
                buttons=main_menu_buttons(True),
            )
        else:
            await event.respond(f"❌ {res['error']}", buttons=main_menu_buttons(False))
        return

    if state and state.get("step") == "awaiting_peer":
        peer = parse_peer_target(text)
        if not peer:
            await event.respond("❌ Некорректный получатель. Укажите `@username`, ID или `t.me/username`.")
            return
        state["peer"] = peer
        state["step"] = "awaiting_msg_body"
        user_states[tg_id] = state
        await event.respond(f"Получатель: `{peer}`. Теперь введите текст сообщения:")
        return

    if state and state.get("step") == "awaiting_msg_body":
        acc = state.get("acc")
        peer = state.get("peer")
        user_states.pop(tg_id, None)

        if not acc or not acc.get("session_string"):
            await event.respond("❌ Сессия аккаунта недоступна.", buttons=main_menu_buttons(True))
            return

        client = TelegramClient(StringSession(acc["session_string"]), API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await event.respond(
                    f"❌ Аккаунт `{acc['phone']}` не авторизован в Telegram.",
                    buttons=main_menu_buttons(True),
                )
                return
            await client.send_message(peer, text)
            await event.respond(
                f"✅ Сообщение отправлено от `{acc['phone']}` для `{peer}`.",
                buttons=main_menu_buttons(True),
            )
        except Exception as e:
            await event.respond(f"❌ Ошибка отправки: {e}", buttons=main_menu_buttons(True))
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        return

    user = get_user_by_tg_id(tg_id)
    if not user:
        res = link_tg_to_key(text, tg_id, username)
        if res["ok"]:
            u = res["user"]
            await event.respond(
                f"✅ Ключ `{u['key_code']}` привязан!",
                buttons=main_menu_buttons(True),
            )
        else:
            await event.respond(
                "Отправьте ваш ключ доступа с сайта для привязки аккаунта.",
                buttons=main_menu_buttons(False),
            )
        return

    await event.respond("Выберите действие в меню ниже:", buttons=main_menu_buttons(True))


async def run_bot():
    if BOT_TOKEN == "ВСТАВЬ_СЮДА_BOT_TOKEN" or not BOT_TOKEN.strip():
        raise RuntimeError("Укажите токен бота в GHOSTCORE_BOT_TOKEN или в переменной BOT_TOKEN.")
    await bot.start(bot_token=BOT_TOKEN.strip())
    print("🤖 GHOSTCORE Telegram Bridge Bot запущен")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(run_bot())
