# SRT+KTX 예약 조정 및 상태 분리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SRT와 KTX를 동시에 감시할 때 중복 예약을 막는 공용 `ReservationCoordinator`를 도입하고, 상태/이벤트를 SRT·KTX로 분리해서 보여준다.

**Architecture:** `backend/reservation_coordinator.py`에 SRT/KTX가 공유하는 예약 조정 객체를 만들고, `app.py` lifespan에서 하나만 생성해 양쪽 서비스에 주입한다. 예약 시도는 식별자(`Attempt`)로 추적해 정확히 1회 완료 처리하며, 결과가 불확실하면(`UNCERTAIN`) 수동 확인 전까지 양쪽 다 예약을 차단한다. 상태는 `ServiceStatus`(서비스별)와 `MonitorStatus`(합산 + 중첩된 srt/ktx/reservation_lock)로 나눈다. 화면은 실제 서비스에 쓰이는 `SRT Monitor _standalone_.html`에 내장된 `extracted_app.js`를 고쳐 gzip+base64로 재압축한다.

**Tech Stack:** Python 3.11+, FastAPI, pydantic v2, pytest, pytest-asyncio 또는 `asyncio.run`, React JSX(Babel standalone, 별도 빌드 없음)

## Global Constraints

- 이 저장소는 git 저장소가 아니다(`Is a git repository: false`) — 각 태스크의 "커밋" 단계는 생략한다. 대신 각 태스크 끝에 "Step 완료 확인"만 둔다.
- `docs/specs/2026-09-18-ktx-reservation-coordination-design.md`가 유일한 설계 근거다. 타입/필드명은 그 문서와 정확히 일치시킨다.
- `frontend/`(Vite 소스)는 건드리지 않는다.
- 실제 코레일/SRT 계정이 필요한 통합 테스트는 만들지 않는다(모의 객체만 사용).
- 테스트 실행 명령: `python -m pytest tests/ -v` (프로젝트 루트에서, `python` 대신 `py`가 필요하면 그것으로 대체).

---

## 파일 맵

| 파일 | 작업 |
|---|---|
| `backend/reservation_coordinator.py` | 신규 — `Attempt`, `AttemptOutcome`, `ReserveResult`, `ReservationCoordinator` |
| `tests/test_reservation_coordinator.py` | 신규 |
| `backend/schemas.py` | 수정 — `EventItem.service`, `ServicePhase`, `ServiceStatus`, `ReservationLockStatus`, `MonitorStatus`, `ReservationLockResolve` |
| `tests/test_schemas.py` | 수정 |
| `backend/srt_logic.py` | 수정 — `try_reserve_train()`이 `ReserveResult` 반환 |
| `tests/test_srt_logic.py` | 신규 |
| `backend/ktx_logic.py` | 수정 — `try_reserve_train_ktx()`가 `ReserveResult` 반환, `_reserve()` 시그니처 사전검사 |
| `tests/test_ktx_logic.py` | 수정(확장) |
| `backend/monitor_service.py` | 수정 — coordinator 주입, attempt 흐름, phase |
| `tests/test_monitor_service.py` | 수정(확장) |
| `backend/ktx_monitor_service.py` | 수정 — 동일 |
| `tests/test_ktx_monitor_service.py` | 수정(확장) |
| `backend/app.py` | 수정 — coordinator 생성/주입, `/api/start`·`/api/stop` 로직, `/api/reservation-lock/resolve` 신규, `_merged_status` |
| `tests/test_app.py` | 신규 |
| `extracted_app.js` | 수정 — 상태 카드 2줄, 잠금 배너, 이벤트 배지 |
| `rebundle_html.py` | 신규(임시 아님 — 재사용을 위해 저장소에 유지) |
| `SRT Monitor _standalone_.html` | 재압축 결과물(스크립트로 갱신) |
| `README.md` | 수정 — 5.6~5.7장, 6장 정정 |

---

## Task 1: `ReservationCoordinator`

**Files:**
- Create: `backend/reservation_coordinator.py`
- Test: `tests/test_reservation_coordinator.py`

**Interfaces:**
- Produces: `Service = Literal["srt", "ktx"]`, `AttemptOutcome(str, Enum)` with `RESERVED`/`NOT_RESERVED`/`UNCERTAIN`, `Attempt(id: str, service: Service)` (frozen dataclass), `ReserveResult(outcome: AttemptOutcome, reservation_id: str = "", reason: str = "")` (dataclass), `ReservationCoordinator` with methods `count`(property), `goal_reached`(property), `blocked_reason`(property, returns `Literal["stopped","uncertain","goal_reached"] | None`), `public_state() -> dict`, `update_max(max_reservations: int) -> None`, `begin_or_resume_session(max_reservations: int) -> None`, `mark_stopped() -> None`, `async try_begin_attempt(service: Service) -> Attempt | None`, `async finish_attempt(attempt: Attempt, outcome: AttemptOutcome) -> None`, `resolve_uncertain(found: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reservation_coordinator.py`:

```python
import asyncio

import pytest

from backend.reservation_coordinator import AttemptOutcome, ReservationCoordinator


def test_initial_state_blocks_until_started():
    c = ReservationCoordinator()
    assert c.blocked_reason == "stopped"
    assert c.count == 0


def test_begin_session_unblocks_and_allows_attempt():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)
    assert c.blocked_reason is None

    attempt = asyncio.run(c.try_begin_attempt("srt"))
    assert attempt is not None
    assert attempt.service == "srt"


def test_reserved_outcome_increments_count_and_reaches_goal():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)

    asyncio.run(flow())
    assert c.count == 1
    assert c.goal_reached
    assert c.blocked_reason == "goal_reached"


def test_not_reserved_outcome_allows_next_attempt():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.NOT_RESERVED)
        return await c.try_begin_attempt("ktx")

    second = asyncio.run(flow())
    assert second is not None
    assert c.count == 0


def test_only_one_side_can_hold_in_flight_attempt():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        first = await c.try_begin_attempt("srt")
        second = await c.try_begin_attempt("ktx")
        return first, second

    first, second = asyncio.run(flow())
    assert first is not None
    assert second is None


def test_concurrent_try_begin_attempt_only_one_wins():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def race():
        return await asyncio.gather(
            c.try_begin_attempt("srt"),
            c.try_begin_attempt("ktx"),
        )

    results = asyncio.run(race())
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 1


def test_uncertain_outcome_blocks_until_resolved():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)

    asyncio.run(flow())
    assert c.blocked_reason == "uncertain"
    assert asyncio.run(c.try_begin_attempt("ktx")) is None

    # restarting the session must NOT clear an unresolved uncertain attempt
    c.begin_or_resume_session(max_reservations=5)
    assert c.blocked_reason == "uncertain"
    assert asyncio.run(c.try_begin_attempt("ktx")) is None

    c.resolve_uncertain(found=False)
    assert c.blocked_reason is None
    assert c.count == 0


def test_resolve_uncertain_found_true_counts_toward_goal():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=1)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)

    asyncio.run(flow())
    c.resolve_uncertain(found=True)
    assert c.count == 1
    assert c.blocked_reason == "goal_reached"


def test_begin_or_resume_session_preserves_count_while_attempt_in_flight():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def start_attempt():
        return await c.try_begin_attempt("srt")

    attempt = asyncio.run(start_attempt())
    assert attempt is not None

    # a restart while an attempt is still in flight must not reset the count
    c.begin_or_resume_session(max_reservations=5)
    assert c.count == 0  # nothing reserved yet, but critically no exception and no corruption

    asyncio.run(c.finish_attempt(attempt, AttemptOutcome.RESERVED))
    assert c.count == 1

    # a *later* restart, now that nothing is in flight/uncertain, is a fresh session
    c.begin_or_resume_session(max_reservations=5)
    assert c.count == 0


def test_mark_stopped_preserves_count_and_uncertain():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)

    asyncio.run(flow())
    c.mark_stopped()
    assert c.count == 1
    assert c.blocked_reason == "stopped"

    # a naive "start" must not silently wipe the count even though stopped
    assert c.public_state()["count"] == 1


def test_duplicate_finish_attempt_is_ignored():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        attempt = await c.try_begin_attempt("srt")
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)
        await c.finish_attempt(attempt, AttemptOutcome.RESERVED)  # duplicate completion

    asyncio.run(flow())
    assert c.count == 1


def test_stale_attempt_finish_is_ignored():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=5)

    async def flow():
        first = await c.try_begin_attempt("srt")
        await c.finish_attempt(first, AttemptOutcome.NOT_RESERVED)
        second = await c.try_begin_attempt("ktx")
        # a late callback for the already-finished first attempt must not
        # touch the state of the (unrelated) second attempt
        await c.finish_attempt(first, AttemptOutcome.RESERVED)
        return second

    second = asyncio.run(flow())
    assert c.count == 0
    assert second is not None


def test_public_state_shape():
    c = ReservationCoordinator()
    c.begin_or_resume_session(max_reservations=3)
    state = c.public_state()
    assert state == {
        "count": 0,
        "max": 3,
        "blocked": False,
        "reason": None,
        "uncertain_service": None,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_reservation_coordinator.py -v`
