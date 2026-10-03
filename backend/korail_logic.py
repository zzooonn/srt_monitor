"""
Korail (KTX / ITX / 무궁화호) 모니터링 비즈니스 로직.
korail2 패키지(pip install korail2)를 사용합니다.

korail2 train 객체 주요 속성:
  train.train_name        – "KTX", "ITX-새마을", "무궁화호" 등
  train.train_no          – 열차번호 (문자열)
  train.dep_name          – 출발역명
  train.arr_name          – 도착역명
  train.dep_date          – 출발일 YYYYMMDD
  train.dep_time          – 출발시각 HHMMSS
  train.arr_time          – 도착시각 HHMMSS
  train.has_general_seat()  – 일반실 여석 여부
  train.has_special_seat()  – 특실 여석 여부

korail2 reservation 객체 주요 속성:
  reservation.rsv_no      – 예약번호
  reservation.tickets     – Ticket 목록 (없을 수도 있음)

korail2 ticket 객체 주요 속성 (버전마다 다를 수 있음):
  ticket.car_no / ticket.car  – 호차
  ticket.seat_no / ticket.seat – 좌석 (예: "5A")
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .schemas import MonitorConfig
from .srt_logic import (
    has_adjacent_seats,
    has_same_car_seats,
    has_same_row_seats,
    has_window_seat,
    tickets_str,
)


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _fmt_time(value: str) -> str:
    """HHMMSS → HH:MM"""
    return datetime.strptime(value[:6], "%H%M%S").strftime("%H:%M")


class _TicketAdapter:
    """korail2 Ticket을 srt_logic 검증 함수와 호환되는 인터페이스로 변환.

    srt_logic의 ticket_seat_tuple()은 ticket.seat (예: "5A")과
    ticket.car (예: "5")를 사용한다. korail2는 버전에 따라
    seat_no / car_no 또는 seat / car 속성을 제공하므로 모두 시도한다.
    """

    def __init__(self, ticket: Any) -> None:
        self.seat = str(
            getattr(ticket, "seat_no", None)
            or getattr(ticket, "seat", None)
            or ""
        ).strip()
        self.car = str(
            getattr(ticket, "car_no", None)
            or getattr(ticket, "car", None)
            or ""
        ).strip()

    def __repr__(self) -> str:
        return f"<Ticket car={self.car} seat={self.seat}>"


def _get_tickets(reservation: Any) -> list[_TicketAdapter]:
    """korail2 Reservation에서 티켓 목록을 추출한다.

    버전에 따라 다른 속성명을 순서대로 시도한다.
    어떤 속성도 없으면 빈 리스트를 반환하며, 이 경우 좌석 조건
    검증은 건너뛴다(패스로 처리).
    """
    for attr in ("tickets", "seats", "journey_infos", "seat_infos"):
        items = getattr(reservation, attr, None)
        if items:
            return [_TicketAdapter(t) for t in items]
    return []


def _seat_type_value(has_special: bool, has_normal: bool) -> Any:
    """korail2 SeatType/SeatOption 상수 반환 (버전 호환)."""
    try:
        from korail2 import SeatType  # type: ignore[import]
    except ImportError:
        from korail2 import SeatOption as SeatType  # type: ignore[import]

    if has_special and has_normal:
        return SeatType.GENERAL_FIRST
    if has_normal:
        return SeatType.GENERAL_ONLY
    return SeatType.SPECIAL_ONLY


# ── 여석 확인 ────────────────────────────────────────────────────────────────

def available_seat_type_korail(cfg: MonitorConfig, train: Any) -> bool:
    """예약 시도 조건에 맞는 여석이 있으면 True."""
    has_special = cfg.check_special and train.has_special_seat()
    has_normal = cfg.check_normal and train.has_general_seat()
    return has_special or has_normal


def seat_status_str_korail(cfg: MonitorConfig, train: Any) -> str:
    parts = []
    if cfg.check_special:
        parts.append(f"특실: {'✅' if train.has_special_seat() else '❌'}")
    if cfg.check_normal:
        parts.append(f"일반실: {'✅' if train.has_general_seat() else '❌'}")
    return " | ".join(parts)


# ── 승객 생성 ────────────────────────────────────────────────────────────────

def make_passengers_korail(cfg: MonitorConfig) -> list[Any]:
    try:
        from korail2 import (  # type: ignore[import]
            AdultPassenger,
            ChildPassenger,
            Disability1To3Passenger,
            Disability4To6Passenger,
            SeniorPassenger,
        )
    except ImportError:
        # 일부 버전은 korail2.passenger 서브모듈에 있음
        from korail2.passenger import (  # type: ignore[import]
            AdultPassenger,
            ChildPassenger,
            Disability1To3Passenger,
            Disability4To6Passenger,
            SeniorPassenger,
        )

    passengers: list[Any] = []
    passengers.extend(AdultPassenger() for _ in range(cfg.adult_count))
    passengers.extend(ChildPassenger() for _ in range(cfg.child_count))
    passengers.extend(SeniorPassenger() for _ in range(cfg.senior_count))
    passengers.extend(Disability1To3Passenger() for _ in range(cfg.disability_1_to_3_count))
    passengers.extend(Disability4To6Passenger() for _ in range(cfg.disability_4_to_6_count))
    return passengers or [AdultPassenger()]


# ── 좌석 조건 검증 ───────────────────────────────────────────────────────────

def validate_reserved_seats_korail(
    cfg: MonitorConfig, reservation: Any
) -> tuple[bool, str]:
    """예약된 좌석이 사용자 조건을 만족하는지 검증한다.

    korail2가 티켓 상세 정보를 제공하지 않는 경우(tickets 속성 없음)
    승객 수 이외의 조건은 검증 없이 통과로 처리하고 경고 메시지를 반환한다.
    """
    rsv_no = getattr(reservation, "rsv_no", "")
    tickets = _get_tickets(reservation)

    # ── 티켓 상세 정보를 얻지 못한 경우 ─────────────────────────────
    if not tickets:
        # 인원 수는 최소한 확인 (journey_cnt 등 사용)
        journey_cnt = getattr(reservation, "journey_cnt", None)
        if journey_cnt is not None and int(journey_cnt) != cfg.passenger_count:
            return (
                False,
                f"예약 좌석 수가 요청 인원과 다름 "
                f"({journey_cnt}/{cfg.passenger_count})",
            )
        note = ""
        if any([
            cfg.require_same_car,
            cfg.require_same_row,
            cfg.require_adjacent_seats,
            cfg.require_window_seat,
        ]):
            note = " (좌석 배치 상세 검증 불가 — korail2 버전 확인 필요)"
        return True, f"예약번호: {rsv_no}{note}"

    # ── 티켓 상세 정보가 있는 경우: SRT와 동일한 검증 수행 ───────────
    if len(tickets) != cfg.passenger_count:
        return (
            False,
            f"예약 좌석 수가 요청 인원과 다름 ({len(tickets)}/{cfg.passenger_count})",
        )
    if cfg.require_same_car and not has_same_car_seats(tickets):
        return False, f"같은 호차 좌석이 아님: {tickets_str(tickets)}"
    if cfg.require_same_row and not has_same_row_seats(tickets):
        return False, f"같은 줄 좌석이 아님: {tickets_str(tickets)}"
    if cfg.require_adjacent_seats and not has_adjacent_seats(cfg, tickets):
        required_pairs = 2 if len(tickets) >= 4 else 1
        return (
            False,
            f"붙은 좌석 조합이 {required_pairs}쌍 미만임: {tickets_str(tickets)}",
        )
    if cfg.require_window_seat and not has_window_seat(cfg, tickets):
        return False, f"창가 좌석이 없음: {tickets_str(tickets)}"

    return True, tickets_str(tickets)


# ── 예약 시도 ────────────────────────────────────────────────────────────────

def try_reserve_train_korail(
    cfg: MonitorConfig,
    korail: Any,
    train: Any,
    *,
    emit: Callable[[str, str], None],
    send_telegram: Callable[[str], None],
) -> bool:
    """Korail 열차 예약 시도. 예약이 살아있으면 True 반환."""
    try:
        from korail2 import KorailError  # type: ignore[import]
    except ImportError:
        KorailError = Exception  # type: ignore[misc,assignment]

    has_special = cfg.check_special and train.has_special_seat()
    has_normal = cfg.check_normal and train.has_general_seat()
    if not has_special and not has_normal:
        return False

    seat_type = _seat_type_value(has_special, has_normal)

    # ── 예약 요청 ─────────────────────────────────────────────────────
    try:
        reservation = korail.reserve(
            train,
            passengers=make_passengers_korail(cfg),
            option=seat_type,
        )
    except KorailError as exc:
        emit("error", f"예약 실패 ({train.train_name} {train.train_no}호): {exc}")
        send_telegram(
            f"⚠️ <b>예약 실패</b>\n열차: {train.train_name} {train.train_no}호\n사유: {exc}"
        )
        return False

    rsv_no = getattr(reservation, "rsv_no", "")
    dep_str = _fmt_time(train.dep_time)
    arr_str = _fmt_time(train.arr_time)

    # ── 좌석 조건 검증 ────────────────────────────────────────────────
    seats_ok, seat_message = validate_reserved_seats_korail(cfg, reservation)

    if not seats_ok:
        emit("warn", f"좌석 조건 불일치: {seat_message}")

        if cfg.auto_cancel_if_seat_mismatch:
            try:
                korail.cancel(reservation)
                emit("info", f"조건 불일치 예약 자동 취소: {rsv_no}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"↩️ <b>좌석 조건 불일치로 자동 취소</b>\n"
                        f"🚄 {train.train_name} {train.train_no}호\n"
                        f"사유: {seat_message}\n예약번호: {rsv_no}"
                    )
                return False
            except KorailError as exc:
                emit("error", f"자동 취소 실패: {exc}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"⚠️ <b>자동 취소 실패 — 즉시 확인 필요</b>\n"
                        f"🚄 {train.train_name} {train.train_no}호\n"
                        f"예약번호: {rsv_no}\n"
                        f"좌석 조건: {seat_message}\n"
                        f"취소 오류: {exc}\n\n"
                        f"코레일 앱에서 직접 취소 여부를 결정하세요."
                    )
                return True

        if cfg.notify_if_seat_mismatch:
            send_telegram(
                f"⚠️ <b>예약은 되었지만 좌석 조건이 맞지 않습니다</b>\n"
                f"🚄 {train.train_name} {train.train_no}호\n"
                f"사유: {seat_message}\n예약번호: {rsv_no}\n\n"
                f"코레일 앱에서 직접 취소 여부를 결정하세요."
            )
        return True

    # ── 정상 예약 완료 ────────────────────────────────────────────────
    emit("info", f"예약 완료: {train.train_name} {train.train_no}호 / {seat_message}")
    send_telegram(
        f"🎉 <b>예약 완료!</b>\n"
        f"🚄 {train.train_name} {train.train_no}호\n"
        f"📍 {train.dep_name} {dep_str} → {train.arr_name} {arr_str}\n"
        f"💺 {seat_status_str_korail(cfg, train)}\n"
        f"🎫 배정좌석: {seat_message}\n"
        f"📋 예약번호: {rsv_no}\n\n"
        f"⏰ <b>20분 내 코레일 앱 또는 홈페이지에서 결제하세요!</b>\n"
        f"👉 <a href='https://www.letskorail.com'>코레일 바로가기</a>"
    )
    return True
