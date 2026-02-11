# config_store.py
import os, yaml
from typing import Dict, Any, Callable
from threading import Lock
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_SESSION_LOCKS = defaultdict(Lock)


def get_config_path(session_id: str) -> str:
    folder = os.path.join(BASE_DIR, "configs", session_id)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.yml")


def normalize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = cfg or {}
    cfg.setdefault("render", {})
    cfg.setdefault("tts", {})
    cfg.setdefault("cta", {})
    cfg.setdefault("music", {})
    return cfg


def load_config(session_id: str) -> Dict[str, Any]:
    with _SESSION_LOCKS[session_id]:
        path = get_config_path(session_id)
        if not os.path.exists(path):
            return normalize_config({})
        with open(path, "r", encoding="utf-8") as f:
            return normalize_config(yaml.safe_load(f) or {})


def save_config(session_id: str, cfg: Dict[str, Any]) -> None:
    with _SESSION_LOCKS[session_id]:
        path = get_config_path(session_id)
        tmp_path = f"{path}.tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(normalize_config(cfg), f, sort_keys=False, allow_unicode=True)

        os.replace(tmp_path, path)


def update_config(session_id: str, updater: Callable[[Dict[str, Any]], Dict[str, Any]]):
    with _SESSION_LOCKS[session_id]:
        cfg = load_config(session_id)
        cfg = updater(cfg) or cfg
        save_config(session_id, cfg)
        return cfg
