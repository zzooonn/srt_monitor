"""
KTX monitoring business logic.

This module keeps korail2 imports lazy so the web app and unit tests can load
before KTX support dependencies are installed.
"""
import inspect
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from .reservation_coordinator import AttemptOutcome, ReserveResult

if TYPE_CHECKING:
    from .schemas import MonitorConfig
else:
    MonitorConfig = Any


class _FallbackReserveOption(Enum):
    GENERAL_FIRST = "GENERAL_FIRST"
    GENERAL_ONLY = "GENERAL_ONLY"
    SPECIAL_ONLY = "SPECIAL_ONLY"


ReserveOption: Any = _FallbackReserveOption
SoldOutError: Any = ()


class _ReservationRefused(Exception):
    """A definitive refusal of this booking, before any successful booking reply."""


class _FallbackPassenger:
    def __init__(self, count: int = 1, **_: Any) -> None:
        self.count = count


class AdultPassenger(_FallbackPassenger):
    pass


class ChildPassenger(_FallbackPassenger):
    pass


class SeniorPassenger(_FallbackPassenger):
    pass


def _load_korail2_symbols() -> None:
    global ReserveOption, SoldOutError
    global AdultPassenger, ChildPassenger, SeniorPassenger

    try:
        import korail2  # type: ignore[import-not-found]
    except Exception:
        return

    ReserveOption = getattr(korail2, "ReserveOption", ReserveOption)
    SoldOutError = getattr(korail2, "SoldOutError", SoldOutError)
    AdultPassenger = getattr(korail2, "AdultPassenger", AdultPassenger)
    ChildPassenger = getattr(korail2, "ChildPassenger", ChildPassenger)
    SeniorPassenger = getattr(korail2, "SeniorPassenger", SeniorPassenger)


_load_korail2_symbols()


def fmt_time_ktx(value: str) -> str:
    fmt = "%Y%m%d%H%M%S" if len(value) > 8 else "%H%M%S"
    return datetime.strptime(value, fmt).strftime("%H:%M")


def _call_bool(obj: Any, *names: str) -> bool:
    for name in names:
        if not hasattr(obj, name):
            continue
        value = getattr(obj, name)
        if callable(value):
            value = value()
        return bool(value)
    return False


def _normal_available(train: Any) -> bool:
    return _call_bool(
        train,
        "general_seat_available",
        "normal_seat_available",
        "seat_available",
        "has_seat",
        "has_general_seat",
    )


def _special_available(train: Any) -> bool:
    return _call_bool(
        train,
        "special_seat_available",
        "first_seat_available",
        "has_special_seat",
        "has_first_seat",
    )


def _reserve_option(name: str) -> Any:
    return getattr(ReserveOption, name)


def _cfg_bool(cfg: MonitorConfig, primary: str, fallback: str) -> bool:
    if hasattr(cfg, primary):
        return bool(getattr(cfg, primary))
    return bool(getattr(cfg, fallback))


def available_seat_type_ktx(cfg: MonitorConfig, train: Any) -> Any | None:
    has_special = _cfg_bool(cfg, "ktx_check_special", "check_special") and _special_available(train)
    has_normal = _cfg_bool(cfg, "ktx_check_normal", "check_normal") and _normal_available(train)
    if not has_special and not has_normal:
        return None
    if has_special and has_normal:
        return _reserve_option("GENERAL_FIRST")
    return _reserve_option("GENERAL_ONLY") if has_normal else _reserve_option("SPECIAL_ONLY")


def seat_status_str_ktx(cfg: MonitorConfig, train: Any) -> str:
    parts = []
    if _cfg_bool(cfg, "ktx_check_special", "check_special"):
        parts.append(f"특실: {'✅' if _special_available(train) else '❌'}")
    if _cfg_bool(cfg, "ktx_check_normal", "check_normal"):
        parts.append(f"일반실: {'✅' if _normal_available(train) else '❌'}")
    return " | ".join(parts)


def make_passengers_ktx(cfg: MonitorConfig) -> list[Any]:
    if cfg.disability_1_to_3_count or cfg.disability_4_to_6_count:
        raise ValueError("현재 코레일 연결은 장애인 운임 자동 예약을 지원하지 않습니다.")
    passengers: list[Any] = []
    if cfg.adult_count > 0:
        passengers.append(AdultPassenger(count=cfg.adult_count))
    if cfg.child_count > 0:
        passengers.append(ChildPassenger(count=cfg.child_count))
    if cfg.senior_count > 0:
        passengers.append(SeniorPassenger(count=cfg.senior_count))

    return passengers or [AdultPassenger(count=1)]


