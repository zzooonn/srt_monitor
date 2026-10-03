import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config_store.CONFIG_PATH", tmp_path / "web_config.json")
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


def test_status_includes_srt_ktx_and_reservation_lock(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert "srt" in body and "ktx" in body
    assert "reservation_lock" in body
    assert body["reservation_lock"]["max"] >= 1


def test_restart_while_running_does_not_reset_count(client):
    coordinator = app.state.reservation_coordinator
    coordinator.begin_or_resume_session(max_reservations=5)
    import asyncio

    from backend.reservation_coordinator import AttemptOutcome

    async def seed():
        attempt = await coordinator.try_begin_attempt("srt")
        await coordinator.finish_attempt(attempt, AttemptOutcome.RESERVED)

    asyncio.run(seed())
    assert coordinator.count == 1

    from types import SimpleNamespace

    # pretend SRT is running without spinning a real monitoring loop
    app.state.srt_service._task = SimpleNamespace(done=lambda: False)

    client.post("/api/start", json={})

    assert coordinator.count == 1


def test_stop_preserves_uncertain_state(client):
    coordinator = app.state.reservation_coordinator
    coordinator.begin_or_resume_session(max_reservations=1)
    import asyncio

    from backend.reservation_coordinator import AttemptOutcome

    async def seed():
        attempt = await coordinator.try_begin_attempt("srt")
        await coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)

    asyncio.run(seed())

    client.post("/api/stop")

    assert coordinator.blocked_reason == "uncertain"


def test_reservation_lock_resolve_clears_uncertain(client):
    coordinator = app.state.reservation_coordinator
    coordinator.begin_or_resume_session(max_reservations=1)
    import asyncio

    from backend.reservation_coordinator import AttemptOutcome

    async def seed():
        attempt = await coordinator.try_begin_attempt("srt")
        await coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)

    asyncio.run(seed())

    response = client.post("/api/reservation-lock/resolve", json={"found": False})

    assert response.status_code == 200
    assert response.json()["blocked"] is False
    assert coordinator.blocked_reason is None


@pytest.mark.parametrize('field', ['departure_station', 'arrival_station',
                                 'ktx_departure_station', 'ktx_arrival_station'])
@pytest.mark.parametrize('value', [None, 123, False, {}, []])
@pytest.mark.parametrize('endpoint', ['/api/config/public', '/api/start'])
def test_malformed_station_values_are_validation_errors(client, field, value, endpoint):
    response = client.post(endpoint, json={field: value})
    assert response.status_code == 422
    assert any(field in error['loc'] for error in response.json()['detail'])


def test_station_api_includes_verified_branches_without_duplicate_names(client):
    expected = {'청량리', '양평', '서원주', '원주', '제천', '단양', '풍기', '영주', '안동',
                '만종', '횡성', '둔내', '평창', '진부(오대산)', '강릉',
                '판교', '부발', '충주', '문경', '살미', '수안보온천', '연풍', '부전'}
    for endpoint in ('/api/stations', '/api/stations/ktx'):
        stations = client.get(endpoint).json()
        names = [station['name'] for station in stations]
        assert len(names) == len(set(names))
        assert expected <= set(names)
        for station in stations:
            if station['name'] in expected:
                assert station['code'] is None
