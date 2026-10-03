import asyncio

from backend.ktx_monitor_service import KtxMonitorService, _is_korail_macro_error
from backend.schemas import MonitorConfig, ServicePhase


def test_start_noops_when_monitor_ktx_disabled():
    service = KtxMonitorService()
    status = asyncio.run(service.start(MonitorConfig(monitor_ktx=False)))

    assert status.running is False
    assert service.running is False


def test_start_noops_without_korail_credentials():
    service = KtxMonitorService()
    status = asyncio.run(service.start(MonitorConfig(monitor_ktx=True)))

    assert status.running is False
    assert service.running is False
    assert "Korail" in status.last_message


def test_detects_korail_macro_error():
    exc = RuntimeError(
        "원활한 서비스 이용을 위해 앱을 최신 버전으로 업데이트한 뒤 재실행 후 "
        "안정적인 환경에서 사용해 주시기 바랍니다. (MACRO ERROR)"
    )

    assert _is_korail_macro_error(exc)


def test_status_returns_service_status_with_off_phase_before_start():
    service = KtxMonitorService()
    status = service.status()
    assert status.phase == ServicePhase.OFF


from backend.reservation_coordinator import ReservationCoordinator


def test_reservation_blocked_when_coordinator_goal_already_reached():
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(max_reservations=1)
    coordinator._count = 1

    KtxMonitorService(coordinator)
    attempt = asyncio.run(coordinator.try_begin_attempt("ktx"))
    assert attempt is None
