"""
SRT 취소표 모니터링 & 자동 예약 봇 (CLI)
"""

import logging
import logging.handlers
import random
import signal
import sys
import time
from datetime import datetime

import requests
from SRT import SRT, SRTError, SRTLoginError
from SRT.errors import SRTNetFunnelError

from pathlib import Path

# legacy_cli/ 에서 실행해도 프로젝트 루트의 backend 패키지를 찾도록 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from backend.schemas import MonitorConfig
from backend.srt_logic import (
    available_seat_type,
    fmt_time,
    seat_status_str,
    try_reserve_train,
)

# ── 로깅 설정 ─────────────────────────────────────────────────────

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

_console = logging.StreamHandler()
_console.setFormatter(_formatter)

_LOG_PATH = Path(__file__).resolve().parent / "srt_monitor.log"

_file = logging.handlers.RotatingFileHandler(
    str(_LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file.setFormatter(_formatter)

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
log.addHandler(_console)
log.addHandler(_file)

_NETFUNNEL_BACKOFF_MIN_SECONDS = 15
_NETFUNNEL_BACKOFF_MAX_SECONDS = 40


# ── config.ini → MonitorConfig 변환 ──────────────────────────────

def _build_monitor_config() -> MonitorConfig:
    return MonitorConfig(
        telegram_bot_token=config.TELEGRAM_BOT_TOKEN,
        telegram_chat_id=config.TELEGRAM_CHAT_ID,
        srt_id=config.SRT_ID,
        srt_password=config.SRT_PASSWORD,
        departure_date=config.DEPARTURE_DATE,
        departure_time=config.DEPARTURE_TIME,
        max_departure_time=config.MAX_DEPARTURE_TIME,
        departure_station=config.DEPARTURE_STATION,
        arrival_station=config.ARRIVAL_STATION,
        check_special=config.CHECK_SPECIAL,
        check_normal=config.CHECK_NORMAL,
        adult_count=config.ADULT_COUNT,
        child_count=config.CHILD_COUNT,
        senior_count=config.SENIOR_COUNT,
        disability_1_to_3_count=config.DISABILITY_1_TO_3_COUNT,
        disability_4_to_6_count=config.DISABILITY_4_TO_6_COUNT,
        require_same_row=config.REQUIRE_SAME_ROW,
        require_same_car=config.REQUIRE_SAME_CAR,
        require_adjacent_seats=config.REQUIRE_ADJACENT_SEATS,
        prefer_window_seat=config.PREFER_WINDOW_SEAT,
        require_window_seat=config.REQUIRE_WINDOW_SEAT,
        adjacent_seat_pairs=list(config.ADJACENT_SEAT_PAIRS),
        window_seat_letters=list(config.WINDOW_SEAT_LETTERS),
        auto_reserve=config.AUTO_RESERVE,
        max_reservations=config.MAX_RESERVATIONS,
        auto_cancel_if_seat_mismatch=config.AUTO_CANCEL_IF_SEAT_MISMATCH,
        notify_if_seat_mismatch=config.NOTIFY_IF_SEAT_MISMATCH,
        poll_interval=config.POLL_INTERVAL,
        heartbeat_interval=config.HEARTBEAT_INTERVAL,
    )


# ── 텔레그램 ──────────────────────────────────────────────────────

def send_telegram(cfg: MonitorConfig, message: str) -> None:
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": cfg.telegram_chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("텔레그램 전송 실패: %s", e)


# ── SRT 세션 ──────────────────────────────────────────────────────

class SRTSession:
    """로그인 세션 래퍼 — 세션 만료 시 자동 재로그인."""

    def __init__(self, cfg: MonitorConfig) -> None:
        self._cfg = cfg
        self._client: SRT | None = None

    def get(self) -> SRT:
        if self._client is None:
            self._client = SRT(self._cfg.srt_id, self._cfg.srt_password, verbose=False)
            log.info("SRT 로그인 완료")
        return self._client

    def invalidate(self) -> None:
        self._client = None


# ── 예약 ──────────────────────────────────────────────────────────

def try_reserve(cfg: MonitorConfig, srt: SRT, train) -> bool:
    def emit(level: str, message: str) -> None:
        logger = log.warning if level == "warn" else getattr(log, level, log.info)
        logger(message)

    return try_reserve_train(
        cfg,
        srt,
        train,
        emit=emit,
        send_telegram=lambda message: send_telegram(cfg, message),
        include_srt_link=True,
    )


# ── 메인 루프 ─────────────────────────────────────────────────────

def main() -> None:
    cfg = _build_monitor_config()

    log.info("=" * 50)
    log.info("SRT 취소표 알림 봇 시작")
    log.info("구간: %s → %s", cfg.departure_station, cfg.arrival_station)
    log.info("날짜: %s  자동예약: %s", cfg.departure_date, "ON" if cfg.auto_reserve else "OFF")
    log.info(
        "인원: %d명 / 같은 칸: %s / 같은 줄: %s / 연석: %s / 창가우선: %s / 창가필수: %s",
        cfg.passenger_count,
        "ON" if cfg.require_same_car else "OFF",
        "ON" if cfg.require_same_row else "OFF",
        "ON" if cfg.require_adjacent_seats else "OFF",
        "ON" if cfg.prefer_window_seat else "OFF",
        "ON" if cfg.require_window_seat else "OFF",
    )
    log.info("=" * 50)

    send_telegram(
        cfg,
        f"🚄 <b>SRT 모니터링 시작</b>\n"
        f"구간: {cfg.departure_station} → {cfg.arrival_station}\n"
        f"날짜: {cfg.departure_date[:4]}/{cfg.departure_date[4:6]}/{cfg.departure_date[6:]}\n"
        f"인원: {cfg.passenger_count}명\n"
        f"연석필수: {'ON ✅' if cfg.require_adjacent_seats else 'OFF ❌'}\n"
        f"창가우선: {'ON ✅' if cfg.prefer_window_seat else 'OFF ❌'}\n"
        f"자동예약: {'ON ✅' if cfg.auto_reserve else 'OFF ❌'}",
    )

    session = SRTSession(cfg)
    seen: set[str] = set()
    reserved_count = 0
    error_streak = 0
    netfunnel_retries = 0
    last_heartbeat = time.time()

    def shutdown(sig, frame):
        log.info("종료 신호 수신. 봇을 종료합니다.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    while True:
        if cfg.auto_reserve and reserved_count >= cfg.max_reservations:
            log.info("목표 예약 수(%d) 달성. 종료합니다.", cfg.max_reservations)
            send_telegram(cfg, f"✅ 예약 {reserved_count}건 완료. 봇을 종료합니다.")
            break

        if time.time() - last_heartbeat >= cfg.heartbeat_interval:
            send_telegram(
                cfg,
                f"💓 <b>모니터링 중</b>\n"
                f"구간: {cfg.departure_station} → {cfg.arrival_station}\n"
                f"날짜: {cfg.departure_date[:4]}/{cfg.departure_date[4:6]}/{cfg.departure_date[6:]}",
            )
            last_heartbeat = time.time()

        try:
            srt = session.get()
            trains = srt.search_train(
                dep=cfg.departure_station,
                arr=cfg.arrival_station,
                date=cfg.departure_date,
                time=cfg.departure_time,
                time_limit=cfg.max_departure_time,
                available_only=False,
            )
            log.info("열차 %d편 조회", len(trains))
            error_streak = 0
            netfunnel_retries = 0

            for train in trains:
                dep_hhmmss = train.dep_time[-6:] if len(train.dep_time) > 6 else train.dep_time
                if dep_hhmmss > cfg.max_departure_time:
                    continue
                if available_seat_type(cfg, train) is None:
                    continue

                key = f"{train.train_name}{train.train_number}{train.dep_time}"
                if key in seen:
                    continue

                if cfg.auto_reserve:
                    if try_reserve(cfg, srt, train):
                        seen.add(key)
                        reserved_count += 1
                else:
                    seen.add(key)
                    log.info("취소표 발견: %s %s호", train.train_name, train.train_number)
                    send_telegram(
                        cfg,
                        f"🎉 <b>취소표 발견!</b>\n"
                        f"🚄 {train.train_name} {train.train_number}호\n"
                        f"📍 {train.dep_station_name} {fmt_time(train.dep_time)} "
                        f"→ {train.arr_station_name} {fmt_time(train.arr_time)}\n"
                        f"💺 {seat_status_str(cfg, train)}\n\n"
                        f"👉 <a href='https://etk.srail.kr'>SRT 예매 바로가기</a>",
                    )

        except SRTLoginError as e:
            log.warning("세션 만료, 재로그인 시도: %s", e)
            session.invalidate()
            error_streak += 1
        except SRTNetFunnelError as e:
            netfunnel_retries += 1
            wait = random.randint(
                _NETFUNNEL_BACKOFF_MIN_SECONDS,
                _NETFUNNEL_BACKOFF_MAX_SECONDS,
            )
            if netfunnel_retries >= 3:
                session.invalidate()
                netfunnel_retries = 0
                log.warning(
                    "WAIT_NETFUNNEL: NetFunnel 일시 오류 (3/3). 세션 초기화 후 %d초 뒤 재시도합니다: %r",
                    wait,
                    e,
                )
            else:
                log.warning(
                    "WAIT_NETFUNNEL: NetFunnel 일시 오류 (%d/3). %d초 뒤 재시도합니다: %r",
                    netfunnel_retries,
                    wait,
                    e,
                )
            time.sleep(wait)
            continue
        except requests.exceptions.ConnectionError as e:
            log.warning("네트워크 연결 오류 (자동 재시도): %s", e)
            error_streak += 1
        except SRTError as e:
            log.warning("SRT 오류: %s", e)
            error_streak += 1
        except Exception:
            log.exception("예상치 못한 오류 발생")
            error_streak += 1

        wait = (
            min(cfg.poll_interval * (2**error_streak), 300)
            if error_streak > 0
            else cfg.poll_interval
        )
        if error_streak > 0:
            log.info("연속 오류 %d회 — %d초 후 재시도", error_streak, wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