def ticket_seat_tuple_ktx(ticket: Any) -> tuple[str, int, str] | None:
    seat = getattr(ticket, "seat", getattr(ticket, "seat_no", None))
    car = getattr(ticket, "car", getattr(ticket, "car_no", None))
    if not isinstance(seat, str) or car is None or not str(car).strip():
        return None
    row = "".join(ch for ch in seat if ch.isdigit())
    col = "".join(ch for ch in seat if ch.isalpha()).upper()
    if not row or not col:
        return None
    return str(car), int(row), col


def tickets_str_ktx(tickets: list[Any]) -> str:
    parts = []
    for ticket in tickets:
        car = getattr(ticket, "car", getattr(ticket, "car_no", ""))
        seat = getattr(ticket, "seat", getattr(ticket, "seat_no", ""))
        parts.append(f"{car}호차 {seat}")
    return ", ".join(parts)


def has_same_row_seats_ktx(tickets: list[Any]) -> bool:
    parsed = [ticket_seat_tuple_ktx(t) for t in tickets]
    if len(parsed) <= 1:
        return True
    if any(item is None for item in parsed):
        return False
    cars = {item[0] for item in parsed if item is not None}
    rows = {item[1] for item in parsed if item is not None}
    return len(cars) == 1 and len(rows) == 1


def has_same_car_seats_ktx(tickets: list[Any]) -> bool:
    parsed = [ticket_seat_tuple_ktx(t) for t in tickets]
    if len(parsed) <= 1:
        return True
    if any(item is None for item in parsed):
        return False
    cars = {item[0] for item in parsed if item is not None}
    return len(cars) == 1


def adjacent_pair_count_ktx(cfg: MonitorConfig, tickets: list[Any]) -> int:
    parsed = [ticket_seat_tuple_ktx(t) for t in tickets]
    if any(item is None for item in parsed):
        return 0

    remaining = set(range(len(parsed)))
    pairs = [
        tuple(letter.upper() for letter in pair)
        for pair in cfg.adjacent_seat_pairs
        if len(pair) == 2
    ]
    count = 0

    for car, row in sorted({(item[0], item[1]) for item in parsed if item is not None}):
        indexes = [
            index
            for index in remaining
            if parsed[index] is not None and parsed[index][0] == car and parsed[index][1] == row
        ]
        columns = {parsed[index][2]: index for index in indexes if parsed[index] is not None}
        for left, right in pairs:
            if (
                left in columns
                and right in columns
                and columns[left] in remaining
                and columns[right] in remaining
            ):
                remaining.remove(columns[left])
                remaining.remove(columns[right])
                count += 1

    return count


def has_adjacent_seats_ktx(cfg: MonitorConfig, tickets: list[Any]) -> bool:
    passenger_count = len(tickets)
    if passenger_count <= 1:
        return True
    required_pairs = 2 if passenger_count >= 4 else 1
    return adjacent_pair_count_ktx(cfg, tickets) >= required_pairs


def has_window_seat_ktx(cfg: MonitorConfig, tickets: list[Any]) -> bool:
    parsed = [ticket_seat_tuple_ktx(t) for t in tickets]
    windows = {letter.upper() for letter in cfg.window_seat_letters}
    return any(item is not None and item[2] in windows for item in parsed)


def _reservation_tickets(reservation: Any) -> list[Any]:
    tickets = getattr(reservation, "tickets", None)
    if tickets is None:
        tickets = getattr(reservation, "ticket_list", [])
    if callable(tickets):
        tickets = tickets()
    return list(tickets or [])


