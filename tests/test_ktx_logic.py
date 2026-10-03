from types import SimpleNamespace
import asyncio
import json
import pytest

from backend import ktx_logic
from backend.ktx_logic import available_seat_type_ktx, make_passengers_ktx, seat_status_str_ktx


class DummyReserveOption:
    GENERAL_FIRST = "GENERAL_FIRST"
    GENERAL_ONLY = "GENERAL_ONLY"
    SPECIAL_ONLY = "SPECIAL_ONLY"


class DummyAdult:
    def __init__(self, count=1, **kwargs):
        self.count = count


class DummyChild:
    def __init__(self, count=1, **kwargs):
        self.count = count


class DummySenior:
    def __init__(self, count=1, **kwargs):
        self.count = count


class Train:
    def __init__(self, *, normal: bool, special: bool) -> None:
        self.normal = normal
        self.special = special

    def general_seat_available(self) -> bool:
        return self.normal

    def special_seat_available(self) -> bool:
        return self.special


def cfg(**overrides):
    values = {
        "check_special": True,
        "check_normal": True,
        "adult_count": 2,
        "child_count": 0,
        "senior_count": 0,
        "disability_1_to_3_count": 0,
        "disability_4_to_6_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_available_seat_type_ktx_prefers_general_first_when_both_available(monkeypatch):
    monkeypatch.setattr(ktx_logic, "ReserveOption", DummyReserveOption)

    assert available_seat_type_ktx(cfg(), Train(normal=True, special=True)) == "GENERAL_FIRST"


def test_available_seat_type_ktx_respects_checked_classes(monkeypatch):
    monkeypatch.setattr(ktx_logic, "ReserveOption", DummyReserveOption)

    assert (
        available_seat_type_ktx(
            cfg(check_special=False, check_normal=True),
            Train(normal=True, special=True),
        )
        == "GENERAL_ONLY"
    )
    assert (
        available_seat_type_ktx(
            cfg(check_special=True, check_normal=False),
            Train(normal=True, special=True),
        )
        == "SPECIAL_ONLY"
    )
    assert available_seat_type_ktx(cfg(), Train(normal=False, special=False)) is None


def test_available_seat_type_ktx_prefers_ktx_check_fields(monkeypatch):
    monkeypatch.setattr(ktx_logic, "ReserveOption", DummyReserveOption)

    assert (
        available_seat_type_ktx(
            cfg(ktx_check_special=False, ktx_check_normal=True),
            Train(normal=True, special=True),
        )
        == "GENERAL_ONLY"
    )


def test_seat_status_str_ktx_uses_ktx_train_availability():
    assert seat_status_str_ktx(cfg(), Train(normal=True, special=False)) == "특실: ❌ | 일반실: ✅"


def test_seat_status_str_ktx_only_includes_enabled_classes():
    assert seat_status_str_ktx(cfg(check_special=False, check_normal=True), Train(normal=False, special=True)) == "일반실: ❌"


def test_make_passengers_ktx_builds_requested_passenger_types(monkeypatch):
    monkeypatch.setattr(ktx_logic, "AdultPassenger", DummyAdult)
    monkeypatch.setattr(ktx_logic, "ChildPassenger", DummyChild)
    monkeypatch.setattr(ktx_logic, "SeniorPassenger", DummySenior)
    config = cfg(
        adult_count=2,
        child_count=1,
        senior_count=1,
    )

    passengers = make_passengers_ktx(config)

    assert [type(passenger) for passenger in passengers] == [
        DummyAdult,
        DummyChild,
        DummySenior,
    ]
    assert [passenger.count for passenger in passengers] == [2, 1, 1]


def test_make_passengers_rejects_unsupported_disability_fares():
    import pytest
    with pytest.raises(ValueError, match="장애"):
        make_passengers_ktx(cfg(disability_1_to_3_count=1))


def test_transport_timeout_during_reservation_is_uncertain():
    from backend.reservation_coordinator import AttemptOutcome
    from backend.ktx_logic import try_reserve_train_ktx
    from unittest.mock import MagicMock
    session = MagicMock()
    session.reserve.side_effect = TimeoutError('reservation reply lost')
    result = try_reserve_train_ktx(cfg(prefer_window_seat=False), session,
                                  Train(normal=True, special=False), emit=MagicMock(), send_telegram=MagicMock())
    assert result.outcome is AttemptOutcome.UNCERTAIN


@pytest.mark.parametrize('error', [json.JSONDecodeError('malformed', '{', 1),
                                  TypeError('invalid response'), ValueError('invalid response'),
                                  KeyError('h_pnr_no'), RuntimeError('unknown response failure')])
def test_unknown_dispatched_response_keeps_coordinator_blocked(error):
    from backend.reservation_coordinator import ReservationCoordinator
    dispatches = []
    class Session:
        def reserve(self, train, **kwargs):
            dispatches.append(train)
            raise error
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(2)
    async def run():
        attempt = await coordinator.try_begin_attempt('ktx')
        result = try_reserve_train_ktx(cfg(prefer_window_seat=False), Session(),
            Train(normal=True, special=False), emit=lambda *args: None, send_telegram=lambda *args: None)
        await coordinator.finish_attempt(attempt, result.outcome)
        assert await coordinator.try_begin_attempt('ktx') is None
        assert await coordinator.try_begin_attempt('srt') is None
    asyncio.run(run())
    assert len(dispatches) == 1
    assert coordinator.blocked_reason == 'uncertain'


@pytest.mark.parametrize('response', [{'strResult': 'FAIL'}, {'strResult': 'SUCC'}])
def test_domain_errors_use_booking_reply_before_followup_failure(response):
    from korail2 import KorailError
    from backend.reservation_coordinator import ReservationCoordinator
    dispatches = []
    class Session:
        def _result_check(self, result):
            if result['strResult'] == 'FAIL':
                raise KorailError('confirmed reservation refusal', 'SERVER_REFUSAL')
            return True
        def reserve(self, train, **kwargs):
            dispatches.append(train)
            self._result_check(response)
            # A ticket-list refusal occurs after booking succeeded and is not a booking refusal.
            self._result_check({'strResult': 'FAIL'})
    session = Session()
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(2)
    async def run():
        attempt = await coordinator.try_begin_attempt('ktx')
        result = try_reserve_train_ktx(cfg(prefer_window_seat=False), session,
            Train(normal=True, special=False), emit=lambda *args: None, send_telegram=lambda *args: None)
        await coordinator.finish_attempt(attempt, result.outcome)
        second = await coordinator.try_begin_attempt('ktx')
        if response['strResult'] == 'FAIL':
            assert result.outcome is AttemptOutcome.NOT_RESERVED and second is not None
        else:
            assert result.outcome is AttemptOutcome.UNCERTAIN and second is None
    asyncio.run(run())
    assert len(dispatches) == 1
    # Restore the session's original response checker; the booking observer is scoped to one call.
    assert '_result_check' not in vars(session)


def test_specific_sold_out_refusal_allows_next_attempt():
    from korail2 import SoldOutError
    from backend.reservation_coordinator import ReservationCoordinator
    class Session:
        def _result_check(self, result):
            return True
        def reserve(self, train, **kwargs):
            raise SoldOutError('ERR211161')
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(2)
    async def run():
        attempt = await coordinator.try_begin_attempt('ktx')
        result = try_reserve_train_ktx(cfg(prefer_window_seat=False), Session(),
            Train(normal=True, special=False), emit=lambda *args: None, send_telegram=lambda *args: None)
        assert result.outcome is AttemptOutcome.NOT_RESERVED
        await coordinator.finish_attempt(attempt, result.outcome)
        assert await coordinator.try_begin_attempt('ktx') is not None
    asyncio.run(run())


def test_unobservable_domain_error_is_uncertain():
    from korail2 import KorailError
    class Session:
        @property
        def _result_check(self):
            def checker(result):
                raise KorailError('refused at unknown response stage', 'REFUSAL')
            return checker
        def reserve(self, train, **kwargs):
            self._result_check({'strResult': 'FAIL'})
    result = try_reserve_train_ktx(cfg(prefer_window_seat=False), Session(),
        Train(normal=True, special=False), emit=lambda *args: None, send_telegram=lambda *args: None)
    assert result.outcome is AttemptOutcome.UNCERTAIN


def test_scoped_response_observer_restores_existing_instance_checker():
    from korail2 import KorailError
    def checker(result):
        raise KorailError('confirmed negative', 'REFUSAL')
    class Session:
        def reserve(self, train, **kwargs):
            self._result_check({'strResult': 'FAIL'})
    session = Session()
    session._result_check = checker
    result = try_reserve_train_ktx(cfg(prefer_window_seat=False), session,
        Train(normal=True, special=False), emit=lambda *args: None, send_telegram=lambda *args: None)
    assert result.outcome is AttemptOutcome.NOT_RESERVED
    assert session._result_check is checker


def test_make_passengers_ktx_defaults_to_one_adult(monkeypatch):
    monkeypatch.setattr(ktx_logic, "AdultPassenger", DummyAdult)
    config = cfg(
        adult_count=0,
        child_count=0,
        senior_count=0,
        disability_1_to_3_count=0,
        disability_4_to_6_count=0,
    )

    passengers = make_passengers_ktx(config)

    assert len(passengers) == 1
    assert isinstance(passengers[0], DummyAdult)


def test_available_seat_type_ktx_supports_korail2_alias_methods(monkeypatch):
    monkeypatch.setattr(ktx_logic, "ReserveOption", DummyReserveOption)
    train = SimpleNamespace(has_seat=lambda: True, has_special_seat=lambda: False)

    assert available_seat_type_ktx(cfg(), train) == "GENERAL_ONLY"


from backend.reservation_coordinator import AttemptOutcome
from backend.ktx_logic import _reserve, try_reserve_train_ktx


def test_reserve_calls_option_kwarg_exactly_once_when_supported():
    calls = []

    class Ktx:
        def reserve(self, train, passengers, option, window_seat):
            calls.append({"passengers": passengers, "option": option, "window_seat": window_seat})
            return SimpleNamespace(rsv_id="R1", tickets=[])

    _reserve(Ktx(), train=object(), seat_type="GENERAL_FIRST", passengers=[1], prefer_window=True)

    assert len(calls) == 1
    assert calls[0]["option"] == "GENERAL_FIRST"


def test_reserve_calls_reserve_option_kwarg_when_option_not_supported():
    calls = []

    class Ktx:
        def reserve(self, train, passengers, reserve_option):
            calls.append({"reserve_option": reserve_option})
            return SimpleNamespace(rsv_id="R1", tickets=[])

    _reserve(Ktx(), train=object(), seat_type="GENERAL_FIRST", passengers=[1], prefer_window=True)

    assert len(calls) == 1
    assert calls[0]["reserve_option"] == "GENERAL_FIRST"


def test_reserve_falls_back_to_broadest_kwargs_when_signature_unreadable():
    calls = []

    class Ktx:
        reserve = staticmethod(lambda *args, **kwargs: calls.append(kwargs) or SimpleNamespace(rsv_id="R1", tickets=[]))

    import backend.ktx_logic as ktx_logic_module

    original_signature = ktx_logic_module.inspect.signature

    def raising_signature(_callable):
        raise ValueError("signature not available")

    ktx_logic_module.inspect.signature = raising_signature
    try:
        _reserve(Ktx(), train=object(), seat_type="X", passengers=[1], prefer_window=True)
    finally:
        ktx_logic_module.inspect.signature = original_signature

    assert len(calls) == 1
    assert calls[0] == {"passengers": [1], "option": "X", "window_seat": True}


def test_try_reserve_train_ktx_returns_reserved_on_success(monkeypatch):
    monkeypatch.setattr(ktx_logic, "available_seat_type_ktx", lambda cfg, train: "GENERAL_ONLY")

    class Ktx:
        def reserve(self, train, passengers, option, window_seat):
            return SimpleNamespace(rsv_id="R99", tickets=[
                SimpleNamespace(car="1", seat="1A"), SimpleNamespace(car="1", seat="1B")])

    config = cfg(
        prefer_window_seat=True,
        passenger_count=2,
        require_same_car=False,
        require_same_row=False,
        require_adjacent_seats=False,
        require_window_seat=False,
        window_seat_letters=["A", "D"],
        adjacent_seat_pairs=["AB", "CD"],
        auto_cancel_if_seat_mismatch=True,
        notify_if_seat_mismatch=True,
    )
    emitted = []
    result = try_reserve_train_ktx(
        config, Ktx(), SimpleNamespace(train_name="KTX", train_number="1"),
        emit=lambda level, msg: emitted.append((level, msg)),
        send_telegram=lambda msg: None,
    )

    assert result.outcome is AttemptOutcome.RESERVED
    assert result.reservation_id == "R99"
