# KTX 동시 모니터링 지원 설계

> 작성일: 2026-05-26  
> 범위: SRT Monitor에 KTX(Korail) 예약 기능을 병렬로 추가  
> 구현 방식: 방식 A — 병렬 독립 서비스

---

## 배경 및 목표

현재 SRT Monitor는 SRT(`SRTrain` 라이브러리)만 지원한다. 사용자는 SRT와 KTX를 동시에 감시해서 둘 중 먼저 빈 자리가 나오는 열차를 예약하고 싶다.

**성공 기준:**
- SRT와 KTX가 독립적으로 동시 실행된다
- 기존 SRT 기능이 변경 없이 유지된다
- UI에서 KTX 활성화, 구간, 계정을 설정할 수 있다

---

## 아키텍처

### 원칙: 병렬 독립 서비스

SRT와 KTX는 완전히 분리된 루프로 실행된다. 공유 상태가 없으므로 어느 한 쪽의 오류가 다른 쪽에 영향을 주지 않는다.

```
FastAPI app
  ├── MonitorService (SRT)      — SRTrain 라이브러리
  └── KtxMonitorService (KTX)  — korail2 라이브러리
```

두 서비스는 동일한 인터페이스(`start`, `stop`, `status`, `recent_events`)를 갖는다. `app.py`는 두 서비스를 모두 관리하고 이벤트 스트림을 합산해서 제공한다.

---

## 변경 파일 상세

### 1. `requirements-web.txt`
`korail2` 패키지 추가.

### 2. `backend/stations.py`
`KTX_STATIONS` 역 목록(서울, 용산, 광명, 천안아산, 오송, 대전, 김천구미, 서대구, 동대구, 경주, 울산, 부산, 공주, 익산, 정읍, 광주송정, 나주, 목포, 전주, 순천, 여수EXPO, 창원중앙, 진주, 포항 등)과 `KTX_STATION_NAMES` set 추가.

- SRT 역과 KTX 역은 이름이 다른 경우가 있다 (수서 vs 서울, 평택지제 vs 없음 등). 별도 목록으로 유지한다.
- 공통 역 이름도 있지만(대전, 부산 등) 코드가 달라 목록을 공유하지 않는다.

### 3. `backend/schemas.py`

#### `PublicConfig`에 추가
```python
monitor_ktx: bool = False
ktx_departure_station: str = "서울"    # KTX 출발역
ktx_arrival_station: str = "부산"      # KTX 도착역
ktx_check_special: bool = True
ktx_check_normal: bool = True
```
`ktx_departure_station`/`ktx_arrival_station`은 `KTX_STATION_NAMES`로 검증한다. `monitor_ktx = False`이면 KTX 역 검증은 건너뛴다.

#### `MonitorConfig`에 추가
```python
korail_id: str = ""
korail_password: str = ""
```

#### `SecretsPayload`에 추가
```python
korail_id: str = ""
korail_password: str = ""
```

#### `SecretsStatus`에 추가
```python
korail_id_set: bool
korail_password_set: bool
```

### 4. `backend/ktx_logic.py` (신규)

`srt_logic.py`와 대칭되는 KTX 전용 로직. `korail2` 라이브러리의 열차 객체를 받아 좌석 가용 여부, 예약 시도, 좌석 조건 검증을 수행한다.

주요 함수:
- `available_seat_type_ktx(cfg, train)` → 예약할 좌석 타입 또는 None
- `seat_status_str_ktx(cfg, train)` → 상태 문자열
- `try_reserve_train_ktx(cfg, korail, train, *, emit, send_telegram)` → bool

korail2 API 특이사항:
- 좌석 가용 확인: `train.has_seat()` (일반), `train.has_special_seat()` (특실)
- 예약: `korail.reserve(train, passengers, option)` — `ReserveOption` enum 사용
- 취소: `korail.cancel(reservation)`
- 오류: `KorailError`, `KorailLoginError`

### 5. `backend/ktx_monitor_service.py` (신규)

`MonitorService`와 동일한 인터페이스를 가진 KTX 전용 서비스. 내부 로직은 `korail2`를 사용하고, 이벤트 메시지에 `[KTX]` 접두사를 붙인다.

```python
class KtxMonitorService:
    def status(self) -> MonitorStatus: ...
    def recent_events(self) -> list[EventItem]: ...
    async def start(self, config: MonitorConfig) -> MonitorStatus: ...
    async def stop(self) -> MonitorStatus: ...
```

- `monitor_ktx = False`이거나 `korail_id`/`korail_password`가 비어 있으면 `start()` 호출 시 즉시 반환한다.
- 이벤트 메시지는 `[KTX] ...` 형식으로 접두사를 붙인다.
- `max_reservations`는 KTX 서비스만의 카운터로 독립 추적한다.

