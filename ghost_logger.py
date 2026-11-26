from datetime import datetime


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_ok(msg: str):
    print(f"[{_ts()}] [OK] {msg}", flush=True)


def log_warn(msg: str):
    print(f"[{_ts()}] [WARN] {msg}", flush=True)


def log_err(msg: str):
    print(f"[{_ts()}] [ERR] {msg}", flush=True)
