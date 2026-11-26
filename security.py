import base64
import hashlib
import hmac
import io
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Dict, Deque

from cryptography.fernet import Fernet, InvalidToken
from PIL import Image

ADMIN_LOGIN = os.getenv("GHOSTCORE_ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("GHOSTCORE_ADMIN_PASSWORD", "")
SIGNING_SECRET = os.getenv("GHOSTCORE_SIGNING_SECRET", secrets.token_hex(32)).encode("utf-8")

_RAW_MASTER_KEY = os.getenv("GHOSTCORE_MASTER_KEY", "").strip()
if _RAW_MASTER_KEY:
    try:
        # Поддерживаем как готовый Fernet base64-ключ, так и любую строку/hex
        if len(_RAW_MASTER_KEY) == 44:
            _FERNET = Fernet(_RAW_MASTER_KEY.encode("utf-8"))
        else:
            digest = hashlib.sha256(_RAW_MASTER_KEY.encode("utf-8")).digest()
            _FERNET = Fernet(base64.urlsafe_b64encode(digest))
    except Exception:
        digest = hashlib.sha256(_RAW_MASTER_KEY.encode("utf-8")).digest()
        _FERNET = Fernet(base64.urlsafe_b64encode(digest))
else:
    _FERNET = Fernet(Fernet.generate_key())

_ENC_PREFIX = "enc:v1:"

_FAILED_ATTEMPTS: Dict[str, Deque[float]] = defaultdict(deque)
_BLOCKED_UNTIL: Dict[str, float] = {}


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext
    token = _FERNET.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    if not ciphertext.startswith(_ENC_PREFIX):
        return ciphertext
    token = ciphertext[len(_ENC_PREFIX):].encode("utf-8")
    try:
        return _FERNET.decrypt(token).decode("utf-8")
    except InvalidToken:
        return ""


def verify_admin_credentials(login: str, password: str) -> bool:
    if not ADMIN_PASSWORD:
        return False
    ok_login = hmac.compare_digest((login or "").strip().encode("utf-8"), ADMIN_LOGIN.encode("utf-8"))
    ok_pass = hmac.compare_digest((password or "").encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))
    return ok_login and ok_pass


def sign_value(raw_value: str) -> str:
    payload = raw_value.encode("utf-8")
    sig = hmac.new(SIGNING_SECRET, payload, hashlib.sha256).hexdigest()
    return f"{raw_value}.{sig}"


def verify_signed_value(cookie_val: str | None) -> str | None:
    if not cookie_val or "." not in cookie_val:
        return None
    raw_value, sig = cookie_val.rsplit(".", 1)
    expected = hmac.new(SIGNING_SECRET, raw_value.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return raw_value
    return None


def check_rate_limit(
    bucket_key: str,
    max_attempts: int = 6,
    window_sec: int = 60,
    block_sec: int = 300,
) -> tuple[bool, int]:
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


def validate_image_bytes(raw_bytes: bytes, max_bytes: int = 3 * 1024 * 1024) -> str | None:
    if not raw_bytes or len(raw_bytes) > max_bytes:
        return None
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.verify()
            fmt = (img.format or "").upper()
        mapping = {
            "JPEG": ".jpg",
            "PNG": ".png",
            "GIF": ".gif",
            "WEBP": ".webp",
        }
        return mapping.get(fmt)
    except Exception:
        return None
