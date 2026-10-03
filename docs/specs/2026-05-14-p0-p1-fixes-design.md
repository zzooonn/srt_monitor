# SRT Monitor P0 + P1 수정 설계

> 작성일: 2026-05-14  
> 범위: have_to_fix.md P0-1, P0-2, P0-3, P1-2, P1-4, P1-5  
> 접근 방식: 방식 1 (설정 계층 먼저)

---

## 1. 배경 및 목표

### 해결 항목

| ID | 설명 | 우선순위 |
|----|------|---------|
| P0-1 | Web 저장 시 텔레그램 설정이 빈 문자열로 덮어써짐 | 즉시 |
| P0-2 | SRT 계정·텔레그램 토큰이 `web_config.json`에 평문 저장 | 즉시 |
| P0-3 | Python/pnpm 실행 환경 재현 불가 시 안내 부재 | 즉시 |
| P1-2 | `max_departure_time < departure_time` 조합을 막지 않음 | 중요 |
| P1-4 | SSE 재연결 시 `Last-Event-ID` 헤더 미처리 → 이벤트 중복 | 중요 |
| P1-5 | `_tomorrow()` 중복 등 설정 정규화 로직 분산 | 중요 |

### 목표

- 민감 필드(비밀번호, 토큰)가 저장 과정에서 소실되지 않도록 한다.
- 프론트엔드로 민감 값 원문을 내려보내지 않는다.
- 잘못된 시간 범위 설정 시 즉각 오류를 반환한다.
- SSE 재연결 시 중복 알림이 발생하지 않는다.
- 개발 환경 설정 오류 시 명확한 안내가 출력된다.

---

## 2. 작업 그룹

### 그룹 A — Config 레이어 (P0-1, P0-2, P1-5)

가장 먼저 처리. P0-1·P0-2·P1-5가 같은 코드 경로(`config_store.py` → `app.py` → 프론트)를 건드리므로 한 단위로 묶는다.

#### 2-A-1. `backend/schemas.py`

**추가 모델 3개:**

```python
class PublicConfig(BaseModel):
    """민감 필드를 제외한 설정. GET /api/config/public 응답에 사용."""
    # MonitorConfig에서 아래 4개 필드 제외한 전체
    # srt_id, srt_password, telegram_bot_token, telegram_chat_id 미포함

class SecretsPayload(BaseModel):
    """민감 필드 저장용. POST /api/config/secrets 요청에 사용."""
    srt_id: str = ""
    srt_password: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

class SecretsStatus(BaseModel):
    """민감 필드 설정 여부만 반환. 실제 값은 포함하지 않는다."""
    srt_id_set: bool
    srt_password_set: bool
    telegram_bot_token_set: bool
    telegram_chat_id_set: bool
```

`_tomorrow()` 함수는 `schemas.py`에만 정의하고 `config_store.py`에서는 제거한다.

#### 2-A-2. `backend/config_store.py`

기존 `load_config()` · `save_config()`는 내부용(`MonitorService`)으로 유지.  
API 경계에서 쓸 함수 4개 추가:

```python
def load_public_config() -> PublicConfig:
    """저장된 설정에서 민감 필드를 제외하고 반환."""

def load_secrets_status() -> SecretsStatus:
    """민감 필드 설정 여부만 반환. 값 자체는 반환하지 않는다."""

def save_public_config(public: PublicConfig) -> PublicConfig:
    """공개 필드만 덮어쓰고, 기존 민감 필드는 파일에서 읽어 병합 후 저장."""

def save_secrets(secrets: SecretsPayload) -> SecretsStatus:
    """빈 문자열 필드는 기존 값 유지. 값이 있는 필드만 덮어씀."""
```

`_tomorrow()` 중복 제거: `config_store.py`에서 `_tomorrow()` 정의를 삭제하고, 해당 파일 내 사용처(`data["departure_date"] = _tomorrow()`)를 `schemas.py`의 `_tomorrow`를 임포트해서 대체한다.

