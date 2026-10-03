# SRT+KTX 동시 감시 — 중복 예약 방지 및 상태 분리 설계

> 작성일: 2026-09-18 (1차 설계 리뷰 반영 후 개정)
> 배경: `README.md`(같은 날 작성)에서 코드를 직접 읽어 정리한 현재 구조 조사 + 이 문서 작성 중 추가로 확인한 사실(아래 "사전 조사 결과") + 1차 설계에 대한 리뷰 피드백(정상 동시 예약은 막지만 타임아웃·재시작이 끼면 다시 중복 가능) 반영.

## 목표

1. **중복 예약 방지**: SRT/KTX가 동시에, 또 시간이 어긋나게(타임아웃·재시작 포함) 예약을 시도해도 목표 수보다 더 많이 예약되지 않게 한다.
2. **상태 분리**: SRT/KTX 각각의 상태(꺼짐/조회 중/예약 중/결과 확인 필요/목표 달성/오류/중지됨)를 화면에서 구분해서 보여준다.

## 범위 밖

- `frontend/`(Vite/React/TS 소스) — 실제 서비스에 쓰이지 않는 방치된 구현체.
- SRT/KTX 구간을 독립적으로 고르는 UI, `monitor_srt` on/off 토글.
- `korail2` 실제 로그인/조회 동작의 실계정 검증(모의 객체로 하는 단위 테스트는 포함).
- **프로세스(백엔드) 재시작을 넘어선 영속화.** 아래 설계는 프로세스가 살아있는 동안의 중복 예약은 막지만, `ReservationCoordinator`의 상태는 메모리에만 있다. 백엔드 프로세스 자체가 죽었다 다시 뜨면(정전, 수동 재실행 등) 이전에 진행 중이던 예약 시도 정보는 사라진다. **이 설계는 그 경우까지는 보호하지 않는다** — README와 이 문서 모두에 이 한계를 명시한다.

## 사전 조사 결과 (설계의 전제)

- 실제 사용자가 여는 화면은 `run_web.bat`/`run_frontend.bat`가 여는 `http://127.0.0.1:8000/`이며, `backend/app.py`의 `GET /`가 서빙하는 **`SRT Monitor _standalone_.html`**이다. `frontend/src/`(Vite)는 이 경로에 전혀 관여하지 않는다.
- `SRT Monitor _standalone_.html`은 `<script type="__bundler/manifest">`에 앱 코드(UUID `9823f60b-1b1e-44e2-b967-0f350d23a6ac`)를 gzip+base64로 담고 있고, 프로젝트 루트의 `extracted_app.js`는 이를 디코딩한 것과 **바이트 단위로 동일**함을 직접 확인했다 — `extracted_app.js`를 편집하는 것이 실제 화면을 편집하는 것이다.
- 재압축 절차는 `docs/plans/2026-05-26-ktx-support.md` Task 7 Step 13에 스크립트로 남아 있다(저장소에 스크립트 파일 자체는 없음 — 재현해서 사용).
- `extracted_app.js`는 이미 KTX 토글, Korail 계정 입력, 구간별 KTX 자동판단, `/api/config/public`+`/api/config/secrets` 정확한 호출을 구현하고 있다. **이번 문서에서 다루는 "KTX UI 추가"는 이미 완료된 부분을 다시 만드는 게 아니라, 예약 조정·상태 표시만 최소로 덧붙이는 것이다.**
- `asyncio.to_thread()`로 감싼 블로킹 호출은, 바깥 코루틴이 타임아웃/취소돼도 **스레드 안에서 실제로 나간 HTTP 요청 자체를 중단시키지 못한다.** 즉 "타임아웃 = 예약 안 됐다"가 아니라 "타임아웃 = 결과를 모른다"이다. 이번 설계 전체가 이 전제를 기준으로 한다.

---

## 1. `ReservationCoordinator` (신규: `backend/reservation_coordinator.py`)

FastAPI/스키마에 의존하지 않는 순수 파이썬 클래스. **예약 시도 단위를 식별자로 추적**하고, **시작/중지가 진행 중인 시도 정보를 함부로 지우지 않도록** 설계한다.

