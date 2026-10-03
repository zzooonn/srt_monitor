"""SRT/KTX가 공유하는 예약 조정자.

두 감시 서비스가 동시에, 또는 시간이 어긋나게(타임아웃·재시작 포함) 예약을
시도해도 목표 수(max_reservations)보다 많이 예약되지 않도록 한다.
FastAPI/pydantic에 의존하지 않는 순수 파이썬 모듈이다.
"""
import asyncio
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Literal

Service = Literal["srt", "ktx"]
BlockReason = Literal["stopped", "uncertain", "goal_reached"]


class AttemptOutcome(str, Enum):
    RESERVED = "reserved"
    NOT_RESERVED = "not_reserved"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class Attempt:
    id: str
    service: Service


@dataclass
class ReserveResult:
    outcome: AttemptOutcome
    reservation_id: str = ""
    reason: str = ""


class ReservationCoordinator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._count = 0
        self._max = 1
        self._stopped = True
        self._in_flight: Attempt | None = None
        self._uncertain: Attempt | None = None

    @property
    def count(self) -> int:
        return self._count

    @property
    def in_flight(self) -> bool:
        return self._in_flight is not None

    @property
    def goal_reached(self) -> bool:
        return self._count >= self._max

    @property
    def blocked_reason(self) -> BlockReason | None:
        # "uncertain" 은 가장 시급한 사유이므로, 정지 상태에서도 우선 노출한다
        # (그래야 화면이 정지 후에도 "결과 확인 필요" 배너를 계속 보여줄 수 있다).
        if self._uncertain is not None:
            return "uncertain"
        if self._stopped:
            return "stopped"
        if self.goal_reached:
            return "goal_reached"
        return None

    def public_state(self) -> dict:
        return {
            "count": self._count,
            "max": self._max,
            "blocked": self.blocked_reason is not None,
            "reason": self.blocked_reason,
            "uncertain_service": self._uncertain.service if self._uncertain else None,
        }

    def update_max(self, max_reservations: int) -> None:
        self._max = max_reservations

    def begin_or_resume_session(self, max_reservations: int) -> None:
        self._max = max_reservations
        self._stopped = False
        if self._in_flight is None and self._uncertain is None:
            self._count = 0

    def mark_stopped(self) -> None:
        self._stopped = True

    async def try_begin_attempt(self, service: Service) -> Attempt | None:
        async with self._lock:
            if self.blocked_reason is not None or self._in_flight is not None:
                return None
            attempt = Attempt(id=uuid.uuid4().hex[:12], service=service)
            self._in_flight = attempt
            return attempt

    async def finish_attempt(self, attempt: Attempt, outcome: AttemptOutcome) -> None:
        async with self._lock:
            if self._in_flight is None or self._in_flight.id != attempt.id:
                return
            self._in_flight = None
            if outcome is AttemptOutcome.RESERVED:
                self._count += 1
            elif outcome is AttemptOutcome.UNCERTAIN:
                self._uncertain = attempt

    def resolve_uncertain(self, found: bool) -> None:
        if self._uncertain is None:
            return
        self._uncertain = None
        if found:
            self._count += 1
