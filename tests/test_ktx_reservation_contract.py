"""Offline regressions against the project's installed Korail reservation shape."""
import asyncio
from types import SimpleNamespace

import pytest
from korail2.korail2 import Reservation

from backend.ktx_logic import try_reserve_train_ktx
from backend.reservation_coordinator import AttemptOutcome, ReservationCoordinator
from backend.schemas import MonitorConfig


def reservation(count=2):
    return Reservation({
        'h_pnr_no': 'R-CONTRACT', 'h_tot_seat_cnt': str(count),
        'h_rsv_amt': '10000', 'h_run_dt': '20261004',
        'h_ntisu_lmt_dt': '20261003', 'h_ntisu_lmt_tm': '230000',
    })


class Session:
    def __init__(self, response, cancel_result=True):
        self.response = response
        self.cancel_result = cancel_result
        self.bookings = 0
        self.cancellations = []

    def reserve(self, train, passengers, option, window_seat):
        self.bookings += 1
        return self.response

    def cancel(self, response):
        self.cancellations.append(response)
        return self.cancel_result


def run(session, **options):
    messages = []
    result = try_reserve_train_ktx(
        MonitorConfig(**options), session,
        SimpleNamespace(has_general_seat=lambda: True, has_special_seat=lambda: False),
        emit=lambda level, message: messages.append(message), send_telegram=messages.append,
    )
    return result, messages


def assert_blocks_new_booking(result, session):
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(2)

    async def flow():
        attempt = await coordinator.try_begin_attempt('ktx')
        await coordinator.finish_attempt(attempt, result.outcome)
        for service in ('ktx', 'srt'):
            next_attempt = await coordinator.try_begin_attempt(service)
            assert next_attempt is None
        coordinator.begin_or_resume_session(2)
        assert await coordinator.try_begin_attempt('ktx') is None

    asyncio.run(flow())
    assert coordinator.blocked_reason == 'uncertain'
    assert coordinator.count == 0
    assert session.bookings == 1


def test_actual_reservation_count_without_position_requirements_is_reserved():
    response = reservation()
    assert not hasattr(response, 'tickets') and not hasattr(response, 'ticket_list')
    session = Session(response)
    result, messages = run(session, require_adjacent_seats=False)
    assert result.outcome is AttemptOutcome.RESERVED
    assert result.reservation_id == 'R-CONTRACT'
    assert '좌석 상세 정보를 확인할 수 없' in result.reason
    assert session.cancellations == []
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(1)

    async def flow():
        attempt = await coordinator.try_begin_attempt('ktx')
        await coordinator.finish_attempt(attempt, result.outcome)
        assert await coordinator.try_begin_attempt('ktx') is None

    asyncio.run(flow())
    assert coordinator.count == 1


@pytest.mark.parametrize('constraint', [None, 'require_same_car', 'require_same_row', 'require_window_seat'])
def test_actual_reservation_without_positions_is_retained_for_manual_check(constraint):
    options = {} if constraint is None else {'require_adjacent_seats': False, constraint: True}
    session = Session(reservation())
    result, messages = run(session, **options)
    assert result.outcome is AttemptOutcome.UNCERTAIN
    assert result.reservation_id == 'R-CONTRACT'
    assert '좌석' in result.reason and '코레일+' in result.reason
    assert any('예약을 유지' in message for message in messages)
    assert session.cancellations == []
    assert_blocks_new_booking(result, session)


@pytest.mark.parametrize('response', [None, False, {}, SimpleNamespace(rsv_id=None),
                                     SimpleNamespace(rsv_id=''), SimpleNamespace(rsv_id='  '),
                                     SimpleNamespace(rsv_id=[], tickets=[])])
@pytest.mark.parametrize('auto_cancel', [False, True])
def test_invalid_reservation_response_is_uncertain_and_never_cancelled(response, auto_cancel):
    session = Session(response)
    result, messages = run(session, auto_cancel_if_seat_mismatch=auto_cancel)
    assert result.outcome is AttemptOutcome.UNCERTAIN
    assert result.reservation_id == ''
    assert session.cancellations == []
    assert '코레일+' in result.reason
    assert_blocks_new_booking(result, session)


@pytest.mark.parametrize('identity', [None, '', '  ', 123, False])
def test_actual_reservation_without_valid_identity_never_counts_or_cancels(identity):
    response = reservation()
    response.rsv_id = identity
    session = Session(response)
    result, _ = run(session, auto_cancel_if_seat_mismatch=False, require_adjacent_seats=False)
    assert result.outcome is AttemptOutcome.UNCERTAIN
    assert result.reservation_id == ''
    assert session.cancellations == []
    assert_blocks_new_booking(result, session)


@pytest.mark.parametrize('count', [None, 'unknown', False])
def test_missing_count_and_tickets_does_not_prove_mismatch(count):
    response = reservation()
    response.seat_no_count = count
    session = Session(response)
    result, _ = run(session, require_adjacent_seats=False)
    assert result.outcome is AttemptOutcome.UNCERTAIN
    assert session.cancellations == []
    assert_blocks_new_booking(result, session)


@pytest.mark.parametrize('cancel_result', [False, None, 1])
def test_confirmed_count_mismatch_without_explicit_cancel_success_blocks(cancel_result):
    response = reservation(1)
    session = Session(response, cancel_result)
    result, messages = run(session)
    assert result.outcome is AttemptOutcome.UNCERTAIN
    assert session.cancellations == [response]
    assert '취소' in result.reason and '코레일+' in result.reason
    assert not any('자동 취소:' in message for message in messages)
    assert_blocks_new_booking(result, session)


def test_confirmed_count_mismatch_with_explicit_cancel_success_allows_next_attempt():
    response = reservation(1)
    session = Session(response, True)
    result, _ = run(session)
    assert result.outcome is AttemptOutcome.NOT_RESERVED
    assert '(1/2)' in result.reason
    assert session.cancellations == [response]
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(2)

    async def flow():
        attempt = await coordinator.try_begin_attempt('ktx')
        await coordinator.finish_attempt(attempt, result.outcome)
        assert await coordinator.try_begin_attempt('ktx') is not None

    asyncio.run(flow())


def test_confirmed_count_mismatch_without_auto_cancel_counts_retained_booking():
    session = Session(reservation(1))
    result, _ = run(session, auto_cancel_if_seat_mismatch=False)
    assert result.outcome is AttemptOutcome.RESERVED
    assert session.cancellations == []


def test_ticket_count_can_prove_mismatch_without_a_count_field():
    response = SimpleNamespace(rsv_id='R-TICKETS', tickets=[SimpleNamespace(car='1', seat='1A')])
    session = Session(response)
    result, _ = run(session)
    assert result.outcome is AttemptOutcome.NOT_RESERVED
    assert session.cancellations == [response]


def test_missing_ticket_coordinates_do_not_prove_position_mismatch():
    response = SimpleNamespace(rsv_id='R-TICKETS', tickets=[
        SimpleNamespace(car=None, seat=None), SimpleNamespace(car=None, seat=None),
    ])
    session = Session(response)
    result, _ = run(session)
    assert result.outcome is AttemptOutcome.UNCERTAIN
    assert session.cancellations == []
    assert_blocks_new_booking(result, session)


def test_confirmed_position_mismatch_can_still_be_auto_cancelled():
    response = SimpleNamespace(rsv_id='R-TICKETS', tickets=[
        SimpleNamespace(car='1', seat='1A'), SimpleNamespace(car='2', seat='2D'),
    ])
    session = Session(response)
    result, _ = run(session)
    assert result.outcome is AttemptOutcome.NOT_RESERVED
    assert session.cancellations == [response]