```python
import asyncio
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Literal

Service = Literal["srt", "ktx"]


class AttemptOutcome(str, Enum):
    RESERVED = "reserved"          # 예약이 확인됐고 유지됨 → 목표 수에 반영
    NOT_RESERVED = "not_reserved"  # 예약 실패 확정 또는 취소 완료 확인됨 → 다음 시도 허용
    UNCERTAIN = "uncertain"        # 결과를 확정할 수 없음 → 양쪽 다 차단, 수동 확인 필요


@dataclass(frozen=True)
class Attempt:
    id: str
    service: Service


BlockReason = Literal["stopped", "uncertain", "goal_reached"]


class ReservationCoordinator:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._count = 0
        self._max = 1
        self._stopped = True  # 시작 전에는 기본적으로 새 시도를 허용하지 않음
        self._in_flight: Attempt | None = None
        self._uncertain: Attempt | None = None  # 동시에 최대 1건만 존재 가능 (아래 불변식 참고)

    # ---- 조회 ----
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

    # ---- 세션 관리 (시작/중지) ----
    def update_max(self, max_reservations: int) -> None:
        """이미 활성 세션인 상태에서 목표 수만 갱신 (카운트/차단 상태는 건드리지 않음)."""
        self._max = max_reservations

    def begin_or_resume_session(self, max_reservations: int) -> None:
        """양쪽 서비스가 모두 정지된 상태에서 /api/start 호출 시 사용.

        진행 중이거나 결과 미확인 상태(in_flight/uncertain)가 없을 때만
        카운트를 0으로 되돌린다. 있으면 이전 세션의 연장으로 취급해
        카운트를 보존한 채 목표 수만 갱신한다 — 재시작으로 중복 예약
        가능성을 만들지 않기 위함.
        """
        self._max = max_reservations
        self._stopped = False
        if self._in_flight is None and self._uncertain is None:
            self._count = 0

    def mark_stopped(self) -> None:
        """/api/stop: 새 시도만 차단한다. count/uncertain은 보존."""
        self._stopped = True

    # ---- 예약 시도 (exactly-once 완료) ----
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
                # 이미 처리된 시도이거나(중복 완료 처리), 이 시도가 활성 in_flight가
                # 아닌 경우(지연 콜백) — 현재 상태를 건드리지 않고 무시한다.
                return
            self._in_flight = None
            if outcome is AttemptOutcome.RESERVED:
                self._count += 1
            elif outcome is AttemptOutcome.UNCERTAIN:
                self._uncertain = attempt

    # ---- 결과 확인 필요 상태 해소 ----
    def resolve_uncertain(self, found: bool) -> None:
        """사용자가 SRT 앱/코레일톡을 직접 확인한 뒤 호출.

        found=True  -> 실제로 예약이 있었음 -> 목표 수에 반영하고 차단 해제
        found=False -> 예약이 없었음을 확인 -> 그냥 차단만 해제
        """
        if self._uncertain is None:
            return
        self._uncertain = None
        if found:
            self._count += 1
```

**불변식:** `_uncertain`이 설정되는 순간부터 `blocked_reason`이 `"uncertain"`이 되어 `try_begin_attempt()`가 항상 `None`을 반환하므로, `resolve_uncertain()`이 호출되기 전까지 새로운 `_in_flight` 시도가 생길 수 없다. 따라서 `_uncertain`은 항상 최대 1건이다(리스트가 아니라 단일 값으로 충분).

`_lock`은 "차단 여부 확인 → 시도 시작 표시"와 "결과 1회 반영"만 원자적으로 만들기 위한 것이며, 예약 API 호출(초 단위로 걸림) 자체를 락 안에서 실행하지 않는다.

### `/api/start`, `/api/stop` 연동 (app.py)

`get_srt`/`get_ktx`와 동일한 패턴으로 의존성을 하나 추가한다:

```python
def get_coordinator(request: Request) -> ReservationCoordinator:
    return request.app.state.reservation_coordinator

CoordinatorDep = Annotated[ReservationCoordinator, Depends(get_coordinator)]
```

`lifespan`에서 `app.state.reservation_coordinator = ReservationCoordinator()`로 한 번 생성한다.

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
    return _merged_status(srt, ktx)


@app.post("/api/stop")
async def stop_monitor(srt: SrtService, ktx: KtxService, coordinator: CoordinatorDep):
    coordinator.mark_stopped()
    await asyncio.gather(srt.stop(), ktx.stop())
    return _merged_status(srt, ktx)


