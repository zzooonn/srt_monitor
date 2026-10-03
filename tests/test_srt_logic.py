from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.reservation_coordinator import AttemptOutcome
from backend.srt_logic import try_reserve_train
from SRT import SRTError


def _cfg(**overrides):
    values = dict(
        check_special=True,
        check_normal=True,
        adult_count=1,
        child_count=0,
        senior_count=0,
        disability_1_to_3_count=0,
        disability_4_to_6_count=0,
        require_same_row=False,
        require_same_car=False,
        require_adjacent_seats=False,
        require_window_seat=False,
        window_seat_letters=["A", "D"],
        adjacent_seat_pairs=["AB", "CD"],
        auto_cancel_if_seat_mismatch=True,
        notify_if_seat_mismatch=True,
        prefer_window_seat=True,
    )
    values.update(overrides)
    values["passenger_count"] = 1
    return SimpleNamespace(**values)


def _train():
    return SimpleNamespace(
        train_name="SRT",
        train_number="101",
        dep_station_name="수서",
        arr_station_name="동탄",
        dep_time="20260101200000",
        arr_time="20260101201500",
        special_seat_available=lambda: False,
        general_seat_available=lambda: True,
    )


def _ticket(seat="1A", car="1"):
    return SimpleNamespace(seat=seat, car=car)


def test_reserve_error_returns_not_reserved():
    srt = MagicMock()
    srt.reserve.side_effect = SRTError("no seats")
    emit = MagicMock()
    send_telegram = MagicMock()

    result = try_reserve_train(_cfg(), srt, _train(), emit=emit, send_telegram=send_telegram)

    assert result.outcome is AttemptOutcome.NOT_RESERVED


def test_seat_mismatch_cancel_success_returns_not_reserved():
    reservation = SimpleNamespace(tickets=[_ticket()], reservation_number="R1")
    srt = MagicMock()
    srt.reserve.return_value = reservation
    srt.cancel.return_value = None
    emit = MagicMock()
    send_telegram = MagicMock()

    cfg = _cfg()
    cfg.passenger_count = 2  # reservation only has 1 ticket -> seat mismatch

    result = try_reserve_train(cfg, srt, _train(), emit=emit, send_telegram=send_telegram)

    assert result.outcome is AttemptOutcome.NOT_RESERVED
    srt.cancel.assert_called_once_with(reservation)


def test_seat_mismatch_cancel_failure_returns_uncertain():
    reservation = SimpleNamespace(tickets=[_ticket()], reservation_number="R1")
    srt = MagicMock()
    srt.reserve.return_value = reservation
    srt.cancel.side_effect = SRTError("cancel failed")
    emit = MagicMock()
    send_telegram = MagicMock()

    cfg = _cfg()
    cfg.passenger_count = 2  # forces seat mismatch

    result = try_reserve_train(cfg, srt, _train(), emit=emit, send_telegram=send_telegram)

    assert result.outcome is AttemptOutcome.UNCERTAIN


def test_seat_mismatch_without_auto_cancel_returns_reserved():
    reservation = SimpleNamespace(tickets=[_ticket()], reservation_number="R1")
    srt = MagicMock()
    srt.reserve.return_value = reservation
    emit = MagicMock()
    send_telegram = MagicMock()

    cfg = _cfg(auto_cancel_if_seat_mismatch=False)
    cfg.passenger_count = 2  # forces seat mismatch

    result = try_reserve_train(cfg, srt, _train(), emit=emit, send_telegram=send_telegram)

    assert result.outcome is AttemptOutcome.RESERVED
    srt.cancel.assert_not_called()


def test_successful_match_returns_reserved_with_reservation_id():
    reservation = SimpleNamespace(tickets=[_ticket()], reservation_number="R42")
    srt = MagicMock()
    srt.reserve.return_value = reservation
    emit = MagicMock()
    send_telegram = MagicMock()

    result = try_reserve_train(_cfg(), srt, _train(), emit=emit, send_telegram=send_telegram)

    assert result.outcome is AttemptOutcome.RESERVED
    assert result.reservation_id == "R42"


def test_telegram_failure_does_not_change_outcome():
    reservation = SimpleNamespace(tickets=[_ticket()], reservation_number="R42")
    srt = MagicMock()
    srt.reserve.return_value = reservation
    emit = MagicMock()

    sent = []
    result = try_reserve_train(
        _cfg(), srt, _train(), emit=emit, send_telegram=sent.append
    )

    assert result.outcome is AttemptOutcome.RESERVED
    assert len(sent) == 1  # 성공 알림이 한 번 시도됐음
