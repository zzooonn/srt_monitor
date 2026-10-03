# KTX/ITX/무궁화호 지원 아키텍처 설계

> 작성일: 2026-05-07
> 목적: 기존 SRT 모니터링 동작 방식을 그대로 유지하면서 Korail(KTX/ITX-새마을/ITX-청춘/무궁화호)을 추가한다.

---

## 1. 현재 구조 요약

```
MonitorConfig (schemas.py)
   └─ srt_id / srt_password  ← SRT 전용 자격증명
   └─ departure_station / arrival_station  ← SRT 역명만 허용

monitor_service.py
   └─ SRT 로그인 → search_train() → reserve() (SRT 라이브러리 직접 사용)

srt_logic.py
   └─ available_seat_type / make_passengers / try_reserve_train (SRT 타입 사용)

stations.py
   └─ SRT_STATIONS + SRT_STATION_NAMES (SRT 전용 역 목록)
```

---

## 2. 핵심 설계 결정

### 전략: 최소 분기 방식 (Minimal Branching)

어댑터 추상화([P3-3] 권장 방향)는 별도 리팩터링으로 남기고,
**이번 작업은 기존 동작을 유지하며 최소한의 변경으로 Korail을 추가**한다.

- `rail_type: "srt" | "korail"` 필드 하나로 분기
- SRT 로직은 완전히 보존
- Korail 전용 함수를 별도 파일(`korail_logic.py`)에 격리
- `monitor_service.py`는 `rail_type`을 보고 SRT/Korail 중 하나를 실행

### 사용 라이브러리
- SRT: 기존 `SRT` 패키지 유지
- Korail: `korail2` 패키지 추가 (`pip install korail2`)

---

## 3. 변경 파일 목록

### 백엔드 (5개 파일)

| 파일 | 변경 종류 | 내용 |
|------|-----------|------|
| `backend/schemas.py` | 수정 | `rail_type`, `korail_id`, `korail_password` 필드 추가; 역 검증을 rail_type에 따라 분기 |
| `backend/stations.py` | 수정 | `KORAIL_STATIONS`, `KORAIL_STATION_NAMES` 추가 |
| `backend/korail_logic.py` | 신규 | Korail 전용 좌석 조회, 예약, 검증 함수 |
| `backend/monitor_service.py` | 수정 | `rail_type`에 따라 SRT/Korail 분기 처리 |
| `requirements-web.txt` | 수정 | `korail2` 의존성 추가 |

### 프론트엔드 (4개 파일)

| 파일 | 변경 종류 | 내용 |
|------|-----------|------|
| `frontend/src/types.ts` | 수정 | `rail_type`, `korail_id`, `korail_password` 필드 추가 |
| `frontend/src/configDefaults.ts` | 수정 | 기본값 추가, `missingAccountFields` 분기 |
| `frontend/src/stations.ts` | 수정 | Korail 역 목록 추가 |
| `frontend/src/App.tsx` | 수정 | 철도 종류 선택 UI, 자격증명/역 선택 분기 |

---

## 4. 상세 설계

### 4.1 `MonitorConfig` 필드 추가 (schemas.py)

```python
rail_type: Literal["srt", "korail"] = "srt"
korail_id: str = ""       # 코레일 회원번호
korail_password: str = "" # 코레일 비밀번호
```

역 검증:
```python
@field_validator("departure_station", "arrival_station")
def validate_station(cls, value, info):
    rail_type = info.data.get("rail_type", "srt")
    valid_names = SRT_STATION_NAMES if rail_type == "srt" else KORAIL_STATION_NAMES
    if value not in valid_names:
        raise ValueError(f"지원하지 않는 역입니다: {value}")
    return value
```

### 4.2 korail_logic.py (신규)

`srt_logic.py`와 동일한 함수 시그니처, korail2 타입 사용:

```python
def available_seat_type_korail(cfg, train) -> str | None:
    # korail2의 SeatType 반환

def try_reserve_train_korail(cfg, korail, train, *, emit, send_telegram) -> bool:
    # korail2의 reserve() 호출
```

### 4.3 monitor_service.py 분기

```python
if config.rail_type == "srt":
    session = SRT(config.srt_id, config.srt_password, verbose=False)
    trains = session.search_train(...)
    try_reserve_train(config, session, train, ...)
else:
    session = Korail(config.korail_id, config.korail_password, auto_login=False)
    session.login()
    trains = session.search_train(...)
    try_reserve_train_korail(config, session, train, ...)
```

오류 처리도 분기:
- SRT: `SRTLoginError`, `SRTNetFunnelError`, `SRTError`
- Korail: `KorailError` (korail2 예외 클래스)

### 4.4 프론트엔드 UX

```
[ SRT ] [ Korail ]  ← 철도 종류 탭/토글

SRT 선택 시:     Korail 선택 시:
- SRT ID         - 코레일 회원번호
- SRT 비밀번호   - 코레일 비밀번호
- SRT 역 목록    - Korail 역 목록
```

역 목록은 `rail_type`에 따라 `StationSelect`에 다른 배열 전달.

---

## 5. Korail 역 목록 (KTX 정차역 기준)

경부선: 서울, 용산, 광명, 수원, 천안아산, 오송, 대전, 김천구미, 동대구, 경주, 울산, 부산
호남선: 광주송정, 나주, 목포 + 오송, 익산 (경부 분기)
전라선: 전주, 남원, 순천, 여수EXPO
경전선: 창원중앙, 창원, 마산, 진주
동해선: 포항
강릉선: 만종, 횡성, 둔내, 평창, 진부, 강릉
중앙선: 청량리, 양평, 서원주, 원주, 제천, 단양, 풍기, 영주, 안동 등
경강선: 판교, 이매, 여주 등

---

## 6. 구현 순서

1. `requirements-web.txt` — `korail2` 추가
2. `backend/stations.py` — Korail 역 추가
3. `backend/schemas.py` — 필드 추가, 역 검증 분기
4. `backend/korail_logic.py` — 신규 작성
5. `backend/monitor_service.py` — rail_type 분기
6. `frontend/src/stations.ts` — Korail 역 추가
7. `frontend/src/types.ts` — 필드 추가
8. `frontend/src/configDefaults.ts` — 기본값, 검증 분기
9. `frontend/src/App.tsx` — 철도 선택 UI 추가

---

## 7. 리스크 및 주의사항

| 리스크 | 대응 |
|--------|------|
| `korail2` API 변동 | 예외 처리를 `korail_logic.py`에 집중시켜 영향 범위 최소화 |
| Korail 좌석 타입이 SRT와 다름 | `check_special` → korail2의 프리미엄실 여부로 매핑 |
| 역 이름 중복 (예: 대전이 SRT/Korail 모두 있음) | rail_type으로 분리된 목록을 사용하므로 충돌 없음 |
| 기존 `web_config.json`이 새 필드 없이 저장된 상태 | Pydantic 기본값이 적용되어 하위 호환 유지 |
