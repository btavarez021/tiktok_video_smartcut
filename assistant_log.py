# assistant_log.py

from typing import List

status_log: List[str] = []
MAX_LOG_ENTRIES = 150   # drastically lower

def log_step(message: str) -> None:
    line = message.strip()
    if not line:
        return

    print(f"[LOG] {line}")
    status_log.append(line)

    # Always keep log small
    if len(status_log) > MAX_LOG_ENTRIES:
        status_log[:] = status_log[-MAX_LOG_ENTRIES:]

def clear_status_log() -> None:
    status_log.clear()
