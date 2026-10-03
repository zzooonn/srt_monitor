"""
SRT 취소표 알림 봇 — 설정 로더
모든 값은 config.ini 에서 읽습니다. 이 파일을 직접 수정하지 마세요.
"""

import configparser
import sys
from datetime import date, timedelta
from pathlib import Path

_INI = Path(__file__).parent / "config.ini"

if not _INI.exists():
    sys.exit(f"[오류] {_INI} 파일을 찾을 수 없습니다. config.ini 를 만들고 값을 입력하세요.")

_cfg = configparser.ConfigParser()
_cfg.read(_INI, encoding="utf-8")


def _require(section: str, key: str) -> str:
    """누락되거나 플레이스홀더인 경우 즉시 종료."""
    try:
        value = _cfg[section][key].strip()
    except KeyError:
        sys.exit(f"[오류] config.ini 에서 [{section}] {key} 항목을 찾을 수 없습니다.")
    placeholders = {"여기에_텔레그램_봇_토큰_입력", "여기에_채팅_ID_입력", "SRT_아이디_입력", "SRT_비밀번호_입력"}
    if not value or value in placeholders:
        sys.exit(f"[오류] config.ini 의 [{section}] {key} 에 실제 값을 입력해 주세요.")
    return value


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).strftime("%Y%m%d")


def _date_or_tomorrow(section: str, key: str) -> str:
    value = _cfg.get(section, key, fallback="").strip()
    if not value or value.lower() == "auto":
        return _tomorrow()
    return value


# ── [1] 텔레그램 설정 ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = _require("telegram", "bot_token")
TELEGRAM_CHAT_ID   = _require("telegram", "chat_id")

# ── [2] SRT 로그인 정보 ────────────────────────────────────────────
SRT_ID       = _require("srt", "id")
SRT_PASSWORD = _require("srt", "password")

# ── [3] 열차 조건 ──────────────────────────────────────────────────
DEPARTURE_DATE     = _date_or_tomorrow("train", "departure_date")
DEPARTURE_TIME     = _cfg.get("train", "departure_time",     fallback="200000")
MAX_DEPARTURE_TIME = _cfg.get("train", "max_departure_time", fallback="230000")
DEPARTURE_STATION  = _cfg.get("train", "departure_station",  fallback="동탄")
ARRIVAL_STATION    = _cfg.get("train", "arrival_station",    fallback="오송")

# ── [4] 좌석 등급 ──────────────────────────────────────────────────
CHECK_SPECIAL = _cfg.getboolean("seats", "check_special", fallback=True)
CHECK_NORMAL  = _cfg.getboolean("seats", "check_normal",  fallback=True)

# ── [5] 인원 설정 ──────────────────────────────────────────────────
ADULT_COUNT             = _cfg.getint("passengers", "adult_count",             fallback=1)
CHILD_COUNT             = _cfg.getint("passengers", "child_count",             fallback=0)
SENIOR_COUNT            = _cfg.getint("passengers", "senior_count",            fallback=0)
DISABILITY_1_TO_3_COUNT = _cfg.getint("passengers", "disability_1_to_3_count", fallback=0)
DISABILITY_4_TO_6_COUNT = _cfg.getint("passengers", "disability_4_to_6_count", fallback=0)

PASSENGER_COUNT = (
    ADULT_COUNT
    + CHILD_COUNT
    + SENIOR_COUNT
    + DISABILITY_1_TO_3_COUNT
    + DISABILITY_4_TO_6_COUNT
)

# ── [6] 좌석 조건 ──────────────────────────────────────────────────
REQUIRE_SAME_ROW       = _cfg.getboolean("seats", "require_same_row",       fallback=False)
REQUIRE_SAME_CAR       = _cfg.getboolean("seats", "require_same_car",       fallback=False)
REQUIRE_ADJACENT_SEATS = _cfg.getboolean("seats", "require_adjacent_seats", fallback=True)
PREFER_WINDOW_SEAT     = _cfg.getboolean("seats", "prefer_window_seat",     fallback=True)
REQUIRE_WINDOW_SEAT    = _cfg.getboolean("seats", "require_window_seat",    fallback=False)

SEAT_COLUMN_ORDER   = "ABCD"
ADJACENT_SEAT_PAIRS = {"AB", "CD"}
WINDOW_SEAT_LETTERS = {"A", "D"}

# ── [7] 자동 예약 설정 ────────────────────────────────────────────
AUTO_RESERVE                 = _cfg.getboolean("options", "auto_reserve",                 fallback=True)
MAX_RESERVATIONS             = _cfg.getint    ("options", "max_reservations",             fallback=1)
AUTO_CANCEL_IF_SEAT_MISMATCH = _cfg.getboolean("options", "auto_cancel_if_seat_mismatch", fallback=True)
NOTIFY_IF_SEAT_MISMATCH      = _cfg.getboolean("options", "notify_if_seat_mismatch",      fallback=True)

# ── [8] 폴링 간격 (초) ────────────────────────────────────────────
POLL_INTERVAL = _cfg.getint("options", "poll_interval", fallback=30)

# ── [9] 하트비트 간격 (초) ────────────────────────────────────────
HEARTBEAT_INTERVAL = _cfg.getint("options", "heartbeat_interval", fallback=1800)