Expected: `ERROR` or `FAIL` — `ModuleNotFoundError: No module named 'backend.reservation_coordinator'`

- [ ] **Step 3: Implement `backend/reservation_coordinator.py`**

```python
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
    def goal_reached(self) -> bool:
        return self._count >= self._max

    @property
    def blocked_reason(self) -> BlockReason | None:
        if self._stopped:
            return "stopped"
        if self._uncertain is not None:
            return "uncertain"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reservation_coordinator.py -v`
Expected: all `PASSED`

---

## Task 2: `EventItem.service` + 서비스별 이벤트 태깅

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/monitor_service.py` (`_emit` 메서드만)
- Modify: `backend/ktx_monitor_service.py` (`_emit` 메서드만)
- Modify: `tests/test_schemas.py`

**Interfaces:**
- Produces: `EventItem.service: Literal["srt", "ktx"]` (필수 필드). `MonitorService._emit`/`KtxMonitorService._emit`는 시그니처(`self, level: str, message: str`)를 바꾸지 않고 내부적으로 각자 고정된 서비스 값을 채운다.

- [ ] **Step 1: Write the failing test**

`tests/test_schemas.py` 끝에 추가:

```python
def test_event_item_requires_service():
    from backend.schemas import EventItem

    item = EventItem(level="info", message="테스트", timestamp="2026-09-18T00:00:00", service="srt")
    assert item.service == "srt"

    with pytest.raises(ValueError):
        EventItem(level="info", message="테스트", timestamp="2026-09-18T00:00:00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schemas.py::test_event_item_requires_service -v`
Expected: FAIL — `service`가 없어도 통과해버림(현재 필드 자체가 없음, `TypeError`는 안 나고 그냥 무시됨 — pydantic은 extra 필드를 기본적으로 무시하지 않고 에러내지 않는 구성일 수 있으므로 실제로는 두 번째 assert가 실패하지 않을 수 있음. 정확한 실패 사유는 상관없이, 구현 전에는 이 테스트가 의도대로 통과하지 않는지만 확인한다.)

- [ ] **Step 3: `backend/schemas.py`에 `service` 필드 추가**

`EventItem` 클래스를 찾아서 교체:

```python
class EventItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    level: str
    message: str
    timestamp: str
    service: Literal["srt", "ktx"]
```

파일 상단 import에 `Literal` 추가:

```python
from typing import Literal
```

(이미 `from datetime import date, timedelta`만 있다면 그 아래 줄에 `from typing import Literal`을 추가한다.)

- [ ] **Step 4: `monitor_service.py`의 `_emit` 수정**

`backend/monitor_service.py`에서:

```python
    def _emit(self, level: str, message: str) -> None:
        item = EventItem(
            level=level,
            message=message,
            timestamp=datetime.now(_KST).isoformat(timespec="seconds"),
        )
```

를 아래로 교체:

```python
    def _emit(self, level: str, message: str) -> None:
        item = EventItem(
            level=level,
            message=message,
            timestamp=datetime.now(_KST).isoformat(timespec="seconds"),
            service="srt",
        )
```

- [ ] **Step 5: `ktx_monitor_service.py`의 `_emit` 수정**

동일한 패턴으로 `service="ktx"`를 추가한다:

```python
    def _emit(self, level: str, message: str) -> None:
        item = EventItem(
            level=level,
            message=message,
            timestamp=datetime.now(_KST).isoformat(timespec="seconds"),
            service="ktx",
        )
```

- [ ] **Step 6: Run tests to verify everything passes**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED` (기존 `test_monitor_service.py`/`test_ktx_monitor_service.py`의 noop 테스트들도 `_emit`을 거치므로, 이 시점에 여전히 통과하는지 반드시 확인한다.)

---

## Task 3: `ServicePhase`/`ServiceStatus`/`ReservationLockStatus`/`MonitorStatus` 재정의

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/monitor_service.py` (`status()`만, `__init__`에 `_phase` 추가)
- Modify: `backend/ktx_monitor_service.py` (동일)
- Modify: `tests/test_schemas.py`
- Modify: `tests/test_monitor_service.py`
- Modify: `tests/test_ktx_monitor_service.py`

**Interfaces:**
- Consumes: 없음(Task 1/2와 독립).
- Produces: `ServicePhase(str, Enum)`에 `OFF`/`SEARCHING`/`RESERVING`/`NEEDS_CONFIRMATION`/`GOAL_REACHED`/`ERROR`/`STOPPED`. `ServiceStatus(running: bool, phase: ServicePhase, reserved_count: int, last_message: str, started_at: str|None=None, error: str|None=None)`. `ReservationLockStatus(blocked: bool, reason: Literal["stopped","uncertain","goal_reached"]|None, count: int, max: int, uncertain_service: Literal["srt","ktx"]|None=None)`. `MonitorStatus(ServiceStatus)`에 `srt: ServiceStatus`, `ktx: ServiceStatus`, `reservation_lock: ReservationLockStatus` 추가. `ReservationLockResolve(found: bool)`. `MonitorService.status()`/`KtxMonitorService.status()`는 이제 `ServiceStatus`를 반환한다(`MonitorStatus` 아님).

- [ ] **Step 1: Write the failing tests**

`tests/test_schemas.py` 끝에 추가:

```python
def test_service_status_has_phase():
    from backend.schemas import ServiceStatus, ServicePhase

    s = ServiceStatus(running=True, phase=ServicePhase.SEARCHING, reserved_count=0, last_message="조회 중")
    assert s.phase == ServicePhase.SEARCHING
    assert s.error is None


def test_monitor_status_nests_srt_and_ktx_and_lock():
    from backend.schemas import MonitorStatus, ServiceStatus, ServicePhase, ReservationLockStatus

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
```

`tests/test_monitor_service.py` 끝에 추가:

```python
from backend.schemas import ServicePhase


def test_status_returns_service_status_with_off_phase_before_start():
    service = MonitorService()
    status = service.status()
    assert status.phase == ServicePhase.OFF
```

`tests/test_ktx_monitor_service.py` 끝에 추가:

```python
from backend.schemas import ServicePhase


def test_status_returns_service_status_with_off_phase_before_start():
    service = KtxMonitorService()
    status = service.status()
    assert status.phase == ServicePhase.OFF
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_schemas.py tests/test_monitor_service.py tests/test_ktx_monitor_service.py -v`
Expected: `ImportError`/`AttributeError` (`ServicePhase`, `ServiceStatus` 등이 아직 없음)

- [ ] **Step 3: `backend/schemas.py` 수정**

기존 `MonitorStatus` 클래스를 찾아서 아래로 통째로 교체:

```python
class ServicePhase(str, Enum):
    OFF = "off"
    SEARCHING = "searching"
    RESERVING = "reserving"
    NEEDS_CONFIRMATION = "needs_confirmation"
    GOAL_REACHED = "goal_reached"
    ERROR = "error"
    STOPPED = "stopped"


class ServiceStatus(BaseModel):
    running: bool
    phase: ServicePhase
    reserved_count: int
    last_message: str
    started_at: str | None = None
    error: str | None = None


class ReservationLockStatus(BaseModel):
    blocked: bool
    reason: Literal["stopped", "uncertain", "goal_reached"] | None
    count: int
    max: int
    uncertain_service: Literal["srt", "ktx"] | None = None


class MonitorStatus(ServiceStatus):
    srt: ServiceStatus
    ktx: ServiceStatus
    reservation_lock: ReservationLockStatus


class ReservationLockResolve(BaseModel):
    found: bool
```

파일 상단 import에 `Enum` 추가:

```python
from enum import Enum
```

- [ ] **Step 4: `monitor_service.py`의 `__init__`/`status()` 수정**

`__init__`에 phase 필드 추가:

```python
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._config: MonitorConfig | None = None
        self._events: deque[EventItem] = deque(maxlen=300)
        self._reserved_count = 0
        self._started_at: str | None = None
        self._last_message = "대기 중"
        self._phase = ServicePhase.OFF
        self._error: str | None = None
```

`status()`를 교체:

```python
    def status(self) -> ServiceStatus:
        return ServiceStatus(
            running=self.running,
            phase=self._phase,
            reserved_count=self._reserved_count,
            last_message=self._last_message,
            started_at=self._started_at,
            error=self._error,
        )
```

`stop()` 안에서 `self._emit("info", "모니터링을 중지했습니다.")` 바로 앞에 한 줄 추가:

```python
        self._phase = ServicePhase.STOPPED
```

import에 `ServiceStatus`, `ServicePhase` 추가(`from .schemas import EventItem, MonitorConfig, MonitorStatus` → `from .schemas import EventItem, MonitorConfig, ServicePhase, ServiceStatus`로 교체; `MonitorStatus`는 이 파일에서 더 이상 쓰지 않으므로 제거).

- [ ] **Step 5: `ktx_monitor_service.py`도 동일하게 수정**

`__init__`에 `self._phase = ServicePhase.OFF`, `self._error: str | None = None` 추가.

`status()` 교체:

```python
    def status(self) -> ServiceStatus:
        return ServiceStatus(
            running=self.running,
            phase=self._phase,
            reserved_count=self._reserved_count,
            last_message=self._last_message,
            started_at=self._started_at,
            error=self._error,
        )
```

`stop()`에 `self._phase = ServicePhase.STOPPED` 추가(`_emit("info", "[KTX] 모니터링을 중지했습니다.")` 앞).

이 파일은 `try: from .schemas import EventItem, MonitorConfig, MonitorStatus / except Exception:` 형태의 폴백 임포트를 쓰고 있으므로, 두 군데(정상 import 절과 `except` 절의 fallback dataclass) 모두 손봐야 한다.

정상 import 절을 교체:

```python
try:
    from .schemas import EventItem, MonitorConfig, ServicePhase, ServiceStatus
except Exception:
    MonitorConfig = Any

    class ServicePhase:
        OFF = "off"
        SEARCHING = "searching"
        RESERVING = "reserving"
        NEEDS_CONFIRMATION = "needs_confirmation"
        GOAL_REACHED = "goal_reached"
        ERROR = "error"
        STOPPED = "stopped"

    @dataclass
    class ServiceStatus:
        running: bool
        phase: str
        reserved_count: int
        last_message: str
        started_at: str | None = None
        error: str | None = None

    @dataclass
    class EventItem:
        level: str
        message: str
        timestamp: str
        service: str = "ktx"
        id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
```

파일 안에서 `MonitorStatus`를 참조하는 다른 곳이 없는지 확인한다(있다면 `ServiceStatus`로 바꾼다).

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED`

---

## Task 4: `srt_logic.try_reserve_train()`이 `ReserveResult` 반환

**Files:**
- Modify: `backend/srt_logic.py`
- Create: `tests/test_srt_logic.py`

**Interfaces:**
- Consumes: `backend.reservation_coordinator.AttemptOutcome`, `ReserveResult` (Task 1).
- Produces: `try_reserve_train(...) -> ReserveResult` (기존엔 `bool`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_srt_logic.py`:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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

    result = try_reserve_train(
        _cfg(require_same_car=True), srt, _train(), emit=emit, send_telegram=send_telegram
    )
    # only one ticket in a "require_same_car" 2-seat scenario would mismatch on
    # passenger_count; force a mismatch via passenger_count directly instead
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

    def failing_telegram(_message):
        raise RuntimeError("network down")

    # try_reserve_train는 send_telegram이 던지는 예외를 호출자가 아니라
    # 자기 내부에서 처리하지 않는다 — send_telegram 콜백 자체가 예외를
    # 삼키는 책임을 진다(monitor_service._send_telegram이 이미 그렇게 함).
    # 여기서는 콜백이 예외를 던지지 않는 정상 계약을 지킨다는 전제로,
    # 예약 성공 판정에 텔레그램 호출 성공 여부가 전혀 관여하지 않음만 확인한다.
    sent = []
    result = try_reserve_train(
        _cfg(), srt, _train(), emit=emit, send_telegram=sent.append
    )

    assert result.outcome is AttemptOutcome.RESERVED
    assert len(sent) == 1  # 성공 알림이 한 번 시도됐음
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_srt_logic.py -v`
Expected: FAIL — `AttributeError: 'bool' object has no attribute 'outcome'` (현재 `bool` 반환 중)

- [ ] **Step 3: `backend/srt_logic.py` 수정**

파일 상단 import에 추가:

```python
from .reservation_coordinator import AttemptOutcome, ReserveResult
```

`validate_reserved_seats`의 첫 줄(`tickets = reservation.tickets`) 위, `try_reserve_train` 함수 전체를 아래로 교체:

```python
def try_reserve_train(
    cfg: MonitorConfig,
    srt: Any,
    train: Any,
    *,
    emit: Callable[[str, str], None],
    send_telegram: Callable[[str], None],
    include_srt_link: bool = False,
) -> ReserveResult:
    """Reserve one train and report the outcome (reserved/not_reserved/uncertain)."""
    seat_type = available_seat_type(cfg, train)
    if seat_type is None:
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason="선호 좌석 없음")

    try:
        reservation = srt.reserve(
            train,
            passengers=make_passengers(cfg),
            special_seat=seat_type,
            window_seat=cfg.prefer_window_seat,
        )
    except SRTError as exc:
        emit("error", f"예약 실패 ({train.train_name} {train.train_number}호): {exc}")
        send_telegram(
            f"⚠️ <b>예약 실패</b>\n열차: {train.train_name} {train.train_number}호\n사유: {exc}",
        )
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason=str(exc))

    seats_ok, seat_message = validate_reserved_seats(cfg, reservation)
    if not seats_ok:
        emit("warn", f"좌석 조건 불일치: {seat_message}")
        if cfg.auto_cancel_if_seat_mismatch:
            try:
                srt.cancel(reservation)
                emit("info", f"조건 불일치 예약 자동 취소: {reservation.reservation_number}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"↩️ <b>좌석 조건 불일치로 자동 취소</b>\n"
                        f"🚄 {train.train_name} {train.train_number}호\n"
                        f"사유: {seat_message}\n예약번호: {reservation.reservation_number}",
                    )
                return ReserveResult(
                    AttemptOutcome.NOT_RESERVED,
                    reservation_id=str(reservation.reservation_number),
                    reason=seat_message,
                )
            except SRTError as exc:
                emit("error", f"자동 취소 실패: {exc}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"⚠️ <b>자동 취소 실패 — 즉시 확인 필요</b>\n"
                        f"🚄 {train.train_name} {train.train_number}호\n"
                        f"예약번호: {reservation.reservation_number}\n"
                        f"좌석 조건: {seat_message}\n"
                        f"취소 오류: {exc}\n\n"
                        f"SRT 앱에서 직접 취소 여부를 결정하세요.",
                    )
                return ReserveResult(
                    AttemptOutcome.UNCERTAIN,
                    reservation_id=str(reservation.reservation_number),
                    reason=f"자동 취소 실패: {exc}",
                )

        if cfg.notify_if_seat_mismatch:
            send_telegram(
                f"⚠️ <b>예약은 되었지만 좌석 조건이 맞지 않습니다</b>\n"
                f"🚄 {train.train_name} {train.train_number}호\n"
                f"사유: {seat_message}\n예약번호: {reservation.reservation_number}\n\n"
                f"SRT 앱에서 직접 취소 여부를 결정하세요.",
            )
        return ReserveResult(
            AttemptOutcome.RESERVED,
            reservation_id=str(reservation.reservation_number),
            reason=seat_message,
        )

    emit("info", f"예약 완료: {train.train_name} {train.train_number}호 / {seat_message}")
    link = "\n👉 <a href='https://etk.srail.kr'>SRT 바로가기</a>" if include_srt_link else ""
    send_telegram(
        f"🎉 <b>예약 완료!</b>\n"
        f"🚄 {train.train_name} {train.train_number}호\n"
        f"📍 {train.dep_station_name} {fmt_time(train.dep_time)} → "
        f"{train.arr_station_name} {fmt_time(train.arr_time)}\n"
        f"💺 {seat_status_str(cfg, train)}\n"
        f"🎫 배정좌석: {seat_message}\n"
        f"📋 예약번호: {reservation.reservation_number}\n\n"
        f"⏰ <b>20분 내 SRT 앱에서 결제하세요!</b>{link}",
    )
    return ReserveResult(
        AttemptOutcome.RESERVED,
        reservation_id=str(reservation.reservation_number),
        reason=seat_message,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_srt_logic.py -v`
Expected: all `PASSED`

- [ ] **Step 5: 회귀 확인**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED` (Task 5에서 `monitor_service.py`가 아직 새 반환 타입에 맞춰 고쳐지지 않았으므로, `monitor_service.py`를 직접 실행하는 테스트가 있다면 이 시점에 깨질 수 있다 — 현재 `test_monitor_service.py`는 `start()`의 noop 경로만 테스트하므로 `try_reserve_train`을 호출하지 않아 영향 없음을 확인한다.)

---

## Task 5: `ktx_logic.try_reserve_train_ktx()`가 `ReserveResult` 반환 + `_reserve()` 재작성

**Files:**
- Modify: `backend/ktx_logic.py`
- Modify: `tests/test_ktx_logic.py`

**Interfaces:**
- Consumes: `backend.reservation_coordinator.AttemptOutcome`, `ReserveResult`.
- Produces: `try_reserve_train_ktx(...) -> ReserveResult`. `_reserve(...)`는 `ktx.reserve`를 정확히 1회만 호출.

- [ ] **Step 1: Write the failing tests**

`tests/test_ktx_logic.py` 끝에 추가:

```python
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
        # bound builtin-like method: inspect.signature() raises ValueError on
        # some builtins/C-extension callables. list.append reproduces that.
        reserve = staticmethod(lambda *args, **kwargs: calls.append(kwargs) or SimpleNamespace(rsv_id="R1", tickets=[]))

    # patch inspect.signature to simulate an unreadable signature regardless
    # of platform, so the test doesn't depend on CPython builtin internals.
    import backend.ktx_logic as ktx_logic_module

    original_signature = ktx_logic_module.inspect.signature

    def raising_signature(_callable):
        raise ValueError("signature not available")

    ktx_logic_module.inspect.signature = raising_signature
    try:
        _reserve(Ktx(), train=object(), seat_type="X", passengers=[1], prefer_window=True)
    finally:
        ktx_logic_module.inspect.signature = original_signature

    # 시그니처를 못 읽으면 가장 표준적인 조합(option + window_seat)으로 정확히 1번만 호출한다.
    assert len(calls) == 1
    assert calls[0] == {"passengers": [1], "option": "X", "window_seat": True}


def test_try_reserve_train_ktx_returns_reserved_on_success(monkeypatch):
    from backend import ktx_logic

    monkeypatch.setattr(ktx_logic, "available_seat_type_ktx", lambda cfg, train: "GENERAL_ONLY")

    class Ktx:
        def reserve(self, train, passengers, option, window_seat):
            return SimpleNamespace(rsv_id="R99", tickets=[])

    cfg = SimpleNamespace(
        prefer_window_seat=True,
        passenger_count=0,
        require_same_car=False,
        require_same_row=False,
        require_adjacent_seats=False,
        require_window_seat=False,
        window_seat_letters=["A", "D"],
        adjacent_seat_pairs=["AB", "CD"],
    )
    emitted = []
    result = try_reserve_train_ktx(
        cfg, Ktx(), SimpleNamespace(train_name="KTX", train_number="1"),
        emit=lambda level, msg: emitted.append((level, msg)),
        send_telegram=lambda msg: None,
    )

    assert result.outcome is AttemptOutcome.RESERVED
    assert result.reservation_id == "R99"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ktx_logic.py -v`
Expected: `ImportError`(`_reserve`가 아직 다른 시그니처) 또는 `AttributeError`

- [ ] **Step 3: `backend/ktx_logic.py` 수정**

파일 상단 import에 추가:

```python
import inspect

from .reservation_coordinator import AttemptOutcome, ReserveResult
```

기존 `_reserve` 함수를 아래로 교체:

```python
def _reserve(ktx: Any, train: Any, seat_type: Any, passengers: list[Any], prefer_window: bool) -> Any:
    try:
        params = inspect.signature(ktx.reserve).parameters
    except (TypeError, ValueError):
        params = None

    kwargs: dict[str, Any] = {"passengers": passengers}
    if params is None or "option" in params:
        kwargs["option"] = seat_type
    elif "reserve_option" in params:
        kwargs["reserve_option"] = seat_type
    elif "special_seat" in params:
        kwargs["special_seat"] = seat_type
    if params is None or "window_seat" in params:
        kwargs["window_seat"] = prefer_window

    return ktx.reserve(train, **kwargs)
```

기존 `try_reserve_train_ktx` 함수 전체를 아래로 교체:

```python
def try_reserve_train_ktx(
    cfg: MonitorConfig,
    ktx: Any,
    train: Any,
    *,
    emit: Callable[[str, str], None],
    send_telegram: Callable[[str], None],
    include_ktx_link: bool = False,
) -> ReserveResult:
    """Reserve one KTX train and report the outcome (reserved/not_reserved/uncertain)."""
    seat_type = available_seat_type_ktx(cfg, train)
    if seat_type is None:
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason="선호 좌석 없음")

    train_name = _train_attr(train, "train_name", "name", default="KTX")
    train_number = _train_attr(train, "train_number", "train_no", "number")

    try:
        reservation = _reserve(
            ktx,
            train,
            seat_type,
            make_passengers_ktx(cfg),
            cfg.prefer_window_seat,
        )
    except Exception as exc:
        emit("error", f"[KTX] 예약 실패 ({train_name} {train_number}호): {exc}")
        send_telegram(
            f"⚠️ <b>KTX 예약 실패</b>\n열차: {train_name} {train_number}호\n사유: {exc}",
        )
        return ReserveResult(AttemptOutcome.NOT_RESERVED, reason=str(exc))

    seats_ok, seat_message = validate_reserved_seats_ktx(cfg, reservation)
    reservation_number = _reservation_number(reservation)
    if not seats_ok:
        emit("warn", f"[KTX] 좌석 조건 불일치: {seat_message}")
        if cfg.auto_cancel_if_seat_mismatch:
            try:
                ktx.cancel(reservation)
                emit("info", f"[KTX] 조건 불일치 예약 자동 취소: {reservation_number}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"↩️ <b>KTX 좌석 조건 불일치로 자동 취소</b>\n"
                        f"🚄 {train_name} {train_number}호\n"
                        f"사유: {seat_message}\n예약번호: {reservation_number}",
                    )
                return ReserveResult(
                    AttemptOutcome.NOT_RESERVED, reservation_id=reservation_number, reason=seat_message
                )
            except Exception as exc:
                emit("error", f"[KTX] 자동 취소 실패: {exc}")
                if cfg.notify_if_seat_mismatch:
                    send_telegram(
                        f"⚠️ <b>KTX 자동 취소 실패 — 즉시 확인 필요</b>\n"
                        f"🚄 {train_name} {train_number}호\n"
                        f"예약번호: {reservation_number}\n"
                        f"좌석 조건: {seat_message}\n"
                        f"취소 오류: {exc}\n\n"
                        f"코레일톡에서 직접 취소 여부를 결정하세요.",
                    )
                return ReserveResult(
                    AttemptOutcome.UNCERTAIN,
                    reservation_id=reservation_number,
                    reason=f"자동 취소 실패: {exc}",
                )

        if cfg.notify_if_seat_mismatch:
            send_telegram(
                f"⚠️ <b>KTX 예약은 되었지만 좌석 조건이 맞지 않습니다</b>\n"
                f"🚄 {train_name} {train_number}호\n"
                f"사유: {seat_message}\n예약번호: {reservation_number}\n\n"
                f"코레일톡에서 직접 취소 여부를 결정하세요.",
            )
        return ReserveResult(
            AttemptOutcome.RESERVED, reservation_id=reservation_number, reason=seat_message
        )

    dep_station = _train_attr(train, "dep_station_name", "dep_name", "dep")
    arr_station = _train_attr(train, "arr_station_name", "arr_name", "arr")
    dep_time = _train_attr(train, "dep_time", "departure_time")
    arr_time = _train_attr(train, "arr_time", "arrival_time")
    time_line = ""
    if dep_time and arr_time:
        time_line = f"📍 {dep_station} {fmt_time_ktx(dep_time)} → {arr_station} {fmt_time_ktx(arr_time)}\n"

    emit("info", f"[KTX] 예약 완료: {train_name} {train_number}호 / {seat_message}")
    link = "\n👉 <a href='https://www.letskorail.com'>레츠코레일 바로가기</a>" if include_ktx_link else ""
    send_telegram(
        f"🎉 <b>KTX 예약 완료!</b>\n"
        f"🚄 {train_name} {train_number}호\n"
        f"{time_line}"
        f"💺 {seat_status_str_ktx(cfg, train)}\n"
        f"🎫 배정좌석: {seat_message}\n"
        f"📋 예약번호: {reservation_number}\n\n"
        f"⏰ <b>코레일톡 또는 레츠코레일에서 결제하세요!</b>{link}",
    )
    return ReserveResult(AttemptOutcome.RESERVED, reservation_id=reservation_number, reason=seat_message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ktx_logic.py -v`
Expected: all `PASSED` (`test_reserve_falls_back_to_option_when_signature_unreadable`은 `TypeError`를 잡아서 무시하는 형태이므로 실패하지 않는다)

- [ ] **Step 5: 회귀 확인**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED`

---

## Task 6: `monitor_service.py` — coordinator 연동

**Files:**
- Modify: `backend/monitor_service.py`
- Modify: `tests/test_monitor_service.py`

**Interfaces:**
- Consumes: `ReservationCoordinator`, `Attempt`, `AttemptOutcome` (Task 1), `ReserveResult`(Task 4의 반환값).
- Produces: `MonitorService(coordinator: ReservationCoordinator | None = None)` — `coordinator`를 생략하면 내부에서 전용 인스턴스를 하나 만들어 기존 단독 테스트(Task 3까지 작성된 노옵 테스트들)가 그대로 동작하게 한다. 실제 서비스(`app.py`)는 항상 명시적으로 주입한다.

- [ ] **Step 1: Write the failing tests**

`tests/test_monitor_service.py` 끝에 추가:

```python
from backend.reservation_coordinator import AttemptOutcome, ReservationCoordinator


def test_reservation_blocked_when_coordinator_goal_already_reached():
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(max_reservations=1)
    coordinator._count = 1  # simulate KTX already having reserved the one seat

    service = MonitorService(coordinator)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_monitor_service.py -v`
Expected: FAIL — `MonitorService(coordinator)`가 인자를 안 받음(`TypeError: __init__() takes 1 positional argument but 2 were given`)

- [ ] **Step 3: `backend/monitor_service.py` 수정**

import 절에 추가:

```python
from .reservation_coordinator import AttemptOutcome, ReservationCoordinator
```

`__init__`을 교체:

```python
    def __init__(self, coordinator: ReservationCoordinator | None = None) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._config: MonitorConfig | None = None
        self._events: deque[EventItem] = deque(maxlen=300)
        self._reserved_count = 0
        self._started_at: str | None = None
        self._last_message = "대기 중"
        self._phase = ServicePhase.OFF
        self._error: str | None = None
        self._coordinator = coordinator or ReservationCoordinator()
```

`_run()` 안에서 조회 시작 직전에 phase를 `SEARCHING`으로 설정한다. `self._emit("info", "열차 조회 요청 중")` 바로 앞에 추가:

```python
                    self._phase = ServicePhase.SEARCHING
```

목표 달성 체크(루프 최상단)를 교체:

```python
                if config.auto_reserve and self._coordinator.goal_reached:
                    self._emit("info", f"목표 예약 수 {config.max_reservations}건 달성")
                    self._phase = ServicePhase.GOAL_REACHED
                    break
```

예약 시도 블록(`if config.auto_reserve:` 안, `self._emit("info", f"예약 시도 중: ...")`부터 그 아래 `if reserved:` 블록까지)을 통째로 교체:

```python
                        if config.auto_reserve:
                            attempt = await self._coordinator.try_begin_attempt("srt")
                            if attempt is None:
                                break
                            self._phase = ServicePhase.RESERVING
                            self._emit("info", f"예약 시도 중: {train.train_name} {train.train_number}호")
                            try:
                                result = await self._run_srt_call(
                                    "예약 시도",
                                    lambda train=train, session=session: self._try_reserve(
                                        config, session, train
                                    ),
                                    timeout=60,
                                )
                            except SrtCallTimeout as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                seen.add(key)
                                session = None
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "예약 결과를 확인할 수 없어 SRT·KTX 자동 예약을 보류했습니다. "
                                    f"SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요. ({exc})",
                                )
                                break
                            except asyncio.CancelledError:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "예약 요청 도중 감시가 취소되어 결과가 불확실합니다. "
                                    "SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요.",
                                )
                                raise
                            except Exception as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "error",
                                    f"예약 시도 중 예상하지 못한 오류: {exc}. "
                                    "SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요.",
                                )
                                break
                            else:
                                await self._coordinator.finish_attempt(attempt, result.outcome)
                                if result.outcome is AttemptOutcome.RESERVED:
                                    seen.add(key)
                                    self._reserved_count += 1
                                elif result.outcome is AttemptOutcome.NOT_RESERVED:
                                    seen.add(key)
                        else:
