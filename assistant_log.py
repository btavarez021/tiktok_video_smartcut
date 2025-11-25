# assistant_log.py

from typing import List

# In-memory rolling log for UI
status_log: List[str] = []


def log_step(message: str) -> None:
    """
    Append a message to the rolling status log and also print it
    (so it shows up in Render logs).
    """
    line = message.strip()
    if not line:
        return

    print(f"[LOG] {line}")
    status_log.append(line)

    # Keep log from growing unbounded
    if len(status_log) > 500:
        del status_log[: len(status_log) - 500]


def clear_status_log() -> None:
    """Clear the status log (used at the start of long operations)."""
    status_log.clear()
