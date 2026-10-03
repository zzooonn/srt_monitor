# KTX 동시 모니터링 지원 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SRT Monitor에 KTX(Korail) 모니터링을 병렬로 추가해, SRT와 KTX를 동시에 감시하고 빈 자리가 나오면 예약한다.

**Architecture:** `KtxMonitorService`를 `MonitorService`와 독립적으로 운영한다. 두 서비스는 각자 세션과 예약 카운터를 가지며, `app.py`가 start/stop/events를 합산 제공한다. 프론트엔드는 HTML 번들 내 JSX를 수정 후 재압축·재삽입한다.

**Tech Stack:** Python 3.11+, `korail2`, FastAPI, React JSX (Babel standalone via HTML bundle), gzip/base64 rebundle script

---

## 파일 맵

| 파일 | 작업 |
|------|------|
| `requirements-web.txt` | `korail2` 추가 |
| `backend/stations.py` | `KTX_STATIONS`, `KTX_STATION_NAMES` 추가 |
| `backend/schemas.py` | KTX 공개 필드 + Korail 시크릿 필드 추가 |
| `backend/config_store.py` | `_SECRETS_FIELDS`에 korail 키 추가 |
| `backend/ktx_logic.py` | 신규: KTX 예약·좌석 검증 로직 |
| `backend/ktx_monitor_service.py` | 신규: KTX 전용 모니터링 루프 |
| `backend/app.py` | 두 서비스 등록, 엔드포인트 수정, `/api/stations/ktx` 추가 |
| `tests/test_schemas.py` | KTX 스키마 테스트 추가 |
| `tests/test_config_store.py` | Korail 시크릿 테스트 추가 |
| `tests/test_ktx_logic.py` | 신규: ktx_logic 단위 테스트 |
| `SRT Monitor _standalone_.html` | JSX 수정 후 재번들 (Python 스크립트로 자동화) |

---

## korail2 API 요약 (구현 참조용)

```python
from korail2 import (
    Korail, KorailError, NeedToLoginError, NoResultsError, SoldOutError,
    AdultPassenger, ChildPassenger, SeniorPassenger,
    ReserveOption, TrainType,
)

# 로그인
korail = Korail(korail_id, korail_pw, auto_login=True, want_feedback=False)

# 열차 조회 — dep/arr는 한글 역명, time은 "HHMMSS"
trains = korail.search_train(
    dep="서울", arr="부산",
    date="20260601", time="200000",
    train_type=TrainType.KTX,   # 100
    include_no_seats=True,
)

# 열차 속성
train.train_no            # 열차 번호 (str)
train.dep_name            # 출발역 이름
train.arr_name            # 도착역 이름
train.dep_time            # "HHMMSS"
train.arr_time            # "HHMMSS"
train.has_general_seat()  # 일반실 여부 (bool)
train.has_special_seat()  # 특실 여부 (bool)

# 예약
reservation = korail.reserve(
    train,
    passengers=[AdultPassenger(count=2)],
    option=ReserveOption.GENERAL_FIRST,
)
reservation.rsv_id        # 예약번호

# 취소
korail.cancel(reservation)

# 에러 계층
NeedToLoginError   # 세션 만료 → session = None 후 재로그인
NoResultsError     # 결과 없음 → 정상 (다음 폴 대기)
SoldOutError       # 매진 → 정상 (다음 폴 대기)
KorailError        # 기타 오류
```

---

## Task 1: 의존성 및 역 목록 추가

**Files:**
- Modify: `requirements-web.txt`
- Modify: `backend/stations.py`

- [ ] **Step 1: korail2 requirements에 추가**

`requirements-web.txt` 끝에 한 줄 추가:
```
korail2>=1.0
```

- [ ] **Step 2: KTX 역 목록을 stations.py에 추가**

`backend/stations.py` 파일 끝에 추가:

```python
KTX_STATIONS = [
    {"name": "서울", "code": "0001", "lines": ["공통"]},
    {"name": "용산", "code": "0002", "lines": ["공통"]},
    {"name": "광명", "code": "0004", "lines": ["공통"]},
    {"name": "천안아산", "code": "0502", "lines": ["공통"]},
    {"name": "오송", "code": "0297", "lines": ["공통"]},
    {"name": "대전", "code": "0010", "lines": ["경부선", "경전선", "동해선"]},
    {"name": "김천(구미)", "code": "0507", "lines": ["경부선", "동해선"]},
    {"name": "서대구", "code": "0506", "lines": ["경부선"]},
    {"name": "동대구", "code": "0015", "lines": ["경부선", "경전선", "동해선"]},
    {"name": "신경주", "code": "0508", "lines": ["경부선"]},
    {"name": "울산(통도사)", "code": "0509", "lines": ["경부선"]},
    {"name": "부산", "code": "0020", "lines": ["경부선"]},
    {"name": "공주", "code": "0514", "lines": ["호남선", "전라선"]},
    {"name": "익산", "code": "0030", "lines": ["호남선", "전라선"]},
    {"name": "정읍", "code": "0033", "lines": ["호남선"]},
    {"name": "광주송정", "code": "0036", "lines": ["호남선"]},
    {"name": "나주", "code": "0037", "lines": ["호남선"]},
    {"name": "목포", "code": "0041", "lines": ["호남선"]},
    {"name": "전주", "code": "0045", "lines": ["전라선"]},
    {"name": "남원", "code": "0048", "lines": ["전라선"]},
    {"name": "순천", "code": "0051", "lines": ["전라선"]},
    {"name": "여수EXPO", "code": "0053", "lines": ["전라선"]},
    {"name": "창원중앙", "code": "0512", "lines": ["경전선"]},
    {"name": "진주", "code": "0063", "lines": ["경전선"]},
    {"name": "포항", "code": "0515", "lines": ["동해선"]},
]

KTX_STATION_NAMES = {s["name"] for s in KTX_STATIONS}
```

