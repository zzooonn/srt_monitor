import asyncio
import logging
import logging.handlers
import random
import time
import uuid
from dataclasses import dataclass, field
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:
    requests = None  # type: ignore[assignment]

from .ktx_logic import available_seat_type_ktx, seat_status_str_ktx, try_reserve_train_ktx
from .reservation_coordinator import AttemptOutcome, ReservationCoordinator
from .telegram_errors import telegram_error_summary

try:
    from .schemas import EventItem, MonitorConfig, ServicePhase, ServiceStatus
except Exception:
    MonitorConfig = Any

    class ServicePhase:
        OFF = "off"
        SEARCHING = "searching"
        RESERVING = "reserving"
        NEEDS_CONFIRMATION = "needs_confirmation"
        GOAL_REACHED = "goal_reached"
        ERROR = "error"
        STOPPED = "stopped"

    @dataclass
    class ServiceStatus:
        running: bool
        phase: str
        reserved_count: int
        last_message: str
        started_at: str | None = None
        error: str | None = None

    @dataclass
    class EventItem:
        level: str
        message: str
        timestamp: str
        service: str = "ktx"
        id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

try:
    from .stations import supports_ktx_route
except Exception:
    def supports_ktx_route(departure: str, arrival: str) -> bool:
        return True


_KST = timezone(timedelta(hours=9))
_LOG_PATH = Path(__file__).resolve().parents[1] / "srt_monitor.log"
_KTX_HTTP_TIMEOUT_SECONDS = 20
_KTX_CALL_TIMEOUT_SECONDS = 60
_NETFUNNEL_BACKOFF_MIN_SECONDS = 15
_NETFUNNEL_BACKOFF_MAX_SECONDS = 40

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_file_handler = logging.handlers.RotatingFileHandler(
    str(_LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)

log = logging.getLogger("ktx_monitor_web")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(_file_handler)


class KtxCallTimeout(RuntimeError):
    pass


def _exc_str(exc: BaseException) -> str:
    try:
        value = str(exc)
        if isinstance(value, str):
            return value
    except Exception:
        pass
    try:
        return repr(exc)
    except Exception:
        return type(exc).__name__


def _patch_requests_default_timeout() -> None:
    if requests is None:
        return
    original_request = requests.sessions.Session.request
    if getattr(original_request, "_ktx_monitor_timeout_patch", False):
        return

    def request_with_default_timeout(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Any:
        kwargs.setdefault("timeout", _KTX_HTTP_TIMEOUT_SECONDS)
        return original_request(self, method, url, **kwargs)

    request_with_default_timeout._ktx_monitor_timeout_patch = True  # type: ignore[attr-defined]
    requests.sessions.Session.request = request_with_default_timeout


def _cfg(config: MonitorConfig, *names: str, default: Any = "") -> Any:
    for name in names:
        if hasattr(config, name):
            value = getattr(config, name)
            if value not in (None, ""):
                return value
    return default


def _train_attr(train: Any, *names: str, default: str = "") -> str:
    for name in names:
        value = getattr(train, name, None)
        if value is not None:
            return str(value)
    return default


def is_high_speed_train(train: Any) -> bool:
    name = _train_attr(train, "train_type_name", "train_name", "name").upper()
    if name:
        return name.startswith(("KTX", "SRT"))
    # These response codes are documented in korail2's Train model.
    return _train_attr(train, "train_type") in {"00", "07"}


def _looks_like(exc: BaseException, *needles: str) -> bool:
    haystacks = [type(exc).__name__, _exc_str(exc)]
    return any(needle.lower() in haystack.lower() for needle in needles for haystack in haystacks)


def _is_korail_macro_error(exc: BaseException) -> bool:
    return _looks_like(exc, "MACRO ERROR", "최신 버전", "업데이트한 뒤", "안정적인 환경")


def _network_exceptions() -> tuple[type[BaseException], ...]:
    if requests is None:
        return (ConnectionError, TimeoutError)
    return (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


def _load_korail2() -> tuple[Any, Any, Any]:
    import korail2  # type: ignore[import-not-found]

    return (
        getattr(korail2, "Korail"),
        getattr(korail2, "TrainType", None),
        getattr(korail2, "KorailError", Exception),
    )


_patch_requests_default_timeout()


class KtxMonitorService:
    def __init__(self, coordinator: "ReservationCoordinator | None" = None) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._config: MonitorConfig | None = None
        self._events: deque[EventItem] = deque(maxlen=300)
        self._reserved_count = 0
        self._started_at: str | None = None
        self._last_checked_at: str | None = None
        self._last_message = "[KTX] 대기 중"
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
        if not getattr(config, "monitor_ktx", False):
            return self.status()
        dep = _cfg(config, "ktx_departure_station", "departure_station")
        arr = _cfg(config, "ktx_arrival_station", "arrival_station")
        if not supports_ktx_route(dep, arr):
            self._emit("info", f"[KTX] 미지원 구간이라 KTX 감시는 건너뜁니다: {dep} → {arr}")
            return self.status()
        if not config.korail_id or not config.korail_password:
            self._emit("warn", "[KTX] Korail 계정 정보가 없어 KTX 모니터링을 시작하지 않았습니다.")
            return self.status()
        self._config = config
        self._phase = ServicePhase.SEARCHING
        self._error = None
        self._last_checked_at = None
        self._reserved_count = 0
        self._started_at = datetime.now(_KST).isoformat(timespec="seconds")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(config, self._stop_event))
        self._emit("info", "[KTX] 모니터링을 시작했습니다.")
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
                self._emit("warn", "[KTX] 모니터링 작업 취소 시간이 초과되었습니다.")
        self._task = None
        self._stop_event = None
        self._phase = ServicePhase.STOPPED
        self._emit("info", "[KTX] 모니터링을 중지했습니다.")
        return self.status()

    def _emit(self, level: str, message: str) -> None:
        item = EventItem(
            level=level,
            message=message,
            timestamp=datetime.now(_KST).isoformat(timespec="seconds"),
            service="ktx",
        )
        self._events.append(item)
        self._last_message = message
        if level == "error":
            self._error = message
            self._phase = ServicePhase.ERROR
        getattr(log, "warning" if level == "warn" else level, log.info)(message)

    def _send_telegram(self, config: MonitorConfig, message: str) -> None:
        token = _cfg(config, "ktx_telegram_bot_token", "telegram_bot_token")
        chat_id = _cfg(config, "ktx_telegram_chat_id", "telegram_chat_id")
        if not token or not chat_id:
            return
        if requests is None:
            self._emit("warn", "[KTX] requests 라이브러리가 없어 텔레그램을 전송할 수 없습니다.")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            ).raise_for_status()
        except Exception as exc:
            self._emit("warn", f"[KTX] 텔레그램 전송 실패: {telegram_error_summary(exc)}")

    def _try_reserve(self, config: MonitorConfig, ktx: Any, train: Any) -> bool:
        return try_reserve_train_ktx(
            config,
            ktx,
            train,
            emit=self._emit,
            send_telegram=lambda message: self._send_telegram(config, message),
            include_ktx_link=True,
        )

    async def _sleep_or_stop(self, stop_event: asyncio.Event, seconds: int) -> bool:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run_ktx_call(
        self,
        label: str,
        func: Callable[[], Any],
        timeout: int = _KTX_CALL_TIMEOUT_SECONDS,
    ) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(func), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise KtxCallTimeout(f"[KTX] {label} 응답 시간 초과 (제한 {timeout}초): {_exc_str(exc)}") from exc

    def _search_train(self, session: Any, config: MonitorConfig, train_type: Any) -> list[Any]:
        kwargs: dict[str, Any] = {
            "dep": _cfg(config, "ktx_departure_station", "departure_station"),
            "arr": _cfg(config, "ktx_arrival_station", "arrival_station"),
            "date": _cfg(config, "ktx_departure_date", "departure_date"),
            "time": _cfg(config, "ktx_departure_time", "departure_time"),
        }
        if train_type is not None:
            # Unified searches may return SRT as well as KTX; inspect every result.
            selector = getattr(train_type, "ALL", getattr(train_type, "KTX", None))
            if selector is not None:
                kwargs["train_type"] = selector

        attempts = (
            kwargs,
            {
                "dep": kwargs["dep"],
                "arr": kwargs["arr"],
                "date": kwargs["date"],
                "time": kwargs["time"],
            },
        )
        last_type_error: TypeError | None = None
        for attempt in attempts:
            try:
                trains = list(session.search_train(**attempt))
                return [train for train in trains if is_high_speed_train(train)]
            except TypeError as exc:
                last_type_error = exc
            finally:
                self._last_checked_at = datetime.now(_KST).isoformat(timespec="seconds")
        if last_type_error is not None:
            raise last_type_error
        return []

    async def _run(self, config: MonitorConfig, stop_event: asyncio.Event) -> None:
        dep = _cfg(config, "ktx_departure_station", "departure_station")
        arr = _cfg(config, "ktx_arrival_station", "arrival_station")
        date = _cfg(config, "ktx_departure_date", "departure_date")

        await self._run_ktx_call(
            "텔레그램 시작 알림 전송",
            lambda: self._send_telegram(
                config,
                f"🚄 <b>KTX 모니터링 시작</b>\n"
                f"구간: {dep} → {arr}\n"
                f"날짜: {date[:4]}/{date[4:6]}/{date[6:]}\n"
                f"인원: {config.passenger_count}명\n"
                f"연석필수: {'ON ✅' if config.require_adjacent_seats else 'OFF ❌'}\n"
                f"자동예약: {'ON ✅' if config.auto_reserve else 'OFF ❌'}",
            ),
            timeout=15,
        )

        session: Any | None = None
        train_type: Any | None = None
        korail_error: type[BaseException] = Exception
        seen: set[str] = set()
        error_streak = 0
        netfunnel_retries = 0
        last_heartbeat = time.time()

        try:
            while not stop_event.is_set():
                if config.auto_reserve and self._coordinator.goal_reached:
                    self._emit("info", f"[KTX] 목표 예약 수 {config.max_reservations}건 달성")
                    self._phase = ServicePhase.GOAL_REACHED
                    break

                if time.time() - last_heartbeat >= config.heartbeat_interval:
                    await self._run_ktx_call(
                        "텔레그램 상태 알림 전송",
                        lambda: self._send_telegram(
                            config,
                            f"📡 <b>KTX 모니터링 중</b>\n"
                            f"구간: {dep} → {arr}\n"
                            f"날짜: {date[:4]}/{date[4:6]}/{date[6:]}",
                        ),
                        timeout=15,
                    )
                    last_heartbeat = time.time()

                try:
                    if session is None:
                        self._emit("info", "[KTX] 코레일 로그인 시도 중")
                        Korail, train_type, korail_error = await self._run_ktx_call(
                            "코레일 모듈 로드",
                            _load_korail2,
                            timeout=10,
                        )
                        session = await self._run_ktx_call(
                            "코레일 로그인",
                            lambda: Korail(
                                config.korail_id,
                                config.korail_password,
                            ),
                            timeout=30,
                        )
                        self._emit("info", "[KTX] 코레일 로그인 완료")

                    self._phase = ServicePhase.SEARCHING
                    self._emit("info", "[KTX] 열차 조회 요청 중")
                    trains = await self._run_ktx_call(
                        "열차 조회",
                        lambda: self._search_train(session, config, train_type),
                        timeout=45,
                    )
                    max_departure_time = _cfg(
                        config, "ktx_max_departure_time", "max_departure_time"
                    )
                    matching_trains: list[Any] = []
                    for train in trains:
                        dep_time = _train_attr(train, "dep_time", "departure_time")
                        dep_hhmmss = dep_time[-6:] if len(dep_time) > 6 else dep_time
                        if dep_hhmmss and dep_hhmmss > max_departure_time:
                            continue
                        if available_seat_type_ktx(config, train) is None:
                            continue
                        matching_trains.append(train)

                    self._emit(
                        "info",
                        f"[KTX] 코레일 반환 {len(trains)}건 · 조건 일치 {len(matching_trains)}건",
                    )
                    error_streak = 0
                    self._error = None
                    netfunnel_retries = 0

                    for train in matching_trains:
                        dep_time = _train_attr(train, "dep_time", "departure_time")

                        key = "|".join(
                            [
                                _train_attr(train, "train_name", "name", default="KTX"),
                                _train_attr(train, "train_number", "train_no", "number"),
                                dep_time,
                            ]
                        )
                        if key in seen:
                            continue

                        if config.auto_reserve:
                            attempt = await self._coordinator.try_begin_attempt("ktx")
                            if attempt is None:
                                break
                            self._phase = ServicePhase.RESERVING
                            train_name = _train_attr(train, "train_name", "name", default="KTX")
                            train_number = _train_attr(train, "train_number", "train_no", "number")
                            self._emit("info", f"[KTX] 예약 시도 중: {train_name} {train_number}호")
                            try:
                                result = await self._run_ktx_call(
                                    "예약 시도",
                                    lambda train=train, session=session: self._try_reserve(
                                        config, session, train
                                    ),
                                    timeout=60,
                                )
                            except KtxCallTimeout as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                seen.add(key)
                                session = None
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "예약 결과를 확인할 수 없어 자동 예약을 보류했습니다. "
                                    f"코레일+에서 예약 내역을 확인해주세요. ({exc})",
                                )
                                break
                            except asyncio.CancelledError:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "[KTX] 예약 요청 도중 감시가 취소되어 결과가 불확실합니다. "
                                    "코레일+에서 예약 내역을 확인해주세요.",
                                )
                                raise
                            except Exception as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "error",
                                    f"[KTX] 예약 시도 중 예상하지 못한 오류: {exc}. "
                                    "코레일+에서 예약 내역을 확인해주세요.",
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
                            train_name = _train_attr(train, "train_name", "name", default="KTX")
                            train_number = _train_attr(train, "train_number", "train_no", "number")
                            msg = f"[KTX] 취소표 발견: {train_name} {train_number}호"
                            self._emit("info", msg)
                            await self._run_ktx_call(
                                "텔레그램 취소표 알림 전송",
                                lambda msg=msg, train=train: self._send_telegram(
                                    config,
                                    f"🎉 <b>{msg}</b>\n💺 {seat_status_str_ktx(config, train)}",
                                ),
                                timeout=15,
                            )

                except KtxCallTimeout as exc:
                    session = None
                    error_streak += 1
                    self._emit("warn", f"{_exc_str(exc)} 세션을 초기화하고 다시 시도합니다.")
                except ModuleNotFoundError as exc:
                    self._emit("error", f"[KTX] korail2 라이브러리를 찾을 수 없습니다: {_exc_str(exc)}")
                    break
                except korail_error as exc:
                    if _is_korail_macro_error(exc):
                        self._emit(
                            "error",
                            "[KTX] 코레일이 현재 korail2 요청을 구버전 앱/자동화 요청으로 차단했습니다. "
                            "코레일 연결 감시를 중지합니다. 연결 설정을 확인해 주세요.",
                        )
                        break
                    elif _looks_like(exc, "NoResults", "no results", "조회 결과", "없습니다"):
                        error_streak = 0
                        self._emit("info", f"[KTX] 조회 결과 없음: {_exc_str(exc)}")
                    elif _looks_like(exc, "SoldOut", "sold out", "매진"):
                        error_streak = 0
                        self._emit("info", f"[KTX] 매진: {_exc_str(exc)}")
                    elif _looks_like(exc, "login", "session", "로그인", "세션", "인증"):
                        session = None
                        error_streak += 1
                        self._emit("warn", f"[KTX] 세션 만료 또는 로그인 오류: {_exc_str(exc)}")
                    elif _looks_like(exc, "NetFunnel", "WAIT_NETFUNNEL"):
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
                                f"[KTX] WAIT_NETFUNNEL: 일시 오류 (3/3). "
                                f"세션 초기화 후 {wait}초 뒤 재시도합니다: {_exc_str(exc)}",
                            )
                        else:
                            self._emit(
                                "warn",
                                f"[KTX] WAIT_NETFUNNEL: 일시 오류 ({netfunnel_retries}/3). "
                                f"{wait}초 뒤 재시도합니다: {_exc_str(exc)}",
                            )
                        if await self._sleep_or_stop(stop_event, wait):
                            break
                        continue
                    else:
                        error_streak += 1
                        self._emit("warn", f"[KTX] 코레일 오류: {_exc_str(exc)}")
                except _network_exceptions() as exc:
                    error_streak += 1
                    self._emit("warn", f"[KTX] 네트워크 연결 오류: {_exc_str(exc)}")
                except asyncio.CancelledError:
                    self._emit("warn", "[KTX] 모니터링 루프가 취소 요청을 받았습니다.")
                    raise
                except Exception as exc:
                    if _is_korail_macro_error(exc):
                        self._emit(
                            "error",
                            "[KTX] 코레일이 현재 korail2 요청을 구버전 앱/자동화 요청으로 차단했습니다. "
                            "코레일 연결 감시를 중지합니다. 연결 설정을 확인해 주세요.",
                        )
                        break
                    elif _looks_like(exc, "NoResults", "no results", "조회 결과", "없습니다"):
                        error_streak = 0
                        self._emit("info", f"[KTX] 조회 결과 없음: {_exc_str(exc)}")
                    elif _looks_like(exc, "SoldOut", "sold out", "매진"):
                        error_streak = 0
                        self._emit("info", f"[KTX] 매진: {_exc_str(exc)}")
                    elif _looks_like(exc, "login", "session", "로그인", "세션", "인증"):
                        session = None
                        error_streak += 1
                        self._emit("warn", f"[KTX] 세션 만료 또는 로그인 오류: {_exc_str(exc)}")
                    else:
                        error_streak += 1
                        self._emit(
                            "error", f"[KTX] 예상하지 못한 오류: {type(exc).__name__}: {_exc_str(exc)}"
                        )

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
            self._emit("warn", "[KTX] 외부 취소 요청으로 루프 종료")
        except Exception as exc:
            self._emit("error", f"[KTX] 루프 예외 유출: {type(exc).__name__}: {_exc_str(exc)}")
        finally:
            self._emit("info", "[KTX] 모니터링 루프가 종료되었습니다.")


MonitorService = KtxMonitorService

