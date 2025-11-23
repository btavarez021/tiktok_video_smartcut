# assistant_log.py

status_log: list[str] = []

def log_step(msg: str) -> None:
    print(msg)
    status_log.append(msg)

def clear_status_log() -> None:
    status_log.clear()
