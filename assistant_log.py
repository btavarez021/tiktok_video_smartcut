import datetime

status_log = []  # simple in-memory log


def log_step(message: str):
    """Append a status line with timestamp."""
    ts = datetime.datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {message}"
    print(line)
    status_log.append(line)
    # cap log length
    if len(status_log) > 500:
        del status_log[: len(status_log) - 500]


def clear_status_log():
    status_log.clear()