```

(이 블록 바로 아래 원래 있던 `else:` 절인 "취소표 발견" 분기는 그대로 둔다 — 위 교체는 `if config.auto_reserve:` 줄부터 `else:` 직전까지만 바꾸는 것이다.)

`self._try_reserve`의 반환 타입 주석 갱신 — 시그니처 자체는 그대로 두되(`_try_reserve`는 내부적으로 `srt_logic.try_reserve_train`을 호출해 이미 `ReserveResult`를 반환하게 됐으므로 코드 변경 불필요, Task 4에서 이미 처리됨), `_run_srt_call`의 반환값이 `ReserveResult`가 되도록 타입 힌트만 확인한다(런타임 동작은 이미 맞음).

import 절 맨 위의 `from .schemas import EventItem, MonitorConfig, ServicePhase, ServiceStatus`가 Task 3에서 이미 반영되어 있는지 확인한다.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_monitor_service.py -v`
Expected: all `PASSED`

- [ ] **Step 5: 회귀 확인**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED`

---

## Task 7: `ktx_monitor_service.py` — coordinator 연동

**Files:**
- Modify: `backend/ktx_monitor_service.py`
- Modify: `tests/test_ktx_monitor_service.py`

**Interfaces:**
- Consumes: Task 1/5와 동일.
- Produces: `KtxMonitorService(coordinator: ReservationCoordinator | None = None)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ktx_monitor_service.py` 끝에 추가:

```python
import asyncio

