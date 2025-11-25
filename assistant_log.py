# assistant_log.py
from typing import List

status_log: List[str] = []

MAX_LOG_ENTRIES = 150   # <— reduce from 500


def log_step(message: str) -> None:
    line = message.strip()
    if not line:
        return

    print(f"[LOG] {line}")
    status_log.append(line)

    if len(status_log) > MAX_LOG_ENTRIES:
        # keep LAST N entries only
        status_log[:] = status_log[-MAX_LOG_ENTRIES:]


def clear_status_log() -> None:
    status_log.clear()
