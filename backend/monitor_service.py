import asyncio
import logging
import logging.handlers
import random
import time
from collections.abc import Callable
from collections import deque
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))
from pathlib import Path
from typing import Any

import requests
from SRT import SRT, SRTError, SRTLoginError
from SRT.errors import SRTNetFunnelError

from .reservation_coordinator import AttemptOutcome, ReservationCoordinator
from .schemas import EventItem, MonitorConfig, ServicePhase, ServiceStatus
from .stations import supports_srt_route
from .telegram_errors import telegram_error_summary
from .srt_logic import (
    available_seat_type,
    seat_status_str,
    try_reserve_train,
)


_LOG_PATH = Path(__file__).resolve().parents[1] / "srt_monitor.log"
_SRT_HTTP_TIMEOUT_SECONDS = 20
_SRT_CALL_TIMEOUT_SECONDS = 60
_NETFUNNEL_BACKOFF_MIN_SECONDS = 15
_NETFUNNEL_BACKOFF_MAX_SECONDS = 40

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_file_handler = logging.handlers.RotatingFileHandler(
    str(_LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)

log = logging.getLogger("srt_monitor_web")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(_file_handler)


class SrtCallTimeout(RuntimeError):
    pass


def _exc_str(exc: BaseException) -> str:
    """SRT 라이브러리 일부 예외의 __str__이 문자열이 아닌 객체를 반환하는 버그를 우회."""
    try:
        s = str(exc)
        if isinstance(s, str):
            return s
    except Exception:
        pass
    try:
        return repr(exc)
    except Exception:
        return type(exc).__name__


def _patch_requests_default_timeout() -> None:
    original_request = requests.sessions.Session.request
    if getattr(original_request, "_srt_monitor_timeout_patch", False):
        return

    def request_with_default_timeout(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        kwargs.setdefault("timeout", _SRT_HTTP_TIMEOUT_SECONDS)
        return original_request(self, method, url, **kwargs)

    request_with_default_timeout._srt_monitor_timeout_patch = True  # type: ignore[attr-defined]
    requests.sessions.Session.request = request_with_default_timeout


_patch_requests_default_timeout()


class MonitorService:
    def __init__(self, coordinator: ReservationCoordinator | None = None) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._config: MonitorConfig | None = None
        self._events: deque[EventItem] = deque(maxlen=300)
        self._reserved_count = 0
        self._started_at: str | None = None
        self._last_checked_at: str | None = None
        self._last_message = "대기 중"
        self._phase = ServicePhase.OFF
        self._error: str | None = None
        self._coordinator = coordinator or ReservationCoordinator()

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            running=self.running,
            phase=self._phase,
            reserved_count=self._reserved_count,
            last_message=self._last_message,
            started_at=self._started_at,
            error=self._error,
            last_checked_at=self._last_checked_at,
        )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def recent_events(self) -> list[EventItem]:
        return list(self._events)

    async def start(self, config: MonitorConfig) -> ServiceStatus:
        if self.running:
            return self.status()
        if not supports_srt_route(config.departure_station, config.arrival_station):
            self._emit(
                "info",
                f"SRT 미지원 구간이라 SRT 감시는 건너뜁니다: "
                f"{config.departure_station} → {config.arrival_station}",
            )
            return self.status()
        if not config.srt_id or not config.srt_password:
            self._emit("warn", "SRT 계정 정보가 없어 SRT 모니터링을 시작하지 않았습니다.")
            return self.status()
        self._config = config
        self._phase = ServicePhase.SEARCHING
        self._error = None
        self._last_checked_at = None
        self._reserved_count = 0
        self._started_at = datetime.now(_KST).isoformat(timespec="seconds")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(config, self._stop_event))
        self._emit("info", "모니터링을 시작했습니다.")
        return self.status()

    async def stop(self) -> ServiceStatus:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                self._emit("warn", "모니터링 작업 취소 시간이 초과되었습니다.")
        self._task = None
        self._stop_event = None
        self._phase = ServicePhase.STOPPED
        self._emit("info", "모니터링을 중지했습니다.")
        return self.status()

    def _emit(self, level: str, message: str) -> None:
        item = EventItem(
            level=level,
            message=message,
            timestamp=datetime.now(_KST).isoformat(timespec="seconds"),
            service="srt",
        )
        self._events.append(item)
        self._last_message = message
        getattr(log, "warning" if level == "warn" else level, log.info)(message)

    def _send_telegram(self, config: MonitorConfig, message: str) -> None:
        if not config.telegram_bot_token or not config.telegram_chat_id:
            return
        url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": config.telegram_chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            ).raise_for_status()
        except Exception as exc:
            self._emit("warn", f"텔레그램 전송 실패: {telegram_error_summary(exc)}")

    def _try_reserve(self, config: MonitorConfig, srt: SRT, train: Any) -> bool:
        return try_reserve_train(
            config,
            srt,
            train,
            emit=self._emit,
            send_telegram=lambda message: self._send_telegram(config, message),
        )

    async def _sleep_or_stop(self, stop_event: asyncio.Event, seconds: int) -> bool:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run_srt_call(
        self,
        label: str,
        func: Callable[[], Any],
        timeout: int = _SRT_CALL_TIMEOUT_SECONDS,
    ) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(func), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise SrtCallTimeout(f"{label} 응답 시간 초과 (제한 {timeout}초): {_exc_str(exc)}") from exc

    def _search_train(self, session: Any, config: MonitorConfig):
        try:
            return session.search_train(
                dep=config.departure_station, arr=config.arrival_station,
                date=config.departure_date, time=config.departure_time,
                time_limit=config.max_departure_time, available_only=False,
            )
        finally:
            self._last_checked_at = datetime.now(_KST).isoformat(timespec="seconds")

    async def _run(self, config: MonitorConfig, stop_event: asyncio.Event) -> None:
        await self._run_srt_call(
            "텔레그램 시작 알림 전송",
            lambda: self._send_telegram(
                config,
                f"🚄 <b>SRT 모니터링 시작</b>\n"
                f"구간: {config.departure_station} → {config.arrival_station}\n"
                f"날짜: {config.departure_date[:4]}/{config.departure_date[4:6]}/{config.departure_date[6:]}\n"
                f"인원: {config.passenger_count}명\n"
                f"연석필수: {'ON ✅' if config.require_adjacent_seats else 'OFF ❌'}\n"
                f"자동예약: {'ON ✅' if config.auto_reserve else 'OFF ❌'}",
            ),
            timeout=15,
        )

        session: SRT | None = None
        seen: set[str] = set()
        error_streak = 0
        netfunnel_retries = 0
        last_heartbeat = time.time()

        try:
            while not stop_event.is_set():
                if config.auto_reserve and self._coordinator.goal_reached:
                    self._emit("info", f"목표 예약 수 {config.max_reservations}건 달성")
                    self._phase = ServicePhase.GOAL_REACHED
                    break

                if time.time() - last_heartbeat >= config.heartbeat_interval:
                    await self._run_srt_call(
                        "텔레그램 상태 알림 전송",
                        lambda: self._send_telegram(
                            config,
                            f"💓 <b>모니터링 중</b>\n"
                            f"구간: {config.departure_station} → {config.arrival_station}\n"
                            f"날짜: {config.departure_date[:4]}/{config.departure_date[4:6]}/{config.departure_date[6:]}",
                        ),
                        timeout=15,
                    )
                    last_heartbeat = time.time()

                try:
                    if session is None:
                        self._emit("info", "SRT 로그인 시도 중")
                        session = await self._run_srt_call(
                            "SRT 로그인",
                            lambda: SRT(config.srt_id, config.srt_password, verbose=False),
                            timeout=30,
                        )
                        self._emit("info", "SRT 로그인 완료")

                    self._phase = ServicePhase.SEARCHING
                    self._emit("info", "열차 조회 요청 중")
                    trains = await self._run_srt_call(
                        "열차 조회",
                        lambda: self._search_train(session, config),
                        timeout=45,
                    )
                    self._emit("info", f"열차 {len(trains)}편 조회")
                    error_streak = 0
                    netfunnel_retries = 0

                    for train in trains:
                        dep_hhmmss = train.dep_time[-6:] if len(train.dep_time) > 6 else train.dep_time
                        if dep_hhmmss > config.max_departure_time:
                            continue
                        if available_seat_type(config, train) is None:
                            continue

                        key = f"{train.train_name}{train.train_number}{train.dep_time}"
                        if key in seen:
                            continue

                        if config.auto_reserve:
                            attempt = await self._coordinator.try_begin_attempt("srt")
                            if attempt is None:
                                break
                            self._phase = ServicePhase.RESERVING
                            self._emit("info", f"예약 시도 중: {train.train_name} {train.train_number}호")
                            try:
                                result = await self._run_srt_call(
                                    "예약 시도",
                                    lambda train=train, session=session: self._try_reserve(
                                        config, session, train
                                    ),
                                    timeout=60,
                                )
                            except SrtCallTimeout as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                seen.add(key)
                                session = None
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "예약 결과를 확인할 수 없어 SRT·KTX 자동 예약을 보류했습니다. "
                                    f"SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요. ({exc})",
                                )
                                break
                            except asyncio.CancelledError:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "예약 요청 도중 감시가 취소되어 결과가 불확실합니다. "
                                    "SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요.",
                                )
                                raise
                            except Exception as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "error",
                                    f"예약 시도 중 예상하지 못한 오류: {exc}. "
                                    "SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요.",
                                )
                                break
                            else:
                                await self._coordinator.finish_attempt(attempt, result.outcome)
                                self._phase = (
                                    ServicePhase.NEEDS_CONFIRMATION if result.outcome is AttemptOutcome.UNCERTAIN
                                    else ServicePhase.GOAL_REACHED if self._coordinator.goal_reached
                                    else ServicePhase.SEARCHING
                                )
                                if result.outcome is AttemptOutcome.RESERVED:
                                    seen.add(key)
                                    self._reserved_count += 1
                                elif result.outcome is AttemptOutcome.NOT_RESERVED:
                                    seen.add(key)
                        else:
                            seen.add(key)
                            msg = f"취소표 발견: {train.train_name} {train.train_number}호"
                            self._emit("info", msg)
                            await self._run_srt_call(
                                "텔레그램 취소표 알림 전송",
                                lambda msg=msg: self._send_telegram(config, f"🎉 <b>{msg}</b>"),
                                timeout=15,
                            )

                except SrtCallTimeout as exc:
                    session = None
                    error_streak += 1
                    self._emit("warn", f"{_exc_str(exc)} 세션을 초기화하고 다시 시도합니다.")
                except SRTLoginError as exc:
                    session = None
                    error_streak += 1
                    self._emit("warn", f"세션 만료 또는 로그인 오류: {_exc_str(exc)}")
                except SRTNetFunnelError as exc:
                    netfunnel_retries += 1
                    wait = random.randint(
                        _NETFUNNEL_BACKOFF_MIN_SECONDS,
                        _NETFUNNEL_BACKOFF_MAX_SECONDS,
                    )
                    if netfunnel_retries >= 3:
                        session = None
                        netfunnel_retries = 0
                        self._emit(
                            "warn",
                            f"WAIT_NETFUNNEL: NetFunnel 일시 오류 (3/3). "
                            f"세션 초기화 후 {wait}초 뒤 재시도합니다: {_exc_str(exc)}",
                        )
                    else:
                        self._emit(
                            "warn",
                            f"WAIT_NETFUNNEL: NetFunnel 일시 오류 ({netfunnel_retries}/3). "
                            f"{wait}초 뒤 재시도합니다: {_exc_str(exc)}",
                        )
                    if await self._sleep_or_stop(stop_event, wait):
                        break
                    continue
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                    error_streak += 1
                    self._emit("warn", f"네트워크 연결 오류: {_exc_str(exc)}")
                except SRTError as exc:
                    error_streak += 1
                    self._emit("warn", f"SRT 오류: {_exc_str(exc)}")
                except asyncio.CancelledError:
                    self._emit("warn", "모니터링 루프가 외부 취소 요청을 받았습니다")
                    raise
                except Exception as exc:
                    error_streak += 1
                    self._emit("error", f"예상하지 못한 오류: {type(exc).__name__}: {_exc_str(exc)}")

                if error_streak and self._coordinator.blocked_reason != "uncertain":
                    self._phase = ServicePhase.ERROR
                    self._error = self._last_message
                elif not error_streak:
                    self._error = None
                wait = (
                    min(config.poll_interval * (2**error_streak), 300)
                    if error_streak
                    else config.poll_interval
                )
                if await self._sleep_or_stop(stop_event, wait):
                    break

        except asyncio.CancelledError:
            self._emit("warn", "외부 취소 요청으로 루프 종료")
        except Exception as exc:
            self._emit("error", f"루프 예외 누출: {type(exc).__name__}: {_exc_str(exc)}")
        finally:
            self._emit("info", "모니터링 루프가 종료되었습니다.")
