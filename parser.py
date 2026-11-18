import re
from typing import Dict, Any, Optional

def parse_telegram_link(link: str) -> Optional[Dict[str, Any]]:
    """
    Распознает и разбирает все типы ссылок Telegram:
    - Публичные каналы/чаты
    - Приватные инвайт-ссылки (+hash или joinchat/hash)
    - Ссылки на конкретные сообщения (в публичных и приватных чатах)
    """
    link = link.strip()
    
    # 1. Обработка юзернейма в чистом виде (например, @durov)
    if link.startswith('@'):
        return {
            "type": "public_chat",
            "identifier": link[1:],
            "message_id": None
        }

    # 2. Шаблоны для ссылок t.me / telegram.me
    # Приватная инвайт-ссылка со знаком плюс: t.me/+hash или t.me/joinchat/hash
    private_invite_match = re.search(r'(?:t\.me|telegram\.me)/(?:\+|joinchat/)([a-zA-Z0-9_\-]+)', link)
    if private_invite_match:
        return {
            "type": "private_invite",
            "identifier": private_invite_match.group(1),
            "message_id": None
        }

    # Ссылка на конкретное сообщение в супергруппе/канале с ID: t.me/c/1234567890/555
    msg_c_match = re.search(r'(?:t\.me|telegram\.me)/c/(\d+)/(\d+)', link)
    if msg_c_match:
        return {
            "type": "message_c",
            "chat_id": int("-100" + msg_c_match.group(1)), # Телеграм требует -100 для супергрупп
            "message_id": int(msg_c_match.group(2))
        }

    # Ссылка на сообщение в публичном чате: t.me/username/555
    msg_public_match = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/(\d+)', link)
    if msg_public_match:
        return {
            "type": "message_public",
            "identifier": msg_public_match.group(1),
            "message_id": int(msg_public_match.group(2))
        }

    # Обычная ссылка на публичный чат/канал: t.me/username
    public_match = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', link)
    if public_match:
        return {
            "type": "public_chat",
            "identifier": public_match.group(1),
            "message_id": None
        }

    return None # Если формат не распознан