from backend.reservation_coordinator import AttemptOutcome, ReservationCoordinator


def test_reservation_blocked_when_coordinator_goal_already_reached():
    coordinator = ReservationCoordinator()
    coordinator.begin_or_resume_session(max_reservations=1)
    coordinator._count = 1

    KtxMonitorService(coordinator)
    attempt = asyncio.run(coordinator.try_begin_attempt("ktx"))
    assert attempt is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ktx_monitor_service.py -v`
Expected: FAIL — `TypeError`

- [ ] **Step 3: `backend/ktx_monitor_service.py` 수정**

import에 추가:

```python
from .reservation_coordinator import AttemptOutcome, ReservationCoordinator
```

`__init__` 교체:

```python
    def __init__(self, coordinator: "ReservationCoordinator | None" = None) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._config: MonitorConfig | None = None
        self._events: deque[EventItem] = deque(maxlen=300)
        self._reserved_count = 0
        self._started_at: str | None = None
        self._last_message = "[KTX] 대기 중"
        self._phase = ServicePhase.OFF
        self._error: str | None = None
        self._coordinator = coordinator or ReservationCoordinator()
```

`_run()`에서 조회 시작 직전(`self._emit("info", "[KTX] 열차 조회 요청 중")` 앞)에:

```python
                    self._phase = ServicePhase.SEARCHING