### 6. `backend/config_store.py`

`_SECRETS_FIELDS`에 `korail_id`, `korail_password` 추가. 나머지 로직은 동일하다.

### 7. `backend/app.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.srt_service = MonitorService()
    app.state.ktx_service = KtxMonitorService()
    yield
    await asyncio.gather(
        app.state.srt_service.stop(),
        app.state.ktx_service.stop(),
    )
```

새 엔드포인트:
- `GET /api/stations/ktx` → KTX 역 목록

변경 엔드포인트:
- `POST /api/start` — `monitor_ktx=True`이면 두 서비스 모두 시작
- `POST /api/stop` — 두 서비스 모두 중지
- `GET /api/status` — SRT + KTX 상태를 합산. 어느 쪽이든 running이면 running, reserved_count 합산
- `GET /api/events` / `GET /api/events/stream` — 두 서비스 이벤트를 타임스탬프 순으로 합산

### 8. 프론트엔드 (`SRT Monitor _standalone_.html` 내부 JSX 수정)

HTML 번들에서 메인 앱 JS(UUID `9823f60b`)를 추출·수정·재압축·재삽입한다.

**추가 UI 요소:**
1. **"KTX도 함께 감시" 토글** — 검색 패널 하단 또는 계정 패널 상단에 배치
2. **KTX 구간 선택** — `monitor_ktx = true`일 때 SRT 구간 아래 표시되는 출발/도착역 드롭다운 (KTX 역 목록 `/api/stations/ktx`에서 fetch)
3. **KTX 계정 입력** — 계정 패널에 Korail ID / 비밀번호 필드 추가
4. **계정 준비 상태** — `monitor_ktx=true`이면 `korail_id_set && korail_password_set`도 credentialsReady에 포함

**JS 상태 추가:**
```js
const [monitorKtx, setMonitorKtx] = useState(false);
const [ktxFrom, setKtxFrom] = useState('서울');
const [ktxTo, setKtxTo] = useState('부산');
const [ktxStations, setKtxStations] = useState([]);
const [korailId, setKorailId] = useState('');
const [korailPw, setKorailPw] = useState('');
```

**`buildPublicConfig()` 확장:**
```js
monitor_ktx: monitorKtx,
ktx_departure_station: ktxFrom,
ktx_arrival_station: ktxTo,
```

**`saveSecretsIfNeeded()` 확장:**
```js
korail_id: korailId.trim(),
korail_password: korailPw,
```

**재번들 방법:** Python 스크립트로 자동화 (gzip 압축 → base64 인코딩 → manifest UUID 교체).

---

## 데이터 흐름

```
사용자 → /api/start (PublicConfig with monitor_ktx=true)
  → MonitorService.start() (SRT 루프 시작)
  → KtxMonitorService.start() (KTX 루프 시작, korail_id/pw 필요)

SRT 루프: SRT 로그인 → search_train → reserve → emit("[SRT] ...")
KTX 루프: Korail 로그인 → search_train → reserve → emit("[KTX] ...")

/api/status → SRT + KTX 상태 합산
/api/events/stream → 두 서비스 이벤트 실시간 합산
```

---

## 에러 핸들링

- `KorailLoginError` → 세션 초기화 + 재로그인 시도 (SRT와 동일 패턴)
- `KorailError` → 이벤트 로그 기록, 다음 폴링 사이클에서 재시도
- `monitor_ktx=False` 또는 KTX 계정 미설정 → KTX 서비스 start() 시 즉시 종료, SRT만 실행

---

## 테스트 시나리오

1. **SRT 단독** — `monitor_ktx=false`로 시작 시 기존과 동일하게 동작
2. **KTX 단독** — `monitor_ktx=true`, SRT 계정 없음 → SRT 루프는 로그인 실패, KTX만 감시
3. **동시 감시** — 둘 다 설정 → 이벤트 로그에 `[SRT]` / `[KTX]` 구분 표시
4. **KTX 계정 누락** — `monitor_ktx=true`지만 korail_id 없음 → KtxMonitorService.start()가 즉시 종료, 경고 이벤트
5. **SRT 성공 후 KTX 계속** — SRT가 먼저 예약 완료해도 KTX 루프는 독립적으로 계속 실행 (각자의 max_reservations 기준)

---

## 미결 사항

- `korail2` 라이브러리의 실제 API를 구현 전 확인 필요 (seat availability 메서드 명, ReserveOption enum 등)
- KTX 역 코드 목록은 korail2 소스 또는 Korail 공식 데이터 기준으로 확정
