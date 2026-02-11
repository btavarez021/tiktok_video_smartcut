# config_store.py
import os, yaml
from typing import Dict, Any, Callable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_config_path(session_id: str) -> str:
    folder = os.path.join(BASE_DIR, "configs", session_id)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.yml")

def load_config(session_id: str) -> Dict[str, Any]:
    path = get_config_path(session_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_config(session_id: str, cfg: Dict[str, Any]) -> None:
    path = get_config_path(session_id)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

def normalize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = cfg or {}
    cfg.setdefault("render", {})
    cfg.setdefault("tts", {})
    cfg.setdefault("cta", {})
    cfg.setdefault("music", {})
    return cfg

def update_config(session_id: str, updater: Callable[[Dict[str, Any]], Dict[str, Any]]):
    cfg = normalize_config(load_config(session_id))
    cfg = updater(cfg) or cfg
    cfg = normalize_config(cfg)
    save_config(session_id, cfg)
    return cfg