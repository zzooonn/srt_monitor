import json
import pytest
from pathlib import Path

from backend.config_store import (
    load_public_config,
    load_secrets_status,
    save_public_config,
    save_secrets,
)
from backend.schemas import PublicConfig, SecretsPayload


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    config_file = tmp_path / "web_config.json"
    monkeypatch.setattr("backend.config_store.CONFIG_PATH", config_file)
    return config_file


def test_load_public_config_no_file(tmp_config):
    result = load_public_config()
    assert isinstance(result, PublicConfig)
    assert result.departure_station == "오송"


def test_save_and_load_public_config(tmp_config):
    config = PublicConfig(departure_station="수서", arrival_station="동탄")
    save_public_config(config)
    loaded = load_public_config()
    assert loaded.departure_station == "수서"
    assert loaded.arrival_station == "동탄"


def test_save_public_config_preserves_secrets(tmp_config):
    tmp_config.write_text(
        json.dumps({
            "srt_id": "myid",
            "srt_password": "mypw",
            "telegram_bot_token": "token123",
            "telegram_chat_id": "chat456",
            "departure_station": "수서",
            "arrival_station": "동탄",
        }),
        encoding="utf-8",
    )
    public = PublicConfig(departure_station="오송", arrival_station="수서")
    save_public_config(public)

    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["srt_id"] == "myid"
    assert data["srt_password"] == "mypw"
    assert data["telegram_bot_token"] == "token123"
    assert data["departure_station"] == "오송"


def test_save_secrets_overwrites_nonempty(tmp_config):
    tmp_config.write_text(
        json.dumps({"srt_id": "old_id", "srt_password": "old_pw"}),
        encoding="utf-8",
    )
    save_secrets(SecretsPayload(srt_id="new_id", srt_password="new_pw"))

    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["srt_id"] == "new_id"
    assert data["srt_password"] == "new_pw"


def test_save_secrets_saves_korail_id(tmp_config):
    save_secrets(SecretsPayload(korail_id="kid", korail_password="kpw"))

    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["korail_id"] == "kid"
    assert data["korail_password"] == "kpw"


def test_save_secrets_ignores_empty_fields(tmp_config):
    tmp_config.write_text(
        json.dumps({"srt_id": "existing_id", "srt_password": "existing_pw"}),
        encoding="utf-8",
    )
    save_secrets(SecretsPayload(srt_id="", srt_password="new_pw"))

    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["srt_id"] == "existing_id"
    assert data["srt_password"] == "new_pw"


def test_load_secrets_status_mixed(tmp_config):
    tmp_config.write_text(
        json.dumps({
            "srt_id": "myid",
            "srt_password": "",
            "telegram_bot_token": "token",
            "telegram_chat_id": "",
        }),
        encoding="utf-8",
    )
    status = load_secrets_status()
    assert status.srt_id_set is True
    assert status.srt_password_set is False
    assert status.telegram_bot_token_set is True
    assert status.telegram_chat_id_set is False


def test_load_secrets_status_includes_korail(tmp_config):
    tmp_config.write_text(
        json.dumps({
            "srt_id": "s",
            "srt_password": "p",
            "korail_id": "k",
            "korail_password": "",
        }),
        encoding="utf-8",
    )
    status = load_secrets_status()
    assert status.korail_id_set is True
    assert status.korail_password_set is False


def test_load_secrets_status_no_file(tmp_config):
    status = load_secrets_status()
    assert status.srt_id_set is False
    assert status.korail_id_set is False
    assert status.telegram_bot_token_set is False


def test_save_public_config_preserves_korail_secrets(tmp_config):
    tmp_config.write_text(
        json.dumps({
            "korail_id": "k",
            "korail_password": "p",
            "departure_station": "수서",
            "arrival_station": "동탄",
        }),
        encoding="utf-8",
    )
    save_public_config(PublicConfig(departure_station="오송", arrival_station="수서"))

    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["korail_id"] == "k"
    assert data["korail_password"] == "p"
