import asyncio

from backend.monitor_service import MonitorService
from backend.schemas import MonitorConfig, ServicePhase


def test_start_noops_for_non_srt_route():
    service = MonitorService()
    status = asyncio.run(
        service.start(
            MonitorConfig(
                departure_station="서울",
                arrival_station="부산",
            )
        )
    )

    assert status.running is False
    assert service.running is False
    assert "SRT 미지원" in status.last_message


def test_start_noops_without_srt_credentials():
    service = MonitorService()
    status = asyncio.run(
        service.start(
            MonitorConfig(
                departure_station="수서",
                arrival_station="동탄",
            )
        )
    )

    assert status.running is False
    assert service.running is False
    assert "SRT 계정" in status.last_message


def test_status_returns_service_status_with_off_phase_before_start():
    service = MonitorService()
    status = service.status()
    assert status.phase == ServicePhase.OFF


from backend.reservation_coordinator import AttemptOutcome, ReservationCoordinator
import pytest


def test_reservation_blocked_when_coordinator_goal_already_reached():
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(max_reservations=1)
    coordinator._count = 1  # simulate KTX already having reserved the one seat

    MonitorService(coordinator)
    attempt = asyncio.run(coordinator.try_begin_attempt("srt"))
    assert attempt is None  # SRT must not even begin a reservation attempt


def test_cancelled_reservation_attempt_marks_uncertain():
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await coordinator.try_begin_attempt("srt")
        assert attempt is not None
        try:
            raise asyncio.CancelledError()
        except asyncio.CancelledError:
            await coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
            raise

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(flow())

    assert coordinator.blocked_reason == "uncertain"