@app.post("/api/reservation-lock/resolve")
def resolve_reservation_lock(payload: ReservationLockResolve, coordinator: CoordinatorDep) -> ReservationLockStatus:
    coordinator.resolve_uncertain(found=payload.found)
    return ReservationLockStatus(**coordinator.public_state())
```

이걸로 리뷰에서 지적된 표를 그대로 구현한다:

| 상황 | 처리 |
|---|---|
| 실행 중 `/api/start` 재호출 | `already_active=True` → `update_max`만, 카운트/차단 상태 보존 |
| `/api/stop` | `mark_stopped()` — 새 시도만 차단, count/uncertain 보존 |
| 예약 요청이 아직 처리 중(`in_flight`) 상태에서 새 감시 시작 | `begin_or_resume_session`이 `in_flight is not None`을 보고 카운트 보존 — 감시(조회)는 재개되지만 `try_begin_attempt`는 여전히 `in_flight`가 남아있는 한 새 시도를 막음(다만 실제로는 서비스가 재시작되며 `in_flight`를 만든 코루틴 자체가 이미 죽어 있을 수 있음 — 이 경우는 사실상 "결과 불명 채로 영영 안 끝나는 in_flight"가 될 수 있으므로, 서비스 쪽에서 `CancelledError` 시 **반드시** `finish_attempt(UNCERTAIN)`을 호출해 `in_flight`를 `uncertain`으로 전환시켜야 한다 — 아래 2장 참고) |
| 예약 결과 불확실(`uncertain`) | `resolve_uncertain()` 호출 전까지 차단 유지, 재시작해도 안 풀림 |
| 새 감시 회차 시작(양쪽 다 정지 + `in_flight`/`uncertain` 없음) | `begin_or_resume_session`이 카운트를 0으로 리셋 |

---

## 2. 예약 시도 흐름 — 모든 종료 경로에서 정확히 1회 `finish_attempt` (`monitor_service.py`/`ktx_monitor_service.py`)

두 파일에 동일한 패턴을 적용한다(SRT 기준 예시):

```python
if config.auto_reserve:
    attempt = await self._coordinator.try_begin_attempt("srt")
    if attempt is None:
        # 목표 달성/정지됨/다른 쪽이 결과 확인 대기 중 — 이번 폴링에서는 시도하지 않는다.
        break

    try:
        result = await self._run_srt_call(
            "예약 시도",
            lambda train=train, session=session: self._try_reserve(config, session, train),
        )
    except SrtCallTimeout as exc:
        await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
        seen.add(key)
        session = None
        error_streak += 1
        self._emit("warn", _UNCERTAIN_MESSAGE.format(detail=str(exc)))
        break
    except asyncio.CancelledError:
        # await 자체는 cancel() 한 번만으로는 재취소되지 않으므로 안전하게 완료 처리 가능.
        await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
        self._emit("warn", "예약 요청 도중 감시가 취소되어 결과가 불확실합니다. " + _UNCERTAIN_MESSAGE.format(detail=""))
        raise
    except Exception as exc:
        # SRT 라이브러리가 문서화되지 않은 예외를 던지는 경우까지 포함해,
        # 요청이 실제로 서버에 도달했는지 알 수 없으므로 보수적으로 UNCERTAIN 처리.
        await self._coordinator.finish_attempt(attempt, AttemptOutcome.UNCERTAIN)
        error_streak += 1
        self._emit("error", f"예약 시도 중 예상하지 못한 오류: {exc}. " + _UNCERTAIN_MESSAGE.format(detail=""))
        break
    else:
        await self._coordinator.finish_attempt(attempt, result.outcome)
        if result.outcome is AttemptOutcome.RESERVED:
            seen.add(key)
            self._reserved_count += 1
        elif result.outcome is AttemptOutcome.NOT_RESERVED:
            seen.add(key)