def _reservation_number(reservation: Any) -> str:
    for name in ("reservation_number", "reservation_no", "rsv_id"):
        value = getattr(reservation, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def validate_reserved_seats_ktx(cfg: MonitorConfig, reservation: Any) -> tuple[bool | None, str]:
    """True: match, False: proven mismatch, None: details unavailable."""
    tickets = _reservation_tickets(reservation)
    count = getattr(reservation, "seat_no_count", None)
    # The installed Korail Reservation supplies an integer count but no tickets.
    # Empty/missing ticket details alone are never evidence of a zero-seat booking.
    confirmed_count = type(count) is int and count >= 0
    if confirmed_count and count != cfg.passenger_count:
        return False, f"예약 좌석 수가 요청 인원과 다름 ({count}/{cfg.passenger_count})"
    if tickets and len(tickets) != cfg.passenger_count:
        return False, f"예약 좌석 수가 요청 인원과 다름 ({len(tickets)}/{cfg.passenger_count})"
    if not confirmed_count and not tickets:
        return None, "예약 좌석 수와 좌석 상세 정보를 확인할 수 없습니다. 예약을 유지했으니 코레일+에서 예약 내역을 확인하세요."
    position_required = any((cfg.require_same_car, cfg.require_same_row,
                             cfg.require_adjacent_seats, cfg.require_window_seat))
    if position_required and (not tickets or any(ticket_seat_tuple_ktx(t) is None for t in tickets)):
        return None, "좌석 상세 정보를 확인할 수 없어 요청한 좌석 조건을 검증할 수 없습니다. 예약을 유지했으니 코레일+에서 배정 좌석을 확인하세요."
    if cfg.require_same_car and not has_same_car_seats_ktx(tickets):
        return False, f"같은 호차 좌석이 아님: {tickets_str_ktx(tickets)}"
    if cfg.require_same_row and not has_same_row_seats_ktx(tickets):
        return False, f"같은 줄 좌석이 아님: {tickets_str_ktx(tickets)}"
    if cfg.require_adjacent_seats and not has_adjacent_seats_ktx(cfg, tickets):
        required_pairs = 2 if len(tickets) >= 4 else 1
        return False, f"붙은 좌석 조합이 {required_pairs}쌍 미만임: {tickets_str_ktx(tickets)}"
    if cfg.require_window_seat and not has_window_seat_ktx(cfg, tickets):
        return False, f"창가 좌석이 없음: {tickets_str_ktx(tickets)}"
    if not tickets or any(ticket_seat_tuple_ktx(t) is None for t in tickets):
        return True, f"{cfg.passenger_count}명 예약 확인 — 좌석 상세 정보를 확인할 수 없습니다. 코레일+에서 배정 좌석을 확인하세요."
    return True, tickets_str_ktx(tickets)


def _train_attr(train: Any, *names: str, default: str = "") -> str:
    for name in names:
        value = getattr(train, name, None)
        if value is not None:
            return str(value)
    return default


def _reserve(ktx: Any, train: Any, seat_type: Any, passengers: list[Any], prefer_window: bool) -> Any:
    try:
        params = inspect.signature(ktx.reserve).parameters
    except (TypeError, ValueError):
        params = None

    kwargs: dict[str, Any] = {"passengers": passengers}
    if params is None or "option" in params:
        kwargs["option"] = seat_type
    elif "reserve_option" in params:
        kwargs["reserve_option"] = seat_type
    elif "special_seat" in params:
        kwargs["special_seat"] = seat_type
    if params is None or "window_seat" in params:
        kwargs["window_seat"] = prefer_window

    # Korail may follow a successful booking reply with reservations() to fetch
    # the ticket. Its error then does not mean the booking was rejected. Observe
    # only the first response check, and restore this per-session hook afterwards.
    result_check = getattr(ktx, "_result_check", None)
    first_reply: str | None = None
    checked_reply = False
    observed = False
    instance_fields = getattr(ktx, "__dict__", {})
    had_local_checker = "_result_check" in instance_fields
    previous_local_checker = instance_fields.get("_result_check")

    def observe_reply(result):
        nonlocal first_reply, checked_reply
        if not checked_reply:
            checked_reply = True
            if isinstance(result, dict):
                first_reply = result.get("strResult")
        return result_check(result)

    if callable(result_check):
        try:
            setattr(ktx, "_result_check", observe_reply)
            observed = True
        except (AttributeError, TypeError):
            # Unsupported wrappers remain conservative when reserve() raises.
            pass
    try:
        return ktx.reserve(train, **kwargs)
    except Exception as exc:
        if observed and (first_reply == "FAIL" or (not checked_reply and isinstance(exc, SoldOutError))):
            raise _ReservationRefused(str(exc)) from exc
        raise
    finally:
        if observed:
            if had_local_checker:
                setattr(ktx, "_result_check", previous_local_checker)
            else:
                delattr(ktx, "_result_check")


def try_reserve_train_ktx(
    cfg: MonitorConfig,
    ktx: Any,
    train: Any,
    *,
    emit: Callable[[str, str], None],
    send_telegram: Callable[[str], None],
    include_ktx_link: bool = False,
) -> ReserveResult:
    """Reserve one KTX train and report the outcome (reserved/not_reserved/uncertain)."""
    seat_type = available_seat_type_ktx(cfg, train)
    if seat_type is None:
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason="선호 좌석 없음")

    train_name = _train_attr(train, "train_name", "name", default="KTX")
    train_number = _train_attr(train, "train_number", "train_no", "number")

    try:
        reservation = _reserve(
            ktx,
            train,
            seat_type,
            make_passengers_ktx(cfg),
            cfg.prefer_window_seat,
        )
    except Exception as exc:
        definitive_refusal = isinstance(exc, _ReservationRefused)
        label = "예약 거절" if definitive_refusal else "예약 결과 확인 필요"
        emit("error", f"[KTX] {label} ({train_name} {train_number}호): {exc}")
        send_telegram(
            f"⚠️ <b>KTX {label}</b>\n열차: {train_name} {train_number}호\n사유: {exc}",
        )
        outcome = AttemptOutcome.NOT_RESERVED if definitive_refusal else AttemptOutcome.UNCERTAIN
        return ReserveResult(outcome, reason=str(exc))

    reservation_number = _reservation_number(reservation)
    if not reservation_number:
        reason = "유효한 예약번호를 확인할 수 없습니다. 코레일+에서 예약 내역을 직접 확인하세요."
        emit("warn", f"[KTX] 예약 결과 확인 필요: {reason}")
        send_telegram(f"⚠️ <b>KTX 예약 결과 확인 필요</b>\n{reason}")
        return ReserveResult(AttemptOutcome.UNCERTAIN, reason=reason)

    seats_ok, seat_message = validate_reserved_seats_ktx(cfg, reservation)
    if seats_ok is None:
        emit("warn", f"[KTX] 좌석 확인 필요: {seat_message} / 예약번호: {reservation_number}")
        send_telegram(
            f"⚠️ <b>KTX 예약 좌석 확인 필요</b>\n"
            f"🚄 {train_name} {train_number}호\n"
            f"예약번호: {reservation_number}\n{seat_message}",
        )
        return ReserveResult(AttemptOutcome.UNCERTAIN, reservation_id=reservation_number, reason=seat_message)
    if not seats_ok:
        emit("warn", f"[KTX] 좌석 조건 불일치: {seat_message}")
        if cfg.auto_cancel_if_seat_mismatch:
            try:
                cancelled = ktx.cancel(reservation)
                if cancelled is not True:
                    raise RuntimeError("취소 완료 응답을 확인할 수 없습니다. 코레일+에서 취소 여부를 확인하세요.")
                emit("info", f"[KTX] 조건 불일치 예약 자동 취소: {reservation_number}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"↩️ <b>KTX 좌석 조건 불일치로 자동 취소</b>\n"
                        f"🚄 {train_name} {train_number}호\n"
                        f"사유: {seat_message}\n예약번호: {reservation_number}",
                    )
                return ReserveResult(
                    AttemptOutcome.NOT_RESERVED, reservation_id=reservation_number, reason=seat_message
                )
            except Exception as exc:
                emit("error", f"[KTX] 자동 취소 실패: {exc}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"⚠️ <b>KTX 자동 취소 실패 — 즉시 확인 필요</b>\n"
                        f"🚄 {train_name} {train_number}호\n"
                        f"예약번호: {reservation_number}\n"
                        f"좌석 조건: {seat_message}\n"
                        f"취소 오류: {exc}\n\n"
                        f"코레일+에서 직접 취소 여부를 결정하세요.",
                    )
                return ReserveResult(
                    AttemptOutcome.UNCERTAIN,
                    reservation_id=reservation_number,
                    reason=f"자동 취소 실패: {exc}",
                )

        if cfg.notify_if_seat_mismatch:
            send_telegram(
                f"⚠️ <b>KTX 예약은 되었지만 좌석 조건이 맞지 않습니다</b>\n"
                f"🚄 {train_name} {train_number}호\n"
                f"사유: {seat_message}\n예약번호: {reservation_number}\n\n"
                f"코레일+에서 직접 취소 여부를 결정하세요.",
            )
        return ReserveResult(
            AttemptOutcome.RESERVED, reservation_id=reservation_number, reason=seat_message
        )

    dep_station = _train_attr(train, "dep_station_name", "dep_name", "dep")
    arr_station = _train_attr(train, "arr_station_name", "arr_name", "arr")
    dep_time = _train_attr(train, "dep_time", "departure_time")
    arr_time = _train_attr(train, "arr_time", "arrival_time")
    time_line = ""
    if dep_time and arr_time:
        time_line = f"📍 {dep_station} {fmt_time_ktx(dep_time)} → {arr_station} {fmt_time_ktx(arr_time)}\n"

    emit("info", f"[KTX] 예약 완료: {train_name} {train_number}호 / {seat_message}")
    link = "\n👉 <a href='https://www.letskorail.com'>레츠코레일 바로가기</a>" if include_ktx_link else ""
    send_telegram(
        f"🎉 <b>KTX 예약 완료!</b>\n"
        f"🚄 {train_name} {train_number}호\n"
        f"{time_line}"
        f"💺 {seat_status_str_ktx(cfg, train)}\n"
        f"🎫 배정좌석: {seat_message}\n"
        f"📋 예약번호: {reservation_number}\n\n"
        f"⏰ <b>코레일+ 또는 레츠코레일에서 결제하세요!</b>{link}",
    )
    return ReserveResult(AttemptOutcome.RESERVED, reservation_id=reservation_number, reason=seat_message)