#### 2-A-3. `backend/app.py`

엔드포인트 추가:

```
GET  /api/config/public    → PublicConfig
POST /api/config/public    → PublicConfig (secrets 병합 유지)
GET  /api/config/secrets   → SecretsStatus (set 여부만)
POST /api/config/secrets   → SecretsStatus (빈 필드 무시)
```

기존 `GET /api/config`, `POST /api/config`는 프론트만 사용하던 경로이며, `MonitorService`는 `config_store.load_config()`를 직접 호출하므로 API 경로와 무관하다. 기존 두 엔드포인트는 이번 변경에서 **삭제**한다.

`POST /api/start` 엔드포인트도 수정 필요:

```python
# 변경 전: MonitorConfig 전체를 프론트에서 받음
async def start_monitor(config: MonitorConfig, service: Service):
    save_config(config)
    return await service.start(config)

# 변경 후: PublicConfig만 받고, secrets는 파일에서 병합
async def start_monitor(public: PublicConfig, service: Service):
    save_public_config(public)          # 공개 설정 저장 (secrets 유지)
    full_config = load_config()         # secrets 포함 전체 설정 로드
    return await service.start(full_config)
```

#### 2-A-4. 프론트엔드

**`frontend/src/api.ts`** — 함수 4개 추가:

```typescript
getPublicConfig()    // GET /api/config/public
savePublicConfig()   // POST /api/config/public
getSecretsStatus()   // GET /api/config/secrets
saveSecrets()        // POST /api/config/secrets
```

**`frontend/src/hooks/useMonitorController.ts`**

- 초기 로드: `Promise.all([getPublicConfig(), getSecretsStatus()])` 병렬 호출
- `telegram_bot_token`, `telegram_chat_id`를 빈 문자열로 덮어쓰는 코드 제거
- `handleSave()`: 공개 설정 → `savePublicConfig()`, 민감 필드에 입력값이 있으면 → `saveSecrets()` 추가 호출
- `handleStart()`: `startMonitor(config)` 호출 전 공개 설정만 전달 (서버가 파일의 secrets와 병합)

**`frontend/src/App.tsx` 계정 패널**

- 각 민감 필드 옆에 설정 여부 표시: `secretsStatus.srt_id_set ? "설정됨" : "미입력"`
- 필드를 비워두면 기존 값 유지(서버 병합)임을 UI 힌트로 안내

---

### 그룹 B — 시간 범위 검증 (P1-2)

**`backend/schemas.py`**

기존 `validate_total_passengers` validator 아래에 추가:

```python
@model_validator(mode="after")
def validate_time_range(self) -> "MonitorConfig":
    if self.max_departure_time < self.departure_time:
        raise ValueError(
            "마지막 출발 시간이 시작 출발 시간보다 빠릅니다. "
            f"(시작: {self.departure_time[:2]}:{self.departure_time[2:4]}, "
            f"마지막: {self.max_departure_time[:2]}:{self.max_departure_time[2:4]})"
        )
    return self
```

문자열 사전순 비교(`"200000" < "230000"`)가 HHMMSS 형식에서 올바르게 동작한다. 같은 값(`departure_time == max_departure_time`)은 허용한다.

---

### 그룹 C — SSE Last-Event-ID (P1-4)

**`backend/app.py`** — `stream_events` 함수 수정:

```python
async def stream_events(request: Request, service: Service, skip: int = 0):
    async def event_generator():
        # Last-Event-ID 헤더 우선, 없으면 skip 쿼리 파라미터 사용
        resume_id = request.headers.get("last-event-id")
        last_id: str | None = None

        events = service.recent_events()
        if resume_id:
            id_map = {e.id: i for i, e in enumerate(events)}
            pos = id_map.get(resume_id)
            # id를 못 찾으면 deque에서 밀려난 것 — 전체 재전송
            initial = events[pos + 1:] if pos is not None else events
        else:
            initial = events[min(skip, len(events)):]

        for item in initial:
            yield f"id: {item.id}\ndata: {json.dumps(item.model_dump(), ensure_ascii=False)}\n\n"
            last_id = item.id

        # 이후 폴링 루프는 기존 app.py:102-116과 동일 (last_id 추적 + asyncio.sleep(1))
```

