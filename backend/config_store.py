import json
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from threading import RLock

from .schemas import MonitorConfig, PublicConfig, SecretsPayload, SecretsStatus, _tomorrow, migrate_public_data

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "web_config.json"
_STORE_LOCK = RLock()
_SECRETS_FIELDS = {"srt_id", "srt_password", "korail_id", "korail_password",
                   "telegram_bot_token", "telegram_chat_id"}


def _read_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("설정 파일은 JSON 객체여야 합니다.")
    return data


def _write_raw(data: dict) -> None:
    """Keep original v1 bytes and atomically replace a complete fsynced file."""
    old = _read_raw()
    if CONFIG_PATH.exists() and old.get("schema_version") != 2:
        backup = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".v1.bak")
        if not backup.exists():
            shutil.copy2(CONFIG_PATH, backup)
    fd, temporary = tempfile.mkstemp(prefix=CONFIG_PATH.name + ".", suffix=".tmp", dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, CONFIG_PATH)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _normalized(data: dict) -> dict:
    data = migrate_public_data(data) if data else {"schema_version": 2}
    value = data.get("departure_date")
    if value and datetime.strptime(str(value), "%Y%m%d").date() < date.today():
        data["departure_date"] = _tomorrow()
    return data


def load_config() -> MonitorConfig:
    with _STORE_LOCK:
        return MonitorConfig(**_normalized(_read_raw()))


def save_config(config: MonitorConfig) -> MonitorConfig:
    with _STORE_LOCK:
        _write_raw(config.model_dump())
    return config


def load_public_config() -> PublicConfig:
    with _STORE_LOCK:
        return PublicConfig(**{k: v for k, v in _normalized(_read_raw()).items() if k not in _SECRETS_FIELDS})


def load_secrets_status() -> SecretsStatus:
    with _STORE_LOCK:
        data = _read_raw()
    return SecretsStatus(**{f"{key}_set": bool(str(data.get(key, "")).strip()) for key in _SECRETS_FIELDS})


def save_public_config(public: PublicConfig) -> PublicConfig:
    with _STORE_LOCK:
        merged = {**_normalized(_read_raw()), **public.model_dump(exclude_unset=True)}
        config = MonitorConfig(**merged)
        _write_raw(config.model_dump())
        return PublicConfig(**{k: v for k, v in config.model_dump().items() if k not in _SECRETS_FIELDS})


def preview_public_config(public: PublicConfig) -> MonitorConfig:
    with _STORE_LOCK:
        return MonitorConfig(**{**_normalized(_read_raw()), **public.model_dump(exclude_unset=True)})


def save_secrets(secrets: SecretsPayload) -> SecretsStatus:
    with _STORE_LOCK:
        data = _normalized(_read_raw())
        for key in _SECRETS_FIELDS:
            value = str(getattr(secrets, key, "") or "")
            if value.strip():
                data[key] = value
        config = MonitorConfig(**data)
        _write_raw(config.model_dump())
        return load_secrets_status()