```

`_UNCERTAIN_MESSAGE`는 리뷰에서 요구한 문구로 통일한다:

> "예약 결과를 확인할 수 없어 SRT·KTX 자동 예약을 보류했습니다. SRT 앱 또는 코레일톡에서 예약 내역을 확인해주세요."

(기존 초안의 "중지 후 다시 시작하세요"라는 문구는 삭제한다 — 재시작으로는 풀리지 않고, 반드시 `/api/reservation-lock/resolve` 호출로만 풀리기 때문에 잘못된 안내였다.)

`self._reserved_count`(서비스별 카운터)는 화면에 "어느 쪽이 실제로 잡았는지" 표시하는 용도로 유지하되, 목표 판정에는 더 이상 쓰지 않는다(코디네이터의 `count`/`max`가 판정한다).

---

## 3. `try_reserve_train()` / `try_reserve_train_ktx()`의 반환값을 3-state로 변경 (`srt_logic.py`/`ktx_logic.py`)

지금은 `bool`을 반환하며 호출부가 "성공/실패"로만 해석한다. 아래처럼 결과 타입을 명시적으로 바꾼다.

```python
@dataclass
class ReserveResult:
    outcome: AttemptOutcome
    reservation_id: str = ""
    reason: str = ""
```

`try_reserve_train()` 내부 분기와 새 반환값의 대응 (SRT 기준, KTX도 동일 구조):

| 기존 코드 경로 | 기존 반환 | 새 반환 | 근거 |
|---|---|---|---|
| `srt.reserve()`가 `SRTError`로 실패 | `False` | `NOT_RESERVED` | 예약 객체가 생성되지 못했다고 라이브러리가 확정적으로 응답함 |
| 좌석 조건 불일치 + 자동취소 **성공** | `False` | `NOT_RESERVED` | 취소가 확인됨 |
| 좌석 조건 불일치 + 자동취소 **실패**(`cancel()`이 `SRTError`) | `True` | **`UNCERTAIN`** | 취소 성공 여부를 모름 — 기존 설계는 이걸 "예약 유지"로 간주해 `True`를 반환했지만, 실제로는 취소됐을 수도 있다. `RESERVED`로 잘못 세면 다른 서비스가 정당한 기회를 놓치고, `NOT_RESERVED`로 잘못 세면 중복 위험이 남는다 — 그래서 사람이 확인해야 하는 `UNCERTAIN`이 맞다 |
| 좌석 조건 불일치 + 자동취소 **비활성화**(의도적으로 유지) | `True` | `RESERVED` | 조건은 안 맞지만 예약은 실제로 살아있음이 확정적임 |
| 정상 예약 완료 | `True` | `RESERVED` | 확정 |

텔레그램 전송 실패는 지금도 `_send_telegram()` 내부에서 예외를 잡아 별도로 경고만 남기고 호출자에게 전파하지 않는다 — **이 성질을 그대로 유지**하고, 회귀 테스트로 고정한다(아래 7장).

`monitor_service.py`/`ktx_monitor_service.py`는 `result.outcome`을 그대로 코디네이터에 넘기기만 하면 된다(2장 코드 참고).

### `ktx_logic.py`의 `_reserve()` — TypeError 재시도를 시그니처 사전 검사로 교체

현재는 `ktx.reserve(train, **kwargs)`를 여러 kwargs 조합으로 반복 호출하다가 `TypeError`가 나면 다음 조합으로 넘어간다. 문제는 **이미 실제 예약 요청이 서버로 나간 뒤에 응답 처리 과정에서 `TypeError`가 나도 똑같이 "재시도"로 오인해 또 호출한다**는 것이다. 요청을 보내기 전에 시그니처를 검사해서 정확히 한 번만 호출하도록 바꾼다.

```python
import inspect

def _reserve(ktx: Any, train: Any, seat_type: Any, passengers: list[Any], prefer_window: bool) -> Any:
    try:
        params = inspect.signature(ktx.reserve).parameters
    except (TypeError, ValueError):
        params = None  # 시그니처를 읽을 수 없는 객체(mock 등) — 가장 표준적인 조합으로 시도

    kwargs: dict[str, Any] = {"passengers": passengers}
    if params is None or "option" in params:
        kwargs["option"] = seat_type
    elif "reserve_option" in params:
        kwargs["reserve_option"] = seat_type
    elif "special_seat" in params:
        kwargs["special_seat"] = seat_type
    if params is None or "window_seat" in params:
        kwargs["window_seat"] = prefer_window

    return ktx.reserve(train, **kwargs)  # 정확히 1회 호출 — 호출 후 TypeError로 재시도하지 않음