- [ ] **Step 3: korail2 설치 확인**

```
pip install korail2
```

Expected: `Successfully installed korail2-...` 또는 `already satisfied`

- [ ] **Step 4: 커밋**

```bash
git add requirements-web.txt backend/stations.py
git commit -m "feat: korail2 의존성 및 KTX 역 목록 추가"
```

---

## Task 2: schemas.py KTX 필드 추가 + 테스트

**Files:**
- Modify: `backend/schemas.py`
- Modify: `tests/test_schemas.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_schemas.py` 끝에 추가:

```python
def test_public_config_ktx_defaults():
    c = PublicConfig()
    assert c.monitor_ktx is False
    assert c.ktx_departure_station == "서울"
    assert c.ktx_arrival_station == "부산"
    assert c.ktx_check_special is True
    assert c.ktx_check_normal is True


def test_ktx_station_valid():
    c = PublicConfig(monitor_ktx=True, ktx_departure_station="서울", ktx_arrival_station="부산")
    assert c.ktx_departure_station == "서울"


def test_ktx_station_invalid_raises_when_monitor_ktx_true():
    with pytest.raises(ValueError, match="지원하지 않는 KTX 역"):
        PublicConfig(monitor_ktx=True, ktx_departure_station="없는역", ktx_arrival_station="부산")


def test_ktx_station_invalid_ignored_when_monitor_ktx_false():
    c = PublicConfig(monitor_ktx=False)
    assert c.ktx_departure_station == "서울"


def test_monitor_config_has_korail_fields():
    mc = MonitorConfig(korail_id="myid", korail_password="mypw")
    assert mc.korail_id == "myid"
    assert mc.korail_password == "mypw"
```

- [ ] **Step 2: 테스트 실패 확인**

```
python -m pytest tests/test_schemas.py::test_public_config_ktx_defaults -v
```

Expected: `FAILED` (AttributeError)

- [ ] **Step 3: schemas.py 수정**

`backend/schemas.py` 상단의 import 수정:
```python
from .stations import SRT_STATION_NAMES, KTX_STATION_NAMES
```

`PublicConfig` 클래스에서 기존 `@field_validator("departure_station", "arrival_station")` 블록 바로 다음에 KTX 필드와 validator 추가:

```python
    monitor_ktx: bool = False
    ktx_departure_station: str = "서울"
    ktx_arrival_station: str = "부산"
    ktx_check_special: bool = True
    ktx_check_normal: bool = True

    @model_validator(mode="after")
    def validate_ktx_stations(self) -> "PublicConfig":
        if not self.monitor_ktx:
            return self
        if self.ktx_departure_station not in KTX_STATION_NAMES:
            raise ValueError(f"지원하지 않는 KTX 역입니다: {self.ktx_departure_station}")
        if self.ktx_arrival_station not in KTX_STATION_NAMES:
            raise ValueError(f"지원하지 않는 KTX 역입니다: {self.ktx_arrival_station}")
        return self
```

`MonitorConfig` 클래스에 추가:
```python
    korail_id: str = ""
    korail_password: str = ""
```

`SecretsPayload` 클래스에 추가:
```python
    korail_id: str = ""
    korail_password: str = ""
```

`SecretsStatus` 클래스에 추가:
```python
    korail_id_set: bool
    korail_password_set: bool
```

- [ ] **Step 4: 테스트 통과 확인**

```
python -m pytest tests/test_schemas.py -v
```

Expected: 모든 테스트 PASSED

- [ ] **Step 5: 커밋**

```bash
git add backend/schemas.py tests/test_schemas.py
git commit -m "feat: PublicConfig/MonitorConfig에 KTX 필드 추가"
```

---

## Task 3: config_store.py KTX 시크릿 처리 + 테스트

**Files:**
- Modify: `backend/config_store.py`
- Modify: `tests/test_config_store.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_config_store.py` 끝에 추가:

```python
def test_save_secrets_saves_korail_id(tmp_config):
    save_secrets(SecretsPayload(korail_id="kid", korail_password="kpw"))
    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["korail_id"] == "kid"
    assert data["korail_password"] == "kpw"


def test_load_secrets_status_includes_korail(tmp_config):
    tmp_config.write_text(
        json.dumps({"srt_id": "s", "srt_password": "p", "korail_id": "k", "korail_password": ""}),
        encoding="utf-8",
    )
    status = load_secrets_status()
    assert status.korail_id_set is True
    assert status.korail_password_set is False


def test_save_public_config_preserves_korail_secrets(tmp_config):
    tmp_config.write_text(
        json.dumps({"korail_id": "k", "korail_password": "p", "departure_station": "수서", "arrival_station": "동탄"}),
        encoding="utf-8",
    )
    save_public_config(PublicConfig(departure_station="오송", arrival_station="수서"))
    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["korail_id"] == "k"
    assert data["korail_password"] == "p"
```

- [ ] **Step 2: 테스트 실패 확인**

```
python -m pytest tests/test_config_store.py::test_save_secrets_saves_korail_id -v
```

Expected: `FAILED`

- [ ] **Step 3: config_store.py 수정**

`backend/config_store.py`에서 `_SECRETS_FIELDS` 교체:
```python
_SECRETS_FIELDS = {
    "srt_id", "srt_password",
    "korail_id", "korail_password",
    "telegram_bot_token", "telegram_chat_id",
}
```

