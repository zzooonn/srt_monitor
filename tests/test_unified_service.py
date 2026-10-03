import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.schemas import EventItem, MonitorConfig, ServicePhase
from backend.reservation_coordinator import AttemptOutcome
from backend.ktx_monitor_service import KtxMonitorService


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.config_store.CONFIG_PATH', tmp_path / 'web_config.json')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        for engine in (app.state.srt_service, app.state.ktx_service):
            engine.start = AsyncMock(return_value=engine.status())
        yield client


def account(client, mode='korail'):
    return client.post('/api/config/secrets', json={
        ('korail_id' if mode == 'korail' else 'srt_id'): 'user',
        ('korail_password' if mode == 'korail' else 'srt_password'): 'password'})


def test_default_start_runs_only_korail_main_route_and_seats(client):
    account(client)
    response = client.post('/api/start', json={'schema_version': 2, 'departure_station': '수서',
        'arrival_station': '동탄', 'monitor_ktx': False, 'check_special': False,
        'ktx_departure_station': '서울', 'ktx_arrival_station': '부산'})
    assert response.status_code == 200
    assert app.state.srt_service.start.await_count == 0
    config = app.state.ktx_service.start.await_args.args[0]
    assert config.monitor_ktx is True
    assert (config.ktx_departure_station, config.ktx_arrival_station) == ('수서', '동탄')
    assert config.ktx_check_special is False


def test_legacy_start_runs_only_srt(client):
    account(client, 'legacy_srt')
    response = client.post('/api/start', json={'schema_version': 2, 'connection_mode': 'legacy_srt'})
    assert response.status_code == 200
    assert app.state.srt_service.start.await_count == 1
    assert app.state.ktx_service.start.await_count == 0
    assert response.json()['connection_mode'] == 'legacy_srt'


def test_selected_missing_account_rejects_without_using_srt_secrets(client):
    account(client, 'legacy_srt')
    response = client.post('/api/start', json={'schema_version': 2})
    assert response.status_code == 400
    assert '코레일' in response.json()['detail']
    assert app.state.srt_service.start.await_count == 0
    assert app.state.ktx_service.start.await_count == 0


def test_running_start_and_config_mutations_rejected(client):
    account(client)
    task = SimpleNamespace(done=lambda: False)
    app.state.ktx_service._task = task
    try:
        assert client.post('/api/start', json={'schema_version': 2, 'connection_mode': 'legacy_srt'}).status_code == 409
        assert client.post('/api/config/public', json={'schema_version': 2}).status_code == 409
        assert client.post('/api/config/secrets', json={'korail_id': 'changed'}).status_code == 409
        assert app.state.srt_service.start.await_count == 0
        assert client.get('/api/config/public').json()['connection_mode'] == 'korail'
    finally:
        app.state.ktx_service._task = None


def test_active_korail_phase_and_checked_at_are_aggregate_status(client):
    app.state.ktx_service._phase = ServicePhase.ERROR
    app.state.ktx_service._error = 'offline'
    app.state.ktx_service._last_checked_at = '2026-10-03T12:00:00+09:00'
    body = client.get('/api/status').json()
    assert body['phase'] == 'error' and body['error'] == 'offline'
    assert body['last_checked_at'] == '2026-10-03T12:00:00+09:00'


def test_uncertain_lock_overrides_idle_or_stopped_phase(client):
    coordinator = app.state.reservation_coordinator
    async def seed():
        coordinator.begin_or_resume_session(1)
        attempt = await coordinator.try_begin_attempt('ktx')
        await coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
    asyncio.run(seed())
    body = client.post('/api/stop').json()
    assert body['phase'] == 'needs_confirmation'
    assert body['reservation_lock']['uncertain_service'] == 'ktx'
    account(client)
    body = client.post('/api/start', json={'schema_version': 2}).json()
    assert body['phase'] == 'needs_confirmation'


def test_korail_unsupported_disability_fare_rejected_for_autoreserve(client):
    account(client)
    response = client.post('/api/start', json={'schema_version': 2, 'adult_count': 1,
                                              'disability_1_to_3_count': 1})
    assert response.status_code == 400
    assert '장애' in response.json()['detail']


def test_disability_passengers_can_use_notifications_without_reservation(client):
    account(client)
    response = client.post('/api/start', json={'schema_version': 2, 'adult_count': 1,
                                              'disability_1_to_3_count': 1, 'auto_reserve': False})
    assert response.status_code == 200


def test_v2_ignores_invalid_unused_legacy_route(client):
    account(client)
    response = client.post('/api/start', json={'schema_version': 2, 'monitor_ktx': True,
        'ktx_departure_station': 'obsolete', 'ktx_arrival_station': 'obsolete'})
    assert response.status_code == 200


def test_partial_config_merge_validation_returns_422(client):
    client.post('/api/config/public', json={'schema_version': 2, 'adult_count': 1, 'senior_count': 3})
    response = client.post('/api/config/public', json={'schema_version': 2, 'child_count': 1})
    assert response.status_code == 422
    assert client.get('/api/config/public').json()['child_count'] == 0


def test_inflight_attempt_phase_is_reserving(client):
    coordinator = app.state.reservation_coordinator
    coordinator.begin_or_resume_session(1)
    asyncio.run(coordinator.try_begin_attempt('ktx'))
    assert client.get('/api/status').json()['phase'] == 'reserving'