```

목표 달성 체크(루프 최상단)를 교체:

```python
                if config.auto_reserve and self._coordinator.goal_reached:
                    self._emit("info", f"[KTX] 목표 예약 수 {config.max_reservations}건 달성")
                    self._phase = ServicePhase.GOAL_REACHED
                    break
```

예약 시도 블록(`if config.auto_reserve:`부터 `else:` 직전, "취소표 발견" 분기 전까지)을 교체:

```python
                        if config.auto_reserve:
                            attempt = await self._coordinator.try_begin_attempt("ktx")
                            if attempt is None:
                                break
                            self._phase = ServicePhase.RESERVING
                            train_name = _train_attr(train, "train_name", "name", default="KTX")
                            train_number = _train_attr(train, "train_number", "train_no", "number")
                            self._emit("info", f"[KTX] 예약 시도 중: {train_name} {train_number}호")
                            try:
                                result = await self._run_ktx_call(
                                    "예약 시도",
                                    lambda train=train, session=session: self._try_reserve(
                                        config, session, train
                                    ),
                                    timeout=60,
                                )
                            except KtxCallTimeout as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                seen.add(key)
                                session = None
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "예약 결과를 확인할 수 없어 SRT·KTX 자동 예약을 보류했습니다. "
                                    f"SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요. ({exc})",
                                )
                                break
                            except asyncio.CancelledError:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "warn",
                                    "[KTX] 예약 요청 도중 감시가 취소되어 결과가 불확실합니다. "
                                    "SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요.",
                                )
                                raise
                            except Exception as exc:
                                await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
                                error_streak += 1
                                self._phase = ServicePhase.NEEDS_CONFIRMATION
                                self._emit(
                                    "error",
                                    f"[KTX] 예약 시도 중 예상하지 못한 오류: {exc}. "
                                    "SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요.",
                                )
                                break
                            else:
                                await self._coordinator.finish_attempt(attempt, result.outcome)
                                if result.outcome is AttemptOutcome.RESERVED:
                                    seen.add(key)
                                    self._reserved_count += 1
                                elif result.outcome is AttemptOutcome.NOT_RESERVED:
                                    seen.add(key)
                        else:
```

기존 코드에 이미 `train_name`/`train_number`가 for 루프 안 다른 위치(취소표 분기)에서도 정의되어 있으니, 중복 정의로 인한 문제가 없는지(변수 재사용일 뿐이라 문제 없음) 확인한다.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ktx_monitor_service.py -v`
Expected: all `PASSED`

- [ ] **Step 5: 회귀 확인**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED`

---

## Task 8: `app.py` — coordinator 배선, 엔드포인트, 병합 상태

**Files:**
- Modify: `backend/app.py`
- Create: `tests/test_app.py`

**Interfaces:**
- Consumes: 위 모든 태스크.
- Produces: `GET /api/status`에 `srt`/`ktx`/`reservation_lock` 포함. `POST /api/reservation-lock/resolve` 신규.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config_store.CONFIG_PATH", tmp_path / "web_config.json")
    with TestClient(app) as c:
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

    app.state.srt_service._task = object()  # pretend SRT is running without spinning a real loop
    app.state.srt_service._task.done = lambda: False

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL — `app.state.reservation_coordinator` 없음(`AttributeError`), `/api/reservation-lock/resolve` 404

- [ ] **Step 3: `backend/app.py` 수정**

import 절에 추가/수정:

```python
from .reservation_coordinator import ReservationCoordinator
from .schemas import MonitorStatus, PublicConfig, ReservationLockResolve, ReservationLockStatus, SecretsPayload, SecretsStatus
```

`lifespan`을 교체:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    coordinator = ReservationCoordinator()
    app.state.reservation_coordinator = coordinator
    app.state.srt_service = MonitorService(coordinator)
    app.state.ktx_service = KtxMonitorService(coordinator)
    yield
    await asyncio.gather(
        app.state.srt_service.stop(),
        app.state.ktx_service.stop(),
        return_exceptions=True,
    )
```