`load_secrets_status()` 함수 교체:
```python
def load_secrets_status() -> SecretsStatus:
    data = _read_raw()
    return SecretsStatus(
        srt_id_set=bool(str(data.get("srt_id", "")).strip()),
        srt_password_set=bool(str(data.get("srt_password", "")).strip()),
        korail_id_set=bool(str(data.get("korail_id", "")).strip()),
        korail_password_set=bool(str(data.get("korail_password", "")).strip()),
        telegram_bot_token_set=bool(str(data.get("telegram_bot_token", "")).strip()),
        telegram_chat_id_set=bool(str(data.get("telegram_chat_id", "")).strip()),
    )
```

- [ ] **Step 4: 테스트 통과 확인**

```
python -m pytest tests/test_config_store.py -v
```

Expected: 모든 테스트 PASSED

- [ ] **Step 5: 커밋**

```bash
git add backend/config_store.py tests/test_config_store.py
git commit -m "feat: config_store에 korail 시크릿 처리 추가"
```

---

## Task 4: ktx_logic.py 생성 + 테스트

**Files:**
- Create: `backend/ktx_logic.py`
- Create: `tests/test_ktx_logic.py`

- [ ] **Step 1: 실패하는 테스트 작성**

새 파일 `tests/test_ktx_logic.py`:

```python
"""ktx_logic.py 단위 테스트. korail2 열차 객체를 mock으로 대체한다."""
from unittest.mock import MagicMock
import pytest

from backend.ktx_logic import available_seat_type_ktx, seat_status_str_ktx, make_passengers_ktx
from backend.schemas import MonitorConfig


def _cfg(**kwargs) -> MonitorConfig:
    defaults = dict(
        srt_id="x", srt_password="x",
        korail_id="k", korail_password="p",
        ktx_check_normal=True, ktx_check_special=True,
    )
    defaults.update(kwargs)
    return MonitorConfig(**defaults)


def _train(general: bool = False, special: bool = False) -> MagicMock:
    t = MagicMock()
    t.has_general_seat.return_value = general
    t.has_special_seat.return_value = special
    return t


def test_available_no_seats_returns_none():
    assert available_seat_type_ktx(_cfg(), _train(False, False)) is None


def test_available_general_only():
    result = available_seat_type_ktx(_cfg(), _train(general=True, special=False))
    assert result == "GENERAL_ONLY"


def test_available_special_only():
    result = available_seat_type_ktx(_cfg(), _train(general=False, special=True))
    assert result == "SPECIAL_ONLY"


def test_available_both_prefers_general():
    result = available_seat_type_ktx(_cfg(), _train(general=True, special=True))
    assert result == "GENERAL_FIRST"


def test_check_normal_false_ignores_general():
    result = available_seat_type_ktx(_cfg(ktx_check_normal=False), _train(general=True, special=True))
    assert result == "SPECIAL_ONLY"


def test_check_special_false_ignores_special():
    result = available_seat_type_ktx(_cfg(ktx_check_special=False), _train(general=True, special=True))
    assert result == "GENERAL_ONLY"


def test_seat_status_str_contains_both_labels():
    s = seat_status_str_ktx(_cfg(), _train(general=True, special=False))
    assert "일반실" in s
    assert "특실" in s


def test_make_passengers_adults():
    from korail2 import AdultPassenger
    cfg = _cfg(adult_count=2, child_count=0, senior_count=0,
               disability_1_to_3_count=0, disability_4_to_6_count=0)
    passengers = make_passengers_ktx(cfg)
    assert len(passengers) == 1
    assert isinstance(passengers[0], AdultPassenger)
    assert passengers[0].count == 2


def test_make_passengers_mixed():
    from korail2 import AdultPassenger, ChildPassenger
    cfg = _cfg(adult_count=1, child_count=1, senior_count=0,
               disability_1_to_3_count=0, disability_4_to_6_count=0)
    passengers = make_passengers_ktx(cfg)
    types = {type(p) for p in passengers}
    assert AdultPassenger in types
    assert ChildPassenger in types


def test_make_passengers_fallback_when_zero():
    from korail2 import AdultPassenger
    cfg = _cfg(adult_count=0, child_count=0, senior_count=0,
               disability_1_to_3_count=0, disability_4_to_6_count=0)
    passengers = make_passengers_ktx(cfg)
    assert len(passengers) == 1
    assert isinstance(passengers[0], AdultPassenger)
```

- [ ] **Step 2: 테스트 실패 확인**

```
python -m pytest tests/test_ktx_logic.py -v
```

Expected: `ERROR` (ImportError)

- [ ] **Step 3: ktx_logic.py 구현**

새 파일 `backend/ktx_logic.py`:

```python
"""
KTX 모니터링 공통 비즈니스 로직 (korail2 라이브러리 사용).
"""
from collections.abc import Callable
from typing import Any

from korail2 import (
    AdultPassenger, ChildPassenger, SeniorPassenger,
    KorailError, ReserveOption,
)

from .schemas import MonitorConfig


def available_seat_type_ktx(cfg: MonitorConfig, train: Any) -> str | None:
    """예약 가능한 좌석 타입 문자열 반환. 없으면 None."""
    has_special = cfg.ktx_check_special and train.has_special_seat()
    has_normal = cfg.ktx_check_normal and train.has_general_seat()
    if not has_special and not has_normal:
        return None
    if has_special and has_normal:
        return ReserveOption.GENERAL_FIRST
    return ReserveOption.GENERAL_ONLY if has_normal else ReserveOption.SPECIAL_ONLY


def seat_status_str_ktx(cfg: MonitorConfig, train: Any) -> str:
    parts = []
    if cfg.ktx_check_special:
        parts.append(f"특실: {'✅' if train.has_special_seat() else '❌'}")
    if cfg.ktx_check_normal:
        parts.append(f"일반실: {'✅' if train.has_general_seat() else '❌'}")
    return " | ".join(parts)


def make_passengers_ktx(cfg: MonitorConfig) -> list[Any]:
    passengers: list[Any] = []
    if cfg.adult_count > 0:
        passengers.append(AdultPassenger(count=cfg.adult_count))
    if cfg.child_count > 0:
        passengers.append(ChildPassenger(count=cfg.child_count))
    if cfg.senior_count > 0:
        passengers.append(SeniorPassenger(count=cfg.senior_count))
    disability = cfg.disability_1_to_3_count + cfg.disability_4_to_6_count
    if disability > 0:
        passengers.append(AdultPassenger(count=disability))
    return passengers or [AdultPassenger(count=1)]


def try_reserve_train_ktx(
    cfg: MonitorConfig,
    korail: Any,
    train: Any,
    *,
    emit: Callable[[str, str], None],
    send_telegram: Callable[[str], None],
) -> bool:
    """KTX 열차 예약 시도. 예약이 살아있으면 True 반환."""
    option = available_seat_type_ktx(cfg, train)
    if option is None:
        return False

    try:
        reservation = korail.reserve(
            train,
            passengers=make_passengers_ktx(cfg),
            option=option,
        )
    except KorailError as exc:
        emit("error", f"[KTX] 예약 실패 ({train.train_no}호): {exc}")
        send_telegram(f"⚠️ <b>[KTX] 예약 실패</b>\n열차: {train.train_no}호\n사유: {exc}")
        return False

    seat_msg = f"예약번호: {reservation.rsv_id}"
    emit("info", f"[KTX] 예약 완료: {train.train_no}호 / {seat_msg}")
    send_telegram(
        f"🎉 <b>[KTX] 예약 완료!</b>\n"
        f"🚄 KTX {train.train_no}호\n"
        f"📍 {train.dep_name} → {train.arr_name}\n"
        f"💺 {seat_status_str_ktx(cfg, train)}\n"
        f"📋 {seat_msg}\n\n"
        f"⏰ <b>20분 내 Korail 앱에서 결제하세요!</b>",
    )
    return True
```

- [ ] **Step 4: 테스트 통과 확인**

```
python -m pytest tests/test_ktx_logic.py -v
```

Expected: 모든 테스트 PASSED

- [ ] **Step 5: 커밋**

```bash
git add backend/ktx_logic.py tests/test_ktx_logic.py
git commit -m "feat: ktx_logic.py 구현 및 테스트 추가"
```

---

## Task 5: KtxMonitorService 생성

**Files:**
- Create: `backend/ktx_monitor_service.py`

- [ ] **Step 1: ktx_monitor_service.py 생성**

새 파일 `backend/ktx_monitor_service.py`:

```python
"""
KTX 모니터링 서비스. MonitorService와 동일한 인터페이스를 제공한다.
"""
import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from korail2 import (
    Korail, KorailError, NeedToLoginError, NoResultsError,
    SoldOutError, TrainType,
)

from .ktx_logic import available_seat_type_ktx, try_reserve_train_ktx
from .schemas import EventItem, MonitorConfig, MonitorStatus

_KST = timezone(timedelta(hours=9))
_KORAIL_CALL_TIMEOUT_SECONDS = 60


class KtxCallTimeout(RuntimeError):
    pass


class KtxMonitorService:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._events: deque[EventItem] = deque(maxlen=300)
        self._reserved_count = 0
        self._started_at: str | None = None
        self._last_message = "대기 중"

    def status(self) -> MonitorStatus:
        return MonitorStatus(
            running=self.running,
            reserved_count=self._reserved_count,
            last_message=self._last_message,
            started_at=self._started_at,
        )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def recent_events(self) -> list[EventItem]:
        return list(self._events)

    async def start(self, config: MonitorConfig) -> MonitorStatus:
        if self.running:
            return self.status()
        if not config.monitor_ktx:
            return self.status()
        if not config.korail_id or not config.korail_password:
            self._emit("warn", "[KTX] Korail 계정이 설정되지 않아 KTX 감시를 건너뜁니다.")
            return self.status()

        self._reserved_count = 0
        self._started_at = datetime.now(_KST).isoformat(timespec="seconds")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(config, self._stop_event))
        self._emit("info", "[KTX] 모니터링을 시작했습니다.")
        return self.status()

    async def stop(self) -> MonitorStatus:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._task = None
        self._stop_event = None
        self._emit("info", "[KTX] 모니터링을 중지했습니다.")
        return self.status()

    def _emit(self, level: str, message: str) -> None:
        item = EventItem(
            level=level,
            message=message,
            timestamp=datetime.now(_KST).isoformat(timespec="seconds"),
        )
        self._events.append(item)
        self._last_message = message

    def _send_telegram(self, config: MonitorConfig, message: str) -> None:
        if not config.telegram_bot_token or not config.telegram_chat_id:
            return
        url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": config.telegram_chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            ).raise_for_status()
        except Exception as exc:
            self._emit("warn", f"[KTX] 텔레그램 전송 실패: {exc}")

    async def _run_call(self, label: str, func: Callable[[], Any]) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func),
                timeout=_KORAIL_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise KtxCallTimeout(
                f"[KTX] {label} 응답이 {_KORAIL_CALL_TIMEOUT_SECONDS}초 동안 없습니다."
            ) from exc

    async def _sleep_or_stop(self, stop_event: asyncio.Event, seconds: int) -> bool:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self, config: MonitorConfig, stop_event: asyncio.Event) -> None:
        await self._run_call(
            "텔레그램 시작 알림",
            lambda: self._send_telegram(
                config,
                f"🚄 <b>[KTX] 모니터링 시작</b>\n"
                f"구간: {config.ktx_departure_station} → {config.ktx_arrival_station}\n"
                f"날짜: {config.departure_date[:4]}/{config.departure_date[4:6]}/{config.departure_date[6:]}\n"
                f"인원: {config.passenger_count}명\n"
                f"자동예약: {'ON ✅' if config.auto_reserve else 'OFF ❌'}",
            ),
        )

        session: Korail | None = None
        seen: set[str] = set()
        error_streak = 0
        last_heartbeat = time.time()

        try:
            while not stop_event.is_set():
                if config.auto_reserve and self._reserved_count >= config.max_reservations:
                    self._emit("info", f"[KTX] 목표 예약 수 {config.max_reservations}건 달성")
                    break

                if time.time() - last_heartbeat >= config.heartbeat_interval:
                    await self._run_call(
                        "텔레그램 상태 알림",
                        lambda: self._send_telegram(
                            config,
                            f"💓 <b>[KTX] 모니터링 중</b>\n"
                            f"구간: {config.ktx_departure_station} → {config.ktx_arrival_station}",
                        ),
                    )
                    last_heartbeat = time.time()

                try:
                    if session is None:
                        self._emit("info", "[KTX] Korail 로그인 시도 중")
                        session = await self._run_call(
                            "Korail 로그인",
                            lambda: Korail(
                                config.korail_id,
                                config.korail_password,
                                auto_login=True,
                                want_feedback=False,
                            ),
                        )
                        self._emit("info", "[KTX] Korail 로그인 완료")

                    self._emit("info", "[KTX] 열차 조회 요청 중")
                    try:
                        trains = await self._run_call(
                            "KTX 열차 조회",
                            lambda: session.search_train(
                                dep=config.ktx_departure_station,
                                arr=config.ktx_arrival_station,
                                date=config.departure_date,
                                time=config.departure_time,
                                train_type=TrainType.KTX,
                                include_no_seats=True,
                            ),
                        )
                    except NoResultsError:
                        self._emit("info", "[KTX] 조회된 열차 없음")
                        error_streak = 0
                        trains = []

                    self._emit("info", f"[KTX] 열차 {len(trains)}편 조회")
                    error_streak = 0

                    for train in trains:
                        if train.dep_time > config.max_departure_time:
                            continue
                        if available_seat_type_ktx(config, train) is None:
                            continue

                        key = f"KTX{train.train_no}{train.dep_time}"
                        if key in seen:
                            continue

                        if config.auto_reserve:
                            self._emit("info", f"[KTX] 예약 시도 중: {train.train_no}호")
                            try:
                                reserved = await self._run_call(
                                    "KTX 예약",
                                    lambda t=train, s=session: try_reserve_train_ktx(
                                        config, s, t,
                                        emit=self._emit,
                                        send_telegram=lambda msg: self._send_telegram(config, msg),
                                    ),
                                )
                            except KtxCallTimeout as exc:
                                seen.add(key)
                                session = None
                                error_streak += 1
                                self._emit("warn", f"[KTX] {exc}")
                                break
                            if reserved:
                                seen.add(key)
                                self._reserved_count += 1
                        else:
                            seen.add(key)
                            msg = f"[KTX] 취소표 발견: {train.train_no}호"
                            self._emit("info", msg)
                            await self._run_call(
                                "텔레그램 취소표 알림",
                                lambda m=msg: self._send_telegram(config, f"🎉 <b>{m}</b>"),
                            )

                except KtxCallTimeout as exc:
                    session = None
                    error_streak += 1
                    self._emit("warn", f"[KTX] {exc} 세션 초기화 후 재시도합니다.")
                except NeedToLoginError as exc:
                    session = None
                    error_streak += 1
                    self._emit("warn", f"[KTX] 세션 만료: {exc}")
                except (SoldOutError, KorailError) as exc:
                    error_streak += 1
                    self._emit("warn", f"[KTX] Korail 오류: {exc}")
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                    error_streak += 1
                    self._emit("warn", f"[KTX] 네트워크 오류: {exc}")
                except asyncio.CancelledError:
                    self._emit("warn", "[KTX] 외부 취소 요청")
                    raise
                except Exception as exc:
                    error_streak += 1
                    self._emit("error", f"[KTX] 예상하지 못한 오류: {type(exc).__name__}: {exc}")

                wait = (
                    min(config.poll_interval * (2 ** error_streak), 300)
                    if error_streak
                    else config.poll_interval
                )
                if await self._sleep_or_stop(stop_event, wait):
                    break

        except asyncio.CancelledError:
            self._emit("warn", "[KTX] 외부 취소 요청으로 루프 종료")
        except Exception as exc:
            self._emit("error", f"[KTX] 루프 예외 누출: {type(exc).__name__}: {exc}")
        finally:
            self._emit("info", "[KTX] 모니터링 루프가 종료되었습니다.")
```

- [ ] **Step 2: import 오류 없는지 확인**

```
python -c "from backend.ktx_monitor_service import KtxMonitorService; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add backend/ktx_monitor_service.py
git commit -m "feat: KtxMonitorService 구현"
```

