import pytest

from backend.schemas import MonitorConfig, PublicConfig
from backend.stations import ALL_STATIONS, KTX_STATIONS


def _ktx_station(code: str) -> str:
    return next(station["name"] for station in KTX_STATIONS if station["code"] == code)


def test_public_config_default():
    c = PublicConfig()
    assert c.departure_station == "오송"
    assert c.arrival_station == "동탄"


def test_public_config_ktx_defaults():
    c = PublicConfig()
    assert c.monitor_ktx is False
    assert c.ktx_departure_station == "서울"
    assert c.ktx_arrival_station == "부산"
    assert c.ktx_check_special is True
    assert c.ktx_check_normal is True


def test_time_range_valid():
    c = PublicConfig(departure_time="200000", max_departure_time="230000")
    assert c.departure_time == "200000"


def test_time_range_same_allowed():
    c = PublicConfig(departure_time="200000", max_departure_time="200000")
    assert c.max_departure_time == "200000"


def test_time_range_reversed_raises():
    with pytest.raises(ValueError, match="마지막 출발"):
        PublicConfig(departure_time="230000", max_departure_time="200000")


def test_monitor_config_inherits_time_range():
    with pytest.raises(ValueError, match="마지막 출발"):
        MonitorConfig(departure_time="230000", max_departure_time="200000")


def test_total_passengers_over_limit():
    with pytest.raises(ValueError, match="4"):
        MonitorConfig(adult_count=3, child_count=2)


def test_invalid_station_raises():
    with pytest.raises(ValueError, match="지원하지 않는 역"):
        PublicConfig(departure_station="missing")


def test_public_config_accepts_merged_ktx_station():
    c = PublicConfig(departure_station="서울", arrival_station="부산")
    assert c.departure_station == "서울"
    assert c.arrival_station == "부산"


def test_merged_station_list_contains_srt_and_ktx_only_stations():
    names = {station["name"] for station in ALL_STATIONS}
    assert "수서" in names
    assert "서울" in names


def test_quarter_hour_invalid_raises():
    with pytest.raises(ValueError, match="15"):
        PublicConfig(departure_time="200500")


def test_ktx_station_validates_when_monitor_ktx_enabled():
    c = PublicConfig(
        monitor_ktx=True,
        ktx_departure_station=_ktx_station("0001"),
        ktx_arrival_station=_ktx_station("0020"),
    )
    assert c.ktx_departure_station == "서울"
    assert c.ktx_arrival_station == "부산"


def test_ktx_station_invalid_raises_when_monitor_ktx_enabled():
    # Enabled legacy routes migrate to the main journey, whose validator rejects it.
    with pytest.raises(ValueError, match="지원하지 않는 역"):
        PublicConfig(
            monitor_ktx=True,
            ktx_departure_station="missing",
            ktx_arrival_station=_ktx_station("0020"),
        )


def test_ktx_station_not_validated_when_monitor_ktx_disabled():
    c = PublicConfig(
        monitor_ktx=False,
        ktx_departure_station="missing",
        ktx_arrival_station="missing",
    )
    assert c.ktx_departure_station == "missing"
    assert c.ktx_arrival_station == "missing"


def test_monitor_config_has_srt_fields():
    mc = MonitorConfig(srt_id="myid", srt_password="mypw")
    assert mc.srt_id == "myid"
    assert mc.srt_password == "mypw"


def test_monitor_config_has_korail_fields():
    mc = MonitorConfig(korail_id="myid", korail_password="mypw")
    assert mc.korail_id == "myid"
    assert mc.korail_password == "mypw"


def test_event_item_requires_service():
    from backend.schemas import EventItem

    item = EventItem(level="info", message="테스트", timestamp="2026-09-18T00:00:00", service="srt")
    assert item.service == "srt"

    with pytest.raises(ValueError):
        EventItem(level="info", message="테스트", timestamp="2026-09-18T00:00:00")


def test_service_status_has_phase():
    from backend.schemas import ServicePhase, ServiceStatus

    s = ServiceStatus(running=True, phase=ServicePhase.SEARCHING, reserved_count=0, last_message="조회 중")
    assert s.phase == ServicePhase.SEARCHING
    assert s.error is None


def test_monitor_status_nests_srt_and_ktx_and_lock():
    from backend.schemas import MonitorStatus, ReservationLockStatus, ServicePhase, ServiceStatus

    srt = ServiceStatus(running=True, phase=ServicePhase.SEARCHING, reserved_count=0, last_message="조회 중")
    ktx = ServiceStatus(running=False, phase=ServicePhase.OFF, reserved_count=0, last_message="대기 중")
    lock = ReservationLockStatus(blocked=False, reason=None, count=0, max=1)

    combined = MonitorStatus(
        running=True,
        phase=ServicePhase.SEARCHING,
        reserved_count=0,
        last_message="조회 중",
        srt=srt,
        ktx=ktx,
        reservation_lock=lock,
    )
    assert combined.srt.phase == ServicePhase.SEARCHING
    assert combined.ktx.phase == ServicePhase.OFF
    assert combined.reservation_lock.max == 1


def test_reservation_lock_resolve_payload():
    from backend.schemas import ReservationLockResolve

    assert ReservationLockResolve(found=True).found is True
