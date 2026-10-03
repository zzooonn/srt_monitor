"""
SRT 모니터링 공통 비즈니스 로직.
srt_monitor.py (CLI) 와 monitor_service.py (Web) 가 함께 사용합니다.
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

from SRT import SRTError, SeatType
from SRT.passenger import Adult, Child, Disability1To3, Disability4To6, Senior

from .reservation_coordinator import AttemptOutcome, ReserveResult
from .schemas import MonitorConfig


def fmt_time(value: str) -> str:
    fmt = "%Y%m%d%H%M%S" if len(value) > 8 else "%H%M%S"
    return datetime.strptime(value, fmt).strftime("%H:%M")


def available_seat_type(cfg: MonitorConfig, train: Any) -> SeatType | None:
    has_special = cfg.check_special and train.special_seat_available()
    has_normal = cfg.check_normal and train.general_seat_available()
    if not has_special and not has_normal:
        return None
    if has_special and has_normal:
        return SeatType.GENERAL_FIRST
    return SeatType.GENERAL_ONLY if has_normal else SeatType.SPECIAL_ONLY


def seat_status_str(cfg: MonitorConfig, train: Any) -> str:
    parts = []
    if cfg.check_special:
        parts.append(f"특실: {'✅' if train.special_seat_available() else '❌'}")
    if cfg.check_normal:
        parts.append(f"일반실: {'✅' if train.general_seat_available() else '❌'}")
    return " | ".join(parts)


def make_passengers(cfg: MonitorConfig) -> list[Any]:
    passengers: list[Any] = []
    passengers.extend(Adult() for _ in range(cfg.adult_count))
    passengers.extend(Child() for _ in range(cfg.child_count))
    passengers.extend(Senior() for _ in range(cfg.senior_count))
    passengers.extend(Disability1To3() for _ in range(cfg.disability_1_to_3_count))
    passengers.extend(Disability4To6() for _ in range(cfg.disability_4_to_6_count))
    return passengers or [Adult()]


def ticket_seat_tuple(ticket: Any) -> tuple[str, int, str] | None:
    seat = getattr(ticket, "seat", "")
    row = "".join(ch for ch in seat if ch.isdigit())
    col = "".join(ch for ch in seat if ch.isalpha()).upper()
    if not row or not col:
        return None
    return str(getattr(ticket, "car", "")), int(row), col


def tickets_str(tickets: list[Any]) -> str:
    return ", ".join(f"{t.car}호차 {t.seat}" for t in tickets)


def has_same_row_seats(tickets: list[Any]) -> bool:
    parsed = [ticket_seat_tuple(t) for t in tickets]
    if len(parsed) <= 1:
        return True
    if any(item is None for item in parsed):
        return False
    cars = {item[0] for item in parsed if item is not None}
    rows = {item[1] for item in parsed if item is not None}
    return len(cars) == 1 and len(rows) == 1


def has_same_car_seats(tickets: list[Any]) -> bool:
    parsed = [ticket_seat_tuple(t) for t in tickets]
    if len(parsed) <= 1:
        return True
    if any(item is None for item in parsed):
        return False
    cars = {item[0] for item in parsed if item is not None}
    return len(cars) == 1


def adjacent_pair_count(cfg: MonitorConfig, tickets: list[Any]) -> int:
    parsed = [ticket_seat_tuple(t) for t in tickets]
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


def has_adjacent_seats(cfg: MonitorConfig, tickets: list[Any]) -> bool:
    passenger_count = len(tickets)
    if passenger_count <= 1:
        return True
    required_pairs = 2 if passenger_count >= 4 else 1
    return adjacent_pair_count(cfg, tickets) >= required_pairs


def has_window_seat(cfg: MonitorConfig, tickets: list[Any]) -> bool:
    parsed = [ticket_seat_tuple(t) for t in tickets]
    windows = {letter.upper() for letter in cfg.window_seat_letters}
    return any(item is not None and item[2] in windows for item in parsed)


def validate_reserved_seats(cfg: MonitorConfig, reservation: Any) -> tuple[bool, str]:
    tickets = reservation.tickets
    if len(tickets) != cfg.passenger_count:
        return False, f"예약 좌석 수가 요청 인원과 다름 ({len(tickets)}/{cfg.passenger_count})"
    if cfg.require_same_car and not has_same_car_seats(tickets):
        return False, f"같은 호차 좌석이 아님: {tickets_str(tickets)}"
    if cfg.require_same_row and not has_same_row_seats(tickets):
        return False, f"같은 줄 좌석이 아님: {tickets_str(tickets)}"
    if cfg.require_adjacent_seats and not has_adjacent_seats(cfg, tickets):
        required_pairs = 2 if len(tickets) >= 4 else 1
        return False, f"붙은 좌석 조합이 {required_pairs}쌍 미만임: {tickets_str(tickets)}"
    if cfg.require_window_seat and not has_window_seat(cfg, tickets):
        return False, f"창가 좌석이 없음: {tickets_str(tickets)}"
    return True, tickets_str(tickets)


def try_reserve_train(
    cfg: MonitorConfig,
    srt: Any,
    train: Any,
    *,
    emit: Callable[[str, str], None],
    send_telegram: Callable[[str], None],
    include_srt_link: bool = False,
) -> ReserveResult:
    """Reserve one train and report the outcome (reserved/not_reserved/uncertain)."""
    seat_type = available_seat_type(cfg, train)
    if seat_type is None:
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason="선호 좌석 없음")

    try:
        reservation = srt.reserve(
            train,
            passengers=make_passengers(cfg),
            special_seat=seat_type,
            window_seat=cfg.prefer_window_seat,
        )
    except SRTError as exc:
        emit("error", f"예약 실패 ({train.train_name} {train.train_number}호): {exc}")
        send_telegram(
            f"⚠️ <b>예약 실패</b>\n열차: {train.train_name} {train.train_number}호\n사유: {exc}",
        )
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason=str(exc))

    seats_ok, seat_message = validate_reserved_seats(cfg, reservation)
    if not seats_ok:
        emit("warn", f"좌석 조건 불일치: {seat_message}")
        if cfg.auto_cancel_if_seat_mismatch:
            try:
                srt.cancel(reservation)
                emit("info", f"조건 불일치 예약 자동 취소: {reservation.reservation_number}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"↩️ <b>좌석 조건 불일치로 자동 취소</b>\n"
                        f"🚄 {train.train_name} {train.train_number}호\n"
                        f"사유: {seat_message}\n예약번호: {reservation.reservation_number}",
                    )
                return ReserveResult(
                    AttemptOutcome.NOT_RESERVED,
                    reservation_id=str(reservation.reservation_number),
                    reason=seat_message,
                )
            except SRTError as exc:
                emit("error", f"자동 취소 실패: {exc}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"⚠️ <b>자동 취소 실패 — 즉시 확인 필요</b>\n"
                        f"🚄 {train.train_name} {train.train_number}호\n"
                        f"예약번호: {reservation.reservation_number}\n"
                        f"좌석 조건: {seat_message}\n"
                        f"취소 오류: {exc}\n\n"
                        f"SRT 앱에서 직접 취소 여부를 결정하세요.",
                    )
                return ReserveResult(
                    AttemptOutcome.UNCERTAIN,
                    reservation_id=str(reservation.reservation_number),
                    reason=f"자동 취소 실패: {exc}",
                )

        if cfg.notify_if_seat_mismatch:
            send_telegram(
                f"⚠️ <b>예약은 되었지만 좌석 조건이 맞지 않습니다</b>\n"
                f"🚄 {train.train_name} {train.train_number}호\n"
                f"사유: {seat_message}\n예약번호: {reservation.reservation_number}\n\n"
                f"SRT 앱에서 직접 취소 여부를 결정하세요.",
            )
        return ReserveResult(
            AttemptOutcome.RESERVED,
            reservation_id=str(reservation.reservation_number),
            reason=seat_message,
        )

    emit("info", f"예약 완료: {train.train_name} {train.train_number}호 / {seat_message}")
    link = "\n👉 <a href='https://etk.srail.kr'>SRT 바로가기</a>" if include_srt_link else ""
    send_telegram(
        f"🎉 <b>예약 완료!</b>\n"
        f"🚄 {train.train_name} {train.train_number}호\n"
        f"📍 {train.dep_station_name} {fmt_time(train.dep_time)} → "
        f"{train.arr_station_name} {fmt_time(train.arr_time)}\n"
        f"💺 {seat_status_str(cfg, train)}\n"
        f"🎫 배정좌석: {seat_message}\n"
        f"📋 예약번호: {reservation.reservation_number}\n\n"
        f"⏰ <b>20분 내 SRT 앱에서 결제하세요!</b>{link}",
    )
    return ReserveResult(
        AttemptOutcome.RESERVED,
        reservation_id=str(reservation.reservation_number),
        reason=seat_message,
    )