---

## Task 6: app.py 업데이트 (듀얼 서비스)

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: app.py 전체를 아래 내용으로 교체**

```python
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .config_store import (
    load_config,
    load_public_config,
    load_secrets_status,
    save_public_config,
    save_secrets,
)
from .ktx_monitor_service import KtxMonitorService
from .monitor_service import MonitorService
from .schemas import MonitorStatus, PublicConfig, SecretsPayload, SecretsStatus
from .stations import KTX_STATIONS, SRT_STATIONS


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.srt_service = MonitorService()
    app.state.ktx_service = KtxMonitorService()
    yield
    await asyncio.gather(
        app.state.srt_service.stop(),
        app.state.ktx_service.stop(),
        return_exceptions=True,
    )


app = FastAPI(title="SRT Monitor", lifespan=lifespan)

LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
STANDALONE_HTML = Path(__file__).resolve().parents[1] / "SRT Monitor _standalone_.html"


@app.middleware("http")
async def require_loopback_client(request: Request, call_next):
    client_host = request.client.host if request.client else ""
    if client_host not in LOOPBACK_CLIENTS:
        return JSONResponse(
            status_code=403,
            content={"detail": "SRT Monitor API는 이 PC의 로컬 브라우저에서만 사용할 수 있습니다."},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_srt(request: Request) -> MonitorService:
    return request.app.state.srt_service


def get_ktx(request: Request) -> KtxMonitorService:
    return request.app.state.ktx_service


SrtService = Annotated[MonitorService, Depends(get_srt)]
KtxService = Annotated[KtxMonitorService, Depends(get_ktx)]


def _merged_status(srt: MonitorService, ktx: KtxMonitorService) -> MonitorStatus:
    ss = srt.status()
    ks = ktx.status()
    return MonitorStatus(
        running=ss.running or ks.running,
        reserved_count=ss.reserved_count + ks.reserved_count,
        last_message=ks.last_message if ks.running else ss.last_message,
        started_at=ss.started_at or ks.started_at,
    )


def _merged_events(srt: MonitorService, ktx: KtxMonitorService):
    events = srt.recent_events() + ktx.recent_events()
    events.sort(key=lambda e: e.timestamp)
    return events[-300:]


@app.get("/", include_in_schema=False)
def get_standalone_app():
    return FileResponse(STANDALONE_HTML, media_type="text/html")


@app.get("/api/config/public")
def get_public_config() -> PublicConfig:
    return load_public_config()


@app.post("/api/config/public")
def update_public_config(public: PublicConfig) -> PublicConfig:
    return save_public_config(public)


@app.get("/api/config/secrets")
def get_secrets_status() -> SecretsStatus:
    return load_secrets_status()


@app.post("/api/config/secrets")
def update_secrets(secrets: SecretsPayload) -> SecretsStatus:
    return save_secrets(secrets)


@app.get("/api/status")
def get_status(srt: SrtService, ktx: KtxService) -> MonitorStatus:
    return _merged_status(srt, ktx)


@app.get("/api/stations")
def get_stations():
    return SRT_STATIONS


@app.get("/api/stations/ktx")
def get_ktx_stations():
    return KTX_STATIONS


@app.get("/api/events")
def get_events(srt: SrtService, ktx: KtxService):
    return _merged_events(srt, ktx)


@app.post("/api/start")
async def start_monitor(public: PublicConfig, srt: SrtService, ktx: KtxService):
    save_public_config(public)
    full_config = load_config()
    await srt.start(full_config)
    await ktx.start(full_config)
    return _merged_status(srt, ktx)


@app.post("/api/stop")
async def stop_monitor(srt: SrtService, ktx: KtxService):
    await asyncio.gather(srt.stop(), ktx.stop())
    return _merged_status(srt, ktx)


@app.get("/api/events/stream")
async def stream_events(request: Request, srt: SrtService, ktx: KtxService, skip: int = 0):
    async def event_generator():
        resume_id = request.headers.get("last-event-id")
        last_id: str | None = None

        events = _merged_events(srt, ktx)
        if resume_id:
            id_map = {e.id: i for i, e in enumerate(events)}
            pos = id_map.get(resume_id)
            initial = events[pos + 1:] if pos is not None else events
        else:
            initial = events[min(skip, len(events)):]

        for item in initial:
            yield f"id: {item.id}\ndata: {json.dumps(item.model_dump(), ensure_ascii=False)}\n\n"
            last_id = item.id

        while True:
            if await request.is_disconnected():
                break
            events = _merged_events(srt, ktx)
            if last_id is None:
                new_events = events
            else:
                index_map = {item.id: i for i, item in enumerate(events)}
                pos = index_map.get(last_id)
                new_events = events[pos + 1:] if pos is not None else []
            for item in new_events:
                yield f"id: {item.id}\ndata: {json.dumps(item.model_dump(), ensure_ascii=False)}\n\n"
                last_id = item.id
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
```

- [ ] **Step 2: 문법 오류 없는지 확인**

```
python -c "from backend.app import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add backend/app.py
git commit -m "feat: app.py에 KtxMonitorService 병렬 등록 및 듀얼 엔드포인트 추가"
```

---

## Task 7: 프론트엔드 JSX 수정 + HTML 재번들

번들 구조: HTML 내 `__bundler/manifest` JSON에서 `9823f60b` UUID가 메인 앱 JSX(gzip+base64)다.

- [ ] **Step 1: 앱 JS 추출**

프로젝트 루트에서 실행:

