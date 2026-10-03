"""Single lifecycle and public status for the selected railway connection."""
import asyncio

from .ktx_monitor_service import KtxMonitorService
from .monitor_service import MonitorService
from .reservation_coordinator import ReservationCoordinator
from .schemas import MonitorConfig, MonitorStatus, ReservationLockStatus, ServicePhase
from .stations import supports_srt_route
from .routes import validate_route


class MonitorConflict(ValueError):
    pass


class UnifiedMonitorService:
    def __init__(self, srt: MonitorService, ktx: KtxMonitorService,
                 coordinator: ReservationCoordinator, connection_mode: str = "korail"):
        self.srt = srt
        self.ktx = ktx
        self.coordinator = coordinator
        self.connection_mode = connection_mode
        self.operation_lock = asyncio.Lock()

    def ensure_idle(self):
        if self.srt.status().running or self.ktx.status().running:
            raise MonitorConflict("감시가 실행 중입니다. 먼저 중지한 뒤 설정을 변경하거나 다시 시작해 주세요.")

    def validate_start(self, config: MonitorConfig):
        validate_route(config)
        if config.connection_mode == "korail":
            if not config.korail_id.strip() or not config.korail_password.strip():
                raise ValueError("코레일 통합 계정과 비밀번호를 먼저 설정해 주세요.")
            if config.auto_reserve and (config.disability_1_to_3_count or config.disability_4_to_6_count):
                raise ValueError("현재 코레일 연결은 장애인 운임 자동 예약을 지원하지 않습니다. 알림만 받기를 선택해 주세요.")
        else:
            if not config.srt_id.strip() or not config.srt_password.strip():
                raise ValueError("이전 SRT 계정과 비밀번호를 먼저 설정해 주세요.")
            if not supports_srt_route(config.departure_station, config.arrival_station):
                raise ValueError("선택한 구간은 이전 SRT 연결에서 지원하지 않습니다. 코레일 연결을 선택해 주세요.")

    async def start(self, config: MonitorConfig):
        async with self.operation_lock:
            self.ensure_idle()
            self.validate_start(config)
            return await self.start_prepared(config)

    async def start_prepared(self, config: MonitorConfig):
        """Called after validation under operation_lock (API saves in the same lock)."""
        self.connection_mode = config.connection_mode
        self.coordinator.begin_or_resume_session(config.max_reservations)
        if config.connection_mode == "korail":
            internal = config.model_copy(update={
                "monitor_ktx": True,
                "ktx_departure_station": config.departure_station,
                "ktx_arrival_station": config.arrival_station,
                "ktx_check_normal": config.check_normal,
                "ktx_check_special": config.check_special,
            })
            await self.ktx.start(internal)
        else:
            await self.srt.start(config)
        return self.status()

    async def stop(self):
        async with self.operation_lock:
            self.coordinator.mark_stopped()
            await asyncio.gather(self.srt.stop(), self.ktx.stop())
            return self.status()

    def status(self) -> MonitorStatus:
        ss, ks = self.srt.status(), self.ktx.status()
        selected = ks if self.connection_mode == "korail" else ss
        # Also represent an engine that was already running before selection changed.
        active = ks if ks.running else ss if ss.running else selected
        lock = self.coordinator.public_state()
        phase = active.phase
        if lock["reason"] == "uncertain":
            phase = ServicePhase.NEEDS_CONFIRMATION
        elif self.coordinator.in_flight:
            phase = ServicePhase.RESERVING
        elif self.coordinator.goal_reached:
            phase = ServicePhase.GOAL_REACHED
        return MonitorStatus(
            connection_mode=self.connection_mode,
            running=ss.running or ks.running, phase=phase,
            reserved_count=self.coordinator.count,
            last_message=active.last_message, started_at=active.started_at,
            error=active.error, last_checked_at=active.last_checked_at,
            srt=ss, ktx=ks, reservation_lock=ReservationLockStatus(**lock),
        )

    def recent_events(self):
        return merged_events(self.srt, self.ktx)


def merged_events(srt, ktx):
    events = srt.recent_events() + ktx.recent_events()
    events.sort(key=lambda event: event.timestamp)
    return events[-300:]