```

이건 실제 계정 없이도 `ktx.reserve`를 다른 시그니처(`option`/`reserve_option`/`special_seat`, `window_seat` 유무)를 가진 목(mock) 함수로 바꿔가며 "정확히 1번만 호출됐는지"로 검증 가능하다(7장 테스트 계획).

---

## 4. 상태/이벤트 스키마 (`backend/schemas.py`)

```python
class ServicePhase(str, Enum):
    OFF = "off"                          # 이 회차에 감시 대상이 아님(계정 없음/구간 미지원/monitor_ktx=False 등)
    SEARCHING = "searching"
    RESERVING = "reserving"
    NEEDS_CONFIRMATION = "needs_confirmation"  # 이 서비스가 만든 시도가 uncertain 상태
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


class EventItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    level: str
    message: str
    timestamp: str
    service: Literal["srt", "ktx"]


class ReservationLockResolve(BaseModel):
    found: bool
```

`MonitorStatus`가 `ServiceStatus`를 상속하므로 최상위 필드(합산값, 기존과 동일 의미)는 하위 호환된다. `srt`/`ktx`/`reservation_lock`은 추가 필드다.

각 서비스는 `_run()` 진행 단계마다 `self._phase`를 갱신한다: 조회 시작 전 `SEARCHING`, 예약 시도 진입 시 `RESERVING`, `finish_attempt`가 `UNCERTAIN`을 반환한 시점에 `NEEDS_CONFIRMATION`(→ `resolve_uncertain()` 이후 서비스가 다음 루프에서 자연히 `SEARCHING`/`GOAL_REACHED`로 갱신), 목표 달성으로 루프 종료 시 `GOAL_REACHED`, `stop()` 처리 시 `STOPPED`, 시작 조건 미충족(계정 없음 등)으로 아예 시작 안 함 → `OFF`.

`_emit()`은 `EventItem` 생성 시 `service="srt"`/`service="ktx"`를 명시한다.

### `app.py`의 병합 함수

```python
def _merged_status(srt: MonitorService, ktx: KtxMonitorService, coordinator: ReservationCoordinator) -> MonitorStatus:
    ss, ks = srt.status(), ktx.status()
    return MonitorStatus(
        running=ss.running or ks.running,
        phase=ss.phase,  # 최상위 phase는 참고용으로만 SRT 기준을 쓰고, 화면은 srt/ktx 개별 phase를 우선 사용
        reserved_count=coordinator.count,
        last_message=ks.last_message if ks.running else ss.last_message,
        started_at=ss.started_at or ks.started_at,
        error=ks.error or ss.error,
        srt=ss,
        ktx=ks,
        reservation_lock=ReservationLockStatus(**coordinator.public_state()),
    )