```python
# 파일명: extract_app.py
import json, re, gzip, base64, pathlib

html = pathlib.Path("SRT Monitor _standalone_.html").read_text(encoding="utf-8")
manifest = json.loads(re.search(r'<script type="__bundler/manifest">(.*?)</script>', html, re.DOTALL).group(1))
entry = manifest["9823f60b"]
data = gzip.decompress(base64.b64decode(entry["data"]))
pathlib.Path("extracted_app.js").write_bytes(data)
print(f"추출 완료: {len(data)} bytes")
```

```
python extract_app.py
```

Expected: `추출 완료: 38720 bytes`

- [ ] **Step 2: KTX 스테이션 배열 추가**

`extracted_app.js`에서 `const timeOptions = Array.from(` 줄 바로 앞에 삽입:

```js
const ktxStations = [
  { name: '서울', code: '0001', line: '공통' },
  { name: '용산', code: '0002', line: '공통' },
  { name: '광명', code: '0004', line: '공통' },
  { name: '천안아산', code: '0502', line: '공통' },
  { name: '오송', code: '0297', line: '공통' },
  { name: '대전', code: '0010', line: '경부·경전·동해' },
  { name: '김천(구미)', code: '0507', line: '경부·동해' },
  { name: '서대구', code: '0506', line: '경부' },
  { name: '동대구', code: '0015', line: '경부·경전·동해' },
  { name: '신경주', code: '0508', line: '경부' },
  { name: '울산(통도사)', code: '0509', line: '경부' },
  { name: '부산', code: '0020', line: '경부' },
  { name: '공주', code: '0514', line: '호남·전라' },
  { name: '익산', code: '0030', line: '호남·전라' },
  { name: '정읍', code: '0033', line: '호남' },
  { name: '광주송정', code: '0036', line: '호남' },
  { name: '나주', code: '0037', line: '호남' },
  { name: '목포', code: '0041', line: '호남' },
  { name: '전주', code: '0045', line: '전라' },
  { name: '순천', code: '0051', line: '전라' },
  { name: '여수EXPO', code: '0053', line: '전라' },
  { name: '창원중앙', code: '0512', line: '경전' },
  { name: '진주', code: '0063', line: '경전' },
  { name: '포항', code: '0515', line: '동해' },
];

```

- [ ] **Step 3: emptySecrets 상수 수정**

기존 코드 (찾아서 교체):
```js
const emptySecrets = {
  srt_id_set: false,
  srt_password_set: false,
  telegram_bot_token_set: false,
  telegram_chat_id_set: false,
};
```

교체:
```js
const emptySecrets = {
  srt_id_set: false,
  srt_password_set: false,
  korail_id_set: false,
  korail_password_set: false,
  telegram_bot_token_set: false,
  telegram_chat_id_set: false,
};
```

- [ ] **Step 4: KTX 상태 변수 추가**

`extracted_app.js`에서 `// 계정` 주석 바로 위에 삽입:

```js
  // KTX
  const [monitorKtx, setMonitorKtx] = useState(false);
  const [ktxFrom, setKtxFrom] = useState('서울');
  const [ktxTo, setKtxTo] = useState('부산');
  const [korailId, setKorailId] = useState('');
  const [korailPw, setKorailPw] = useState('');

```

- [ ] **Step 5: credentialsReady 수정**

기존:
```js
  const credentialsReady = secretsStatus.srt_id_set && secretsStatus.srt_password_set;
```

교체:
```js
  const credentialsReady = secretsStatus.srt_id_set && secretsStatus.srt_password_set &&
    (!monitorKtx || (secretsStatus.korail_id_set && secretsStatus.korail_password_set));
```

- [ ] **Step 6: applyConfig에 KTX 필드 추가**

`applyConfig` 함수 내부에서 `setFrom(config.departure_station || '오송');` 다음 줄에 삽입:

```js
    setMonitorKtx(!!config.monitor_ktx);
    setKtxFrom(config.ktx_departure_station || '서울');
    setKtxTo(config.ktx_arrival_station || '부산');
```

- [ ] **Step 7: buildPublicConfig에 KTX 필드 추가**

`buildPublicConfig()` 리턴 객체에서 `heartbeat_interval: 1800,` 앞에 삽입:

```js
      monitor_ktx: monitorKtx,
      ktx_departure_station: ktxFrom,
      ktx_arrival_station: ktxTo,
      ktx_check_special: opts.check_special,
      ktx_check_normal: opts.check_normal,
```

- [ ] **Step 8: saveSecretsIfNeeded에 Korail 계정 추가**

`saveSecretsIfNeeded` 내 `const payload = {` 블록을 아래로 교체:

```js
    const payload = {
      srt_id: srtId.trim(),
      srt_password: srtPw,
      korail_id: korailId.trim(),
      korail_password: korailPw,
      telegram_bot_token: telegramBotToken.trim(),
      telegram_chat_id: telegramChatId.trim(),
    };
```

`setSrtPw('');` 다음에 삽입:
```js
    setKorailId('');
    setKorailPw('');
```

- [ ] **Step 9: handleStart에 KTX 계정 검증 추가**

`handleStart` 내 SRT 계정 검증 블록(`if (!latestSecrets.srt_id_set ...`) 다음에 삽입:

```js
      if (monitorKtx && (!latestSecrets.korail_id_set || !latestSecrets.korail_password_set)) {
        setModal({ kind: 'warn', title: 'KTX 계정 정보가 필요합니다', body: 'KTX를 감시하려면 Korail ID와 비밀번호를 입력해주세요.' });
        return;
      }
```

- [ ] **Step 10: KTX 토글 UI 추가**