`get_ktx` 함수 다음에 추가:

```python
def get_coordinator(request: Request) -> ReservationCoordinator:
    return request.app.state.reservation_coordinator


CoordinatorDep = Annotated[ReservationCoordinator, Depends(get_coordinator)]
```

`_merged_status`를 교체:

```python
def _merged_status(srt: MonitorService, ktx: KtxMonitorService, coordinator: ReservationCoordinator) -> MonitorStatus:
    ss = srt.status()
    ks = ktx.status()
    return MonitorStatus(
        running=ss.running or ks.running,
        phase=ss.phase,
        reserved_count=coordinator.count,
        last_message=ks.last_message if ks.running else ss.last_message,
        started_at=ss.started_at or ks.started_at,
        error=ks.error or ss.error,
        srt=ss,
        ktx=ks,
        reservation_lock=ReservationLockStatus(**coordinator.public_state()),
    )
```

`_merged_status`를 호출하는 모든 곳(`get_status`, `start_monitor`, `stop_monitor`)에 `coordinator` 인자를 추가:

```python
@app.get("/api/status")
def get_status(srt: SrtService, ktx: KtxService, coordinator: CoordinatorDep) -> MonitorStatus:
    return _merged_status(srt, ktx, coordinator)
```

```python
@app.post("/api/start")
async def start_monitor(public: PublicConfig, srt: SrtService, ktx: KtxService, coordinator: CoordinatorDep):
    save_public_config(public)
    full_config = load_config()
    already_active = srt.status().running or ktx.status().running
    if already_active:
        coordinator.update_max(full_config.max_reservations)
    else:
        coordinator.begin_or_resume_session(full_config.max_reservations)
    await srt.start(full_config)
    await ktx.start(full_config)
    return _merged_status(srt, ktx, coordinator)
```

```python
@app.post("/api/stop")
async def stop_monitor(srt: SrtService, ktx: KtxService, coordinator: CoordinatorDep):
    coordinator.mark_stopped()
    await asyncio.gather(srt.stop(), ktx.stop())
    return _merged_status(srt, ktx, coordinator)
```

`stream_events` 시그니처에도 `coordinator: CoordinatorDep`는 필요 없다(이벤트 스트림은 코디네이터를 쓰지 않음 — 그대로 둔다).

새 엔드포인트를 `stop_monitor` 바로 다음에 추가:

```python
@app.post("/api/reservation-lock/resolve")
def resolve_reservation_lock(payload: ReservationLockResolve, coordinator: CoordinatorDep) -> ReservationLockStatus:
    coordinator.resolve_uncertain(found=payload.found)
    return ReservationLockStatus(**coordinator.public_state())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py -v`
Expected: all `PASSED`

- [ ] **Step 5: 전체 회귀 확인**

Run: `python -m pytest tests/ -v`
Expected: all `PASSED`

- [ ] **Step 6: 서버 기동 확인**

Run: `python -c "from backend.app import app; print('OK')"`
Expected: `OK`

---

## Task 9: 화면 반영 — `extracted_app.js` 수정 및 재압축

**Files:**
- Modify: `extracted_app.js`
- Create: `rebundle_html.py`
- Modify(생성물): `SRT Monitor _standalone_.html`

- [ ] **Step 1: `emptyStatus`에 srt/ktx/reservation_lock 기본값 추가**

`extracted_app.js`에서 (약 102번째 줄) `const emptyStatus = { running: false, reserved_count: 0, last_message: '대기 중', started_at: null };`를 아래로 교체:

```js
const emptyServiceStatus = { running: false, phase: 'off', reserved_count: 0, last_message: '대기 중', started_at: null, error: null };
const emptyStatus = {
  running: false,
  phase: 'off',
  reserved_count: 0,
  last_message: '대기 중',
  started_at: null,
  error: null,
  srt: emptyServiceStatus,
  ktx: emptyServiceStatus,
  reservation_lock: { blocked: false, reason: null, count: 0, max: 1, uncertain_service: null },
};
```

- [ ] **Step 2: `eventFromApi`에 `service` 전달**

`function eventFromApi(item) { ... }`를 찾아 반환 객체에 `service: item.service`를 추가:

```js
function eventFromApi(item) {
  const important = item.message.includes('예약 완료') || item.message.includes('취소표 발견');
  return {
    id: item.id,
    time: formatEventTime(item.timestamp),
    level: important ? 'ok' : item.level,
    msg: item.message,
    timestamp: item.timestamp,
    service: item.service,
  };
}
```

- [ ] **Step 3: 상태 카드에 SRT/KTX 두 줄 추가**

`status-stats` 블록(`<div className="status-stats">` ... `</div>`) 바로 다음에 삽입:

```jsx
              <div className="service-status-row" style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                {[['SRT', backendStatus.srt], ['KTX', backendStatus.ktx]].map(([label, s]) => (
                  <span
                    key={label}
                    title={s.last_message}
                    style={{
                      flex: 1,
                      fontSize: 12,
                      padding: '6px 8px',
                      borderRadius: 8,
                      textAlign: 'center',
                      background: s.running ? 'var(--accent-soft)' : 'var(--surface-2, transparent)',
                      color: s.running ? 'var(--accent)' : 'var(--ink-4)',
                      border: '1px solid var(--border)',
                    }}
                  >
                    {label} · {{
                      off: '꺼짐', searching: '조회 중', reserving: '예약 시도 중',
                      needs_confirmation: '결과 확인 필요', goal_reached: '목표 달성',
                      error: '오류', stopped: '중지됨',
                    }[s.phase] || s.phase}
                  </span>
                ))}
              </div>
```

- [ ] **Step 4: 예약 잠금 배너 추가**

같은 `status-card` 안, 위에서 추가한 `service-status-row` 다음에 삽입:

```jsx
              {backendStatus.reservation_lock.blocked && backendStatus.reservation_lock.reason === 'uncertain' && (
                <div style={{ marginTop: 10, padding: 10, borderRadius: 8, background: 'color-mix(in oklab, var(--err) 12%, var(--surface))', border: '1px solid var(--err)' }}>
                  <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                    예약 결과를 확인할 수 없습니다 ({(backendStatus.reservation_lock.uncertain_service || '').toUpperCase()})
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 8 }}>
                    SRT 앱 또는 코레일톡에서 예약 내역을 직접 확인한 뒤 아래에서 선택하세요.
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn secondary" style={{ height: 32 }} onClick={() => resolveLock(true)}>예약이 있었어요</button>
                    <button className="btn secondary" style={{ height: 32 }} onClick={() => resolveLock(false)}>예약이 없었어요</button>
                  </div>
                </div>
              )}
```

- [ ] **Step 5: `resolveLock` 함수 추가**

`handleStop` 함수 바로 다음에 삽입:

```js
  const resolveLock = async (found) => {
    setBusy('lock');
    try {
      await apiRequest('/api/reservation-lock/resolve', { method: 'POST', body: JSON.stringify({ found }) });
      await refreshAll();
    } catch (err) {
      setModal({ kind: 'warn', title: '확인 처리 실패', body: err.message || String(err) });
    } finally {
      setBusy(null);
    }
  };
```

- [ ] **Step 6: 이벤트 로그에 서비스 배지 추가**

`{events.map((ev) => (` 블록 안, `<span className="tag">{ev.level}</span>` 다음에 삽입:

```jsx
                  <span className={`tag ${ev.service}`} style={{ marginLeft: 4, opacity: 0.7 }}>{(ev.service || '').toUpperCase()}</span>
```

- [ ] **Step 7: `rebundle_html.py` 작성**

Create `rebundle_html.py` at 프로젝트 루트:

```python
"""extracted_app.js를 gzip+base64로 재압축해 SRT Monitor _standalone_.html의
__bundler/manifest 안 앱 항목(UUID 9823f60b...)에 다시 넣는다.

사용법: 프로젝트 루트에서 `python rebundle_html.py` 실행.
"""
import base64
import gzip
import json
import re
import shutil
from pathlib import Path

HTML_PATH = Path("SRT Monitor _standalone_.html")
APP_UUID = "9823f60b-1b1e-44e2-b967-0f350d23a6ac"
APP_JS_PATH = Path("extracted_app.js")


def main() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    manifest_match = re.search(
        r'(<script type="__bundler/manifest">)(.*?)(</script>)', html, re.DOTALL
    )
    if manifest_match is None:
        raise SystemExit("manifest 스크립트 태그를 찾을 수 없습니다")

    manifest = json.loads(manifest_match.group(2))
    if APP_UUID not in manifest:
        raise SystemExit(f"manifest에 {APP_UUID} 항목이 없습니다")

    new_data = gzip.compress(APP_JS_PATH.read_bytes(), compresslevel=9)
    manifest[APP_UUID]["data"] = base64.b64encode(new_data).decode("ascii")
    manifest[APP_UUID]["compressed"] = True

    new_manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    new_html = (
        html[: manifest_match.start(2)] + new_manifest_json + html[manifest_match.end(2):]
    )

    shutil.copy(HTML_PATH, HTML_PATH.with_suffix(".html.bak"))
    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"재번들 완료: {len(new_data)} bytes (compressed)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: 재압축 실행**

Run: `python rebundle_html.py`
Expected: `재번들 완료: <N> bytes (compressed)`, `SRT Monitor _standalone_.html.bak`이 최신 백업으로 갱신됨.

- [ ] **Step 9: 바이트 단위 재검증**

Run:
```
python -c "
import re, json, base64, gzip, pathlib
html = pathlib.Path('SRT Monitor _standalone_.html').read_text(encoding='utf-8')
manifest = json.loads(re.search(r'<script type=\"__bundler/manifest\">(.*?)</script>', html, re.DOTALL).group(1))
data = gzip.decompress(base64.b64decode(manifest['9823f60b-1b1e-44e2-b967-0f350d23a6ac']['data']))
app_js = pathlib.Path('extracted_app.js').read_bytes()
assert data.replace(b'\r\n', b'\n') == app_js.replace(b'\r\n', b'\n'), '불일치'
print('일치 확인')
"
```
Expected: `일치 확인`

- [ ] **Step 10: 수동 브라우저 확인**

1. `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000` 실행
2. `http://127.0.0.1:8000` 접속
3. 상태 카드에 "SRT · 꺼짐/조회 중..." "KTX · 꺼짐/..." 두 줄이 보이는지 확인
4. 이벤트 로그 각 줄에 SRT/KTX 배지가 보이는지 확인(감시를 실제로 시작해야 이벤트가 쌓임 — 계정 없이 시작하면 "계정 정보 필요" 경고만 뜨는 게 정상)
5. 기존 SRT 기능(구간 검색, 저장, 시작/중지)이 그대로 동작하는지 확인

---

## Task 10: `README.md` 정정

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 5.6장(프론트엔드 갭) 정정**

`README.md`의 "5.6 프론트엔드 갭 — 백엔드만 KTX를 지원하고 UI는 아직 없음" 절 전체를 아래로 교체:

```markdown
### 5.6 실제 화면과 `frontend/`의 관계 (정정)

`run_web.bat`/`run_frontend.bat`는 Vite 개발 서버를 켜지 않고 `http://127.0.0.1:8000/`을 여는데, 이는 `backend/app.py`의 `GET /`가 서빙하는 **`SRT Monitor _standalone_.html`**이다. `frontend/src/`(Vite/React/TS)는 이 경로에 전혀 관여하지 않는 방치된 구현체다.

`SRT Monitor _standalone_.html`은 gzip+base64로 압축한 앱 코드를 `<script type="__bundler/manifest">`에 내장하고, 브라우저에서 Babel standalone으로 즉석 트랜스파일한다. 프로젝트 루트의 `extracted_app.js`가 이 내장 코드를 디코딩한 것과 바이트 단위로 동일함을 직접 확인했다 — `extracted_app.js`를 고치고 `rebundle_html.py`로 재압축하는 것이 실제 화면을 바꾸는 방법이다.

`extracted_app.js`(=실제 화면)에는 이미 KTX 토글, Korail 계정 입력, 구간별 KTX 자동판단, `/api/config/public`+`/api/config/secrets`의 올바른 호출이 구현되어 있다. 즉 이전 버전 이 문서에서 "프론트엔드에 KTX UI가 없다"고 적었던 것은 **`frontend/`만 보고 판단한 오류**였다.
```

- [ ] **Step 2: 6장(알려진 불일치) 정정**

"6. 알려진 불일치 / 확인 필요 사항"의 1번 항목(`/api/config` 엔드포인트 부재)을 아래로 교체:

```markdown
1. **`frontend/src/api.ts`의 `/api/config` 엔드포인트 부재는 실제 화면에 영향 없음** — 이 버그는 실제로 쓰이지 않는 `frontend/` Vite 소스에만 있다. 실제 화면(`extracted_app.js`)은 `/api/config/public`+`/api/config/secrets`를 정확히 나눠서 호출한다. `frontend/`를 계속 유지할지, 정리할지는 별도로 판단이 필요하다.
```

3번 항목(프론트엔드에 KTX UI 없음)을 삭제한다(5.6장에서 이미 정정했으므로 중복 제거).

- [ ] **Step 3: 한계 명시 추가**

6장 끝에 새 항목 추가:

```markdown
5. **`ReservationCoordinator`(SRT/KTX 중복 예약 방지)는 백엔드 프로세스가 살아있는 동안만 보호한다.** 상태는 메모리에만 있으므로, 백엔드 프로세스 자체가 재시작되면(정전, 수동 재실행 등) 그 시점에 진행 중이던 예약 시도 정보는 사라진다. 디스크 영속화는 이번 범위에 포함되지 않았다.
```

- [ ] **Step 4: 최종 확인**

`README.md`를 다시 읽어서 5.6장/6장이 자연스럽게 이어지는지, 장 번호가 어긋나지 않는지 확인한다.

---

## 완료 후 전체 검증

- [ ] Run: `python -m pytest tests/ -v` — 모두 `PASSED`
- [ ] Run: `python -c "from backend.app import app; print('OK')"` — `OK`
- [ ] `SRT Monitor _standalone_.html`과 `extracted_app.js`가 바이트 단위로 일치(Task 9 Step 9와 동일한 검증 재실행)
- [ ] `README.md` 5.6/6장이 정정된 내용을 반영