프론트엔드 변경 없음. 브라우저 `EventSource`가 자동으로 `Last-Event-ID` 헤더를 재연결 요청에 포함한다.

---

### 그룹 D — 환경 문서 (P0-3)

**`WEB_README.md`**

- Python 3.11+ 필수, 공식 다운로드 URL 명시
- `py` launcher 없을 때 `python` 명령어 대체 안내
- `pnpm` 없을 때: `npm install -g corepack` → `corepack enable` → `pnpm install`
- 실행 실패 체크리스트: Python PATH 확인, pip 패키지 설치, 포트 8000/5173 충돌

**`run_web.bat` (또는 `run_backend.bat`)**

```bat
py --version >nul 2>&1 || (
    echo [오류] Python이 설치되지 않았거나 PATH에 없습니다.
    echo 설치 주소: https://www.python.org/downloads/
    pause & exit /b 1
)
```

백엔드·프론트 실행 실패 시 오류 메시지를 한국어로 명확히 출력.

---

## 3. 파일 변경 목록

| 파일 | 변경 유형 | 그룹 |
|------|----------|------|
| `backend/schemas.py` | 수정 | A, B |
| `backend/config_store.py` | 수정 | A |
| `backend/app.py` | 수정 | A, C |
| `frontend/src/api.ts` | 수정 | A |
| `frontend/src/hooks/useMonitorController.ts` | 수정 | A |
| `frontend/src/App.tsx` | 수정 (계정 패널) | A |
| `frontend/src/types.ts` | 수정 (타입 추가) | A |
| `WEB_README.md` | 수정 | D |
| `run_web.bat` | 수정 | D |

---

## 4. 데이터 흐름 (변경 후)

```
[프론트 초기 로드]
  getPublicConfig()  → GET /api/config/public  → PublicConfig
  getSecretsStatus() → GET /api/config/secrets → SecretsStatus (set 여부만)

[저장]
  savePublicConfig() → POST /api/config/public  (secrets는 파일에서 병합)
  saveSecrets()      → POST /api/config/secrets (빈 필드 무시)

[시작]
  startMonitor(publicConfig) → POST /api/start
    → 서버 내부에서 load_config() 호출 → secrets 포함 MonitorConfig 사용

[SSE 재연결]
  브라우저 → Last-Event-ID 헤더 자동 포함
  서버 → 해당 id 이후 이벤트만 전송
```

---

## 5. 경계 조건 및 오류 처리

- `save_secrets()`: 빈 문자열(`""`) 필드는 기존 파일 값 유지. `None`과 `""`를 동일하게 처리.
- `load_public_config()`: 파일 없으면 `PublicConfig` 기본값 반환.
- `validate_time_range`: 같은 값(`departure_time == max_departure_time`)은 유효. 역전만 차단.
- SSE `Last-Event-ID`: deque에서 밀려난 id는 전체 재전송(기존 `pos is None` 처리 유지).
- 프론트 민감 필드 입력: 사용자가 필드를 비운 채 저장하면 `saveSecrets()` 호출 생략 → 기존 서버 값 유지.

---

## 6. 구현 순서

1. **그룹 A** — `schemas.py` 모델 추가 → `config_store.py` 함수 추가 → `app.py` 엔드포인트 추가 → 프론트엔드 3파일 수정
2. **그룹 B** — `schemas.py` validator 추가 (그룹 A 완료 후)
3. **그룹 C** — `app.py` SSE 함수 수정
4. **그룹 D** — 문서·배치 파일 수정