`동작 제어` 패널의 `<div className="toggle-grid single">` 내부 맨 마지막 `</Toggle>` 다음, `</div>` 전에 삽입:

```jsx
              <Toggle name="KTX도 함께 감시" desc="서울발 KTX 동시 조회·예약" checked={monitorKtx} onChange={setMonitorKtx} />
```

- [ ] **Step 11: KTX 구간 선택 UI 추가**

SEARCH STRIP 내 `{isRunning ? (` 바로 앞에 삽입:

```jsx
            {monitorKtx && (
              <>
                <div style={{ width: '100%', height: 1, background: 'var(--border)', margin: '4px 0' }} />
                <label className="search-cell">
                  <span className="label" style={{ fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-4)', fontWeight: 700 }}>KTX 출발지</span>
                  <select value={ktxFrom} onChange={(e) => setKtxFrom(e.target.value)}>
                    {ktxStations.map((s) => <option key={s.code} value={s.name}>{s.name}</option>)}
                  </select>
                  <span style={{ fontSize: 12, color: 'var(--ink-4)' }}>KTX · {ktxStations.find(s => s.name === ktxFrom)?.line || ''}</span>
                </label>
                <label className="search-cell">
                  <span className="label" style={{ fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-4)', fontWeight: 700 }}>KTX 도착지</span>
                  <select value={ktxTo} onChange={(e) => setKtxTo(e.target.value)}>
                    {ktxStations.map((s) => <option key={s.code} value={s.name}>{s.name}</option>)}
                  </select>
                  <span style={{ fontSize: 12, color: 'var(--ink-4)' }}>KTX · {ktxStations.find(s => s.name === ktxTo)?.line || ''}</span>
                </label>
              </>
            )}
```

- [ ] **Step 12: Korail 계정 입력 UI 추가**

계정 패널에서 SRT 비밀번호 input 닫는 태그(`/>`) 다음, 텔레그램 봇 토큰 label 앞에 삽입:

```jsx
                {monitorKtx && (
                  <>
                    <label className="field">
                      <span>Korail ID (KTX)</span>
                      <input
                        value={korailId}
                        onChange={(e) => setKorailId(e.target.value)}
                        placeholder={secretsStatus.korail_id_set ? '저장됨 · 변경할 때만 입력' : '아이디 / 이메일 / 전화번호'}
                      />
                    </label>
                    <label className="field">
                      <span>Korail 비밀번호 (KTX)</span>
                      <input
                        type="password"
                        value={korailPw}
                        onChange={(e) => setKorailPw(e.target.value)}
                        placeholder={secretsStatus.korail_password_set ? '저장됨 · 변경할 때만 입력' : '비밀번호'}
                      />
                    </label>
                  </>
                )}
```

- [ ] **Step 13: 재번들 스크립트 실행**

프로젝트 루트에 `rebundle_html.py` 저장 후 실행:

```python
import json, re, gzip, base64, pathlib, shutil

HTML_PATH = pathlib.Path("SRT Monitor _standalone_.html")
APP_UUID = "9823f60b"

html = HTML_PATH.read_text(encoding="utf-8")
manifest_match = re.search(r'(<script type="__bundler/manifest">)(.*?)(</script>)', html, re.DOTALL)
assert manifest_match, "manifest 스크립트 태그를 찾을 수 없습니다"

manifest = json.loads(manifest_match.group(2))
new_data = gzip.compress(pathlib.Path("extracted_app.js").read_bytes(), compresslevel=9)
manifest[APP_UUID]["data"] = base64.b64encode(new_data).decode("ascii")
manifest[APP_UUID]["compressed"] = True

new_manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
new_html = html[: manifest_match.start(2)] + new_manifest_json + html[manifest_match.end(2):]

shutil.copy(HTML_PATH, HTML_PATH.with_suffix(".html.bak"))
HTML_PATH.write_text(new_html, encoding="utf-8")
print("재번들 완료.")
```

```
python rebundle_html.py
```

Expected: `재번들 완료.`

- [ ] **Step 14: 임시 파일 정리**

```
del extract_app.py rebundle_html.py extracted_app.js
```

- [ ] **Step 15: 커밋**

```bash
git add "SRT Monitor _standalone_.html"
git commit -m "feat: 프론트엔드에 KTX 토글·구간 선택·Korail 계정 입력 추가"
```

---

## Task 8: 통합 동작 검증

- [ ] **Step 1: 전체 테스트 실행**

```
python -m pytest tests/ -v
```

Expected: 모든 테스트 PASSED

- [ ] **Step 2: 백엔드 실행**

```
py -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 3: API 확인**

```
curl http://127.0.0.1:8000/api/stations/ktx
curl http://127.0.0.1:8000/api/config/secrets
curl http://127.0.0.1:8000/api/status
```

Expected:
- `/api/stations/ktx`: KTX 역 목록 JSON 배열 (서울, 용산 등)
- `/api/config/secrets`: `korail_id_set: false, korail_password_set: false` 포함
- `/api/status`: `running: false, reserved_count: 0` 정상 응답

- [ ] **Step 4: 브라우저 UI 확인**

`http://127.0.0.1:8000` 접속 후:
1. "동작 제어" 패널 → "KTX도 함께 감시" 토글 보임
2. 토글 ON → 검색 패널에 KTX 출발/도착역 드롭다운 나타남
3. 계정 패널 → "Korail ID", "Korail 비밀번호" 필드 보임
4. 토글 OFF → KTX UI 숨겨짐
5. 기존 SRT 기능 정상 동작 확인