def test_unified_search_requests_all_types_and_checks_actual_name():
    received = {}
    class Search:
        def search_train(self, **kwargs):
            received.update(kwargs)
            return [SimpleNamespace(train_type_name='SRT'), SimpleNamespace(train_type_name='ITX', train_type='00')]
    service = KtxMonitorService()
    selected = service._search_train(Search(), MonitorConfig(), SimpleNamespace(ALL='109', KTX='100'))
    assert received['train_type'] == '109'
    assert [train.train_type_name for train in selected] == ['SRT']
    assert service.status().last_checked_at is not None


def test_srt_last_checked_timestamp_after_mock_query():
    from backend.monitor_service import MonitorService
    service = MonitorService()
    session = SimpleNamespace(search_train=lambda **kwargs: [])
    assert service._search_train(session, MonitorConfig()) == []
    assert service.status().last_checked_at is not None


def test_search_fallback_filters_out_regular_and_unidentified_trains():
    trains = [SimpleNamespace(train_name=name) for name in ('KTX', 'KTX-산천', 'KTX-이음', 'SRT', 'ITX-새마을')]
    trains.append(SimpleNamespace(train_type='00'))
    trains.append(SimpleNamespace(train_number='123'))
    class OldSearch:
        def search_train(self, dep, arr, date, time):
            return trains
    selected = KtxMonitorService()._search_train(OldSearch(), MonitorConfig(), SimpleNamespace(KTX='100'))
    assert [getattr(train, 'train_name', None) for train in selected] == ['KTX', 'KTX-산천', 'KTX-이음', 'SRT', None]


def test_sse_resume_at_last_event_id_and_no_duplicate_replay(client):
    from backend.app import stream_events
    srt = app.state.srt_service
    ktx = app.state.ktx_service
    for index in range(3):
        ktx._events.append(EventItem(id=str(index), level='info', message=str(index),
            timestamp=f'2026-10-03T12:00:0{index}+09:00', service='ktx'))
    async def consume():
        request = SimpleNamespace(headers={'last-event-id': '1'}, is_disconnected=AsyncMock(return_value=True))
        response = await stream_events(request, srt, ktx)
        return [chunk async for chunk in response.body_iterator]
    chunks = asyncio.run(consume())
    assert len(chunks) == 1 and chunks[0].startswith('id: 2\n')


def test_sse_skipped_backlog_stays_skipped(client, monkeypatch):
    from backend.app import stream_events
    ktx = app.state.ktx_service
    ktx._events.append(EventItem(id='old', level='info', message='old',
                                timestamp='2026-10-03T12:00:00+09:00', service='ktx'))
    monkeypatch.setattr('backend.app.asyncio.sleep', AsyncMock())
    async def consume():
        request = SimpleNamespace(headers={}, is_disconnected=AsyncMock(side_effect=[False, True]))
        response = await stream_events(request, app.state.srt_service, ktx, skip=1)
        return [chunk async for chunk in response.body_iterator]
    assert asyncio.run(consume()) == [': keepalive\n\n']


@pytest.mark.parametrize('mode', ['korail', 'legacy_srt'])
def test_failed_query_reports_error_phase_without_switching_connection(mode, monkeypatch):
    from backend import ktx_monitor_service, monitor_service
    from backend.monitor_service import MonitorService
    class Offline:
        def __init__(self, *args, **kwargs):
            pass
        def search_train(self, **kwargs):
            raise TimeoutError('query offline')
    if mode == 'korail':
        monkeypatch.setattr(ktx_monitor_service, '_load_korail2', lambda: (Offline, None, RuntimeError))
        service = KtxMonitorService()
    else:
        monkeypatch.setattr(monitor_service, 'SRT', Offline)
        service = MonitorService()
    service._send_telegram = lambda *args: None
    service._sleep_or_stop = AsyncMock(return_value=True)
    config = MonitorConfig(schema_version=2, connection_mode=mode, monitor_ktx=True,
                           srt_id='s', srt_password='p', korail_id='k', korail_password='p')
    async def run():
        await service.start(config)
        await service._task
    asyncio.run(run())
    assert service.status().phase == ServicePhase.ERROR
    assert 'query offline' in service.status().error


@pytest.mark.parametrize('outcome', [AttemptOutcome.RESERVED, AttemptOutcome.UNCERTAIN, AttemptOutcome.NOT_RESERVED])
def test_korail_completed_attempt_updates_phase_and_shared_lock(outcome, monkeypatch):
    from backend import ktx_monitor_service
    from backend.reservation_coordinator import ReserveResult, ReservationCoordinator
    class Session:
        def __init__(self, *args):
            pass
        def search_train(self, **kwargs):
            return [SimpleNamespace(train_type_name='KTX', train_number='1', dep_time='200000',
                                    general_seat_available=lambda: True)]
    monkeypatch.setattr(ktx_monitor_service, '_load_korail2', lambda: (Session, None, RuntimeError))
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(1)
    service = KtxMonitorService(coordinator)
    service._send_telegram = lambda *args: None
    service._sleep_or_stop = AsyncMock(return_value=True)
    service._try_reserve = lambda *args: ReserveResult(outcome)
    async def run():
        await service.start(MonitorConfig(schema_version=2, monitor_ktx=True, korail_id='k', korail_password='p'))
        await service._task
    asyncio.run(run())
    expected = {AttemptOutcome.RESERVED: ServicePhase.GOAL_REACHED,
                AttemptOutcome.UNCERTAIN: ServicePhase.NEEDS_CONFIRMATION,
                AttemptOutcome.NOT_RESERVED: ServicePhase.SEARCHING}[outcome]
    assert service.status().phase == expected
    assert coordinator.count == (1 if outcome is AttemptOutcome.RESERVED else 0)
    if outcome is AttemptOutcome.UNCERTAIN:
        assert asyncio.run(coordinator.try_begin_attempt('srt')) is None
