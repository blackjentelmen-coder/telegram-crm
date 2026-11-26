import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Dict, Deque

# Секреты берём из окружения; если не заданы — генерируем случайные при старте
ADMIN_LOGIN = os.getenv("GHOSTCORE_ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("GHOSTCORE_ADMIN_PASSWORD", "")
SIGNING_SECRET = os.getenv("GHOSTCORE_SIGNING_SECRET", secrets.token_hex(32)).encode("utf-8")

# Анти-брутфорс в памяти: ключ -> метки неудачных попыток
_FAILED_ATTEMPTS: Dict[str, Deque[float]] = defaultdict(deque)
_BLOCKED_UNTIL: Dict[str, float] = {}


def verify_admin_credentials(login: str, password: str) -> bool:
    if not ADMIN_PASSWORD:
        # Пока не задан GHOSTCORE_ADMIN_PASSWORD в окружении — вход закрыт
        return False
    ok_login = hmac.compare_digest((login or "").strip().encode("utf-8"), ADMIN_LOGIN.encode("utf-8"))
    ok_pass = hmac.compare_digest((password or "").encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))
    return ok_login and ok_pass


def sign_value(raw_value: str) -> str:
    payload = raw_value.encode("utf-8")
    sig = hmac.new(SIGNING_SECRET, payload, hashlib.sha256).hexdigest()
    return f"{raw_value}.{sig}"


def verify_signed_value(cookie_val: str) -> str | None:
    if not cookie_val or "." not in cookie_val:
        return None
    raw_value, sig = cookie_val.rsplit(".", 1)
    expected = hmac.new(SIGNING_SECRET, raw_value.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return raw_value
    return None


def check_rate_limit(bucket_key: str, max_attempts: int = 6, window_sec: int = 60, block_sec: int = 300) -> tuple[bool, int]:
    now = time.time()
    blocked_till = _BLOCKED_UNTIL.get(bucket_key, 0.0)
    if now < blocked_till:
        return False, int(blocked_till - now) + 1

    q = _FAILED_ATTEMPTS[bucket_key]
    while q and (now - q[0]) > window_sec:
        q.popleft()

    if len(q) >= max_attempts:
        _BLOCKED_UNTIL[bucket_key] = now + block_sec
        q.clear()
        return False, block_sec

    return True, 0


def record_failed_attempt(bucket_key: str) -> None:
    _FAILED_ATTEMPTS[bucket_key].append(time.time())


def clear_failed_attempts(bucket_key: str) -> None:
    _FAILED_ATTEMPTS.pop(bucket_key, None)
    _BLOCKED_UNTIL.pop(bucket_key, None)


def get_client_ip(headers, client_host: str | None) -> str:
    xff = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    cf_ip = (headers.get("cf-connecting-ip") or "").strip()
    return cf_ip or xff or (client_host or "unknown")