```

최상위 `reserved_count`는 이제 **코디네이터의 공용 카운트**를 반환한다(기존에는 `ss.reserved_count + ks.reserved_count`였는데, uncertain 상태에서는 이 합산이 실제 목표 진행도와 어긋날 수 있어 코디네이터 값이 정답이다).

`_merged_events`는 변경 없음.

---

## 5. 화면 반영 (`extracted_app.js` → 재압축)

리뷰 지적대로, "결과 확인 필요"는 툴팁이 아니라 화면에 직접 노출해야 하므로 최소 범위를 아래로 조정한다.

- **상태 카드**: 기존 `status-stats` 아래에 SRT/KTX 두 줄 추가. 각 줄은 `backendStatus.srt.phase`/`backendStatus.ktx.phase`를 한국어 라벨로 매핑해 표시(꺼짐/조회 중/예약 시도 중/결과 확인 필요/목표 달성/오류/중지됨).
- **예약 잠금 배너(신규)**: `backendStatus.reservation_lock.blocked && reservation_lock.reason === 'uncertain'`일 때, 상태 카드 바로 아래 눈에 띄는 배너를 렌더링:
  - 문구: "예약 결과를 확인할 수 없습니다 (SRT / KTX). SRT 앱 또는 코레일톡에서 예약 내역을 직접 확인한 뒤 아래에서 선택하세요."
  - 버튼 두 개: **"예약이 있었어요"**(`POST /api/reservation-lock/resolve {found:true}`) / **"예약이 없었어요"**(`{found:false}`).
  - 버튼 클릭 후 `backendStatus`를 즉시 갱신(`refreshAll()` 재사용).
- **이벤트 로그**: `eventFromApi()`에 `service: item.service` 추가, 로그 줄에 `<span className={`svc-badge ${ev.service}`}>{ev.service.toUpperCase()}</span>` 배지 추가.
- `emptyStatus`에 `srt`/`ktx`(각각 `{running:false, phase:'off', reserved_count:0, last_message:'대기 중', started_at:null, error:null}`)와 `reservation_lock`(`{blocked:false, reason:null, count:0, max:1, uncertain_service:null}`) 기본값 추가.

편집 후 `docs/plans/2026-05-26-ktx-support.md` Task 7 Step 13의 스크립트를 재현한 `rebundle_html.py`로 재압축한다(`.bak` 백업 포함).

---

## 6. README/문서 정리

- `README.md` 5.6~5.7장(현재 "프론트엔드에 KTX UI 없음"이라고 잘못 적힌 부분)과 6장을, 이번에 확인한 사실(standalone HTML이 실제 화면이고 이미 KTX UI가 있다는 점)로 정정한다.
- 이 설계 문서의 "범위 밖" 항목 중 **프로세스 재시작을 넘어선 영속화는 지원하지 않는다는 한계**를 README에도 명시한다.

---

## 7. 테스트 계획

**`tests/test_reservation_coordinator.py`(신규)**
- 정상 흐름: `try_begin_attempt` → `finish_attempt(RESERVED)` → `count` 증가, `goal_reached` 이후 추가 `try_begin_attempt`는 `None`.
- 동시성: `asyncio.gather`로 SRT/KTX가 동시에 `try_begin_attempt()` 호출 시 **정확히 하나만** `Attempt`를 받는지.
- **`UNCERTAIN` 이후 `resolve_uncertain()` 전까지 계속 차단**되는지, `begin_or_resume_session()`을 다시 불러도 안 풀리는지(리뷰 지적 1 핵심).
- **실행 중 재시작이 카운트를 보존**하는지: `in_flight` 상태에서 `begin_or_resume_session()` 호출 시 `count`가 리셋되지 않는지.
- **정상 새 세션은 리셋**되는지: `in_flight`/`uncertain` 모두 없는 상태에서 `begin_or_resume_session()` 호출 시 `count`가 0이 되는지.
- **`mark_stopped()`는 count/uncertain을 보존**하는지.
- **중복 완료 처리 무시**: 같은 `Attempt`로 `finish_attempt()`를 두 번 호출해도 `count`가 한 번만 증가하는지. 이미 완료된(`_in_flight`가 아닌) 오래된 `Attempt`로 뒤늦게 `finish_attempt()`가 들어와도 상태가 바뀌지 않는지.
- `resolve_uncertain(found=True/False)`가 각각 count 반영/미반영하며 차단을 푸는지.

**`tests/test_srt_logic.py`/`tests/test_ktx_logic.py`(신규 또는 기존 확장)**
- `try_reserve_train()`/`try_reserve_train_ktx()`가 위 3장 표의 5가지 경로에서 각각 올바른 `outcome`을 반환하는지(특히 "자동취소 실패 → UNCERTAIN"이 핵심 회귀 케이스).
- **예약 성공 + 텔레그램 전송 실패**여도 `outcome`이 `RESERVED`로 유지되는지(회귀 고정).
- `_reserve()`가 `option`/`reserve_option`/`special_seat`/시그니처 불명 각각에서 **`ktx.reserve`를 정확히 1번만** 호출하는지.

**`tests/test_monitor_service.py`/`tests/test_ktx_monitor_service.py`**
- 코디네이터가 `blocked_reason`을 반환하는 상태면 예약 시도 자체를 하지 않는지.
- 예약 호출에서 `asyncio.CancelledError`/예상 밖 `Exception`이 나도 `finish_attempt(UNCERTAIN)`이 정확히 호출되는지(태스크 취소 경로 포함 — `task.cancel()` 후 `_run()`이 `CancelledError`를 받는 지점을 목으로 재현).

**`tests/test_app.py`(신규, FastAPI TestClient 기반) 또는 기존 통합 테스트 위치**
- 실행 중 `/api/start` 재호출 시 코디네이터 `count`/`blocked_reason`이 안 변하는지.
- `/api/stop` 후에도 `uncertain` 상태가 유지되는지, `/api/start` 재호출로 우회되지 않는지.
- `/api/reservation-lock/resolve`가 정확히 상태를 갱신하는지.

프론트엔드(`extracted_app.js`)는 자동 테스트 대상이 아니므로, 구현 단계에서 브라우저로 수동 확인한다: 상태 카드 SRT/KTX 줄 노출, 잠금 배너 노출/버튼 동작, 이벤트 로그 배지.
