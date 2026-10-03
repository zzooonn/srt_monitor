# Unified Rail Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 코레일 통합 계정과 단일 여정을 사용하는 고속철도 취소표 감시 화면·서버를 구현한다.

**Architecture:** FastAPI가 읽을 수 있는 정적 프론트를 제공한다. 통합 서비스가 코레일 또는 명시적으로 선택한 구형 SRT 엔진을 제어하고 기존 예약 조정자를 공유한다. 버전이 있는 공개 설정을 변환하며 민감 정보는 기존 별도 경계를 유지한다.

**Tech Stack:** Python, FastAPI, Pydantic, 기존 SRTrain/korail2, HTML/CSS/JavaScript, pytest, 브라우저 검증.

작업 폴더에는 Git 저장소가 없으므로 현재 폴더에서 순차 작업한다. 사용자 설정은 직접 테스트에 사용하지 않고 임시 설정 파일로 대체한다. 각 구현 단계 뒤 설계 검토와 품질 검토를 순차 수행한다.

## Task 1: 통합 서버·설정 변환

**Files:** `backend/schemas.py`, `backend/config_store.py`, `backend/stations.py`, `backend/unified_monitor_service.py` (신규), `backend/app.py`, `backend/ktx_monitor_service.py`, `backend/monitor_service.py`, 관련 `tests/`.

- [x] 설정 변환, 날짜/시각/승객/좌석 검증, 코레일 단독 시작, 통합 상태 우선순위에 대한 실패 테스트를 먼저 작성한다.
- [x] 통합 공개 계약을 추가한다.

```python
schema_version: int = 2
connection_mode: Literal["korail", "legacy_srt"] = "korail"
```

기존 여정 필드를 정식 필드로 사용한다. 구형 KTX 구간은 버전 1 입력 변환 시에만 승계한다. 새 버전의 여정은 구형 중복 필드의 값 때문에 덮어쓰지 않는다. 코레일 계정이 없어도 공개 설정 저장은 가능하며 시작 시 분명한 오류를 반환한다.

- [x] `migrate_config_data(data)`를 구현한다. 기존 비밀 정보와 공개 옵션을 유지하고 변환 메시지를 공개 정보에 담는다. 기존 파일은 최초 쓰기 전 백업하고 임시 파일 교체로 저장한다.
- [x] 정규 역명과 별칭을 서버에 집중한다. 수서·동탄·평택지제 및 검증된 기존 고속철도 역을 코레일 경로에 포함한다. 기존 SRT 정차역의 ‘사천’ 표기는 ‘여천’으로 교정한다. 코드와 노선은 확인한 목록만 사용한다.
- [x] `UnifiedMonitorService`는 두 엔진과 조정자를 받아 `start(config)`, `stop()`, `status()`, `recent_events()`를 제공한다. 코레일 모드는 하나의 여정을 KTX 엔진 설정으로 매핑한다. 연결 모드를 바꾸면서 실행 중인 엔진을 하나 더 시작하지 않는다. 실행 중 설정 변경은 서버에서 거부하거나 현재 실행에 적용되지 않음을 명확하게 처리한다.
- [x] 전체 상태는 조정자와 활성 엔진에서 계산한다. `connection_mode`, `last_checked_at`, 실제 전체 상태를 노출하고 기존 `srt`, `ktx`, `reservation_lock`은 호환 유지한다. SSE는 같은 이벤트 목록을 사용한다.
- [x] `GET /`와 `/assets`는 Task 2의 `frontend/`를 제공한다. 기존 HTML은 원본 파일로 보존한다. API 이름과 메시지를 새 명칭에 맞춘다.
- [x] `pytest -q`를 실행한다. 의도적인 계약 변경은 기존 기대값을 새 동작에 맞추고 기존 예약 보호 테스트는 유지한다. 실제 철도 로그인·예약·텔레그램 전송 없이 검증한다.

## Task 2: 통합 프론트 화면

**Files:** `frontend/index.html`, `frontend/styles.css`, `frontend/api.js`, `frontend/app.js` (신규).

- [x] 서버 계약을 직접 확인하고 작은 API 모듈을 작성한다.

```javascript
export async function request(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '입력 내용을 확인해주세요.');
  return body;
}
```

- [x] 남색·청록·밝은 배경, 열차 노선에서 영감을 얻은 여정 입력과 실행 요약을 구현한다. 제품명은 Rail Monitor, 설명은 고속철도 통합 모니터. 공식 앱 명칭은 코레일+ 안내에 사용한다.
- [x] 단일 여정, 출발 시각 범위, 승객 유형별 인원, 좌석 종류, 연석/창가/호차, 알림·예약 정책, 조회·생존 알림 주기를 모든 공개 옵션과 연결한다. 서버에서 로드한 값을 기준으로 수정하여 숨겨진 설정을 지우지 않는다.
- [x] 코레일 통합 계정과 텔레그램 설정을 입력하고 비밀 값 저장 후 입력란을 비운다. 서버의 설정 여부만 보여준다. 구형 SRT 모드는 고급 설정에 명시적인 선택과 해당 계정을 둔다.
- [x] 저장·복원·감시 시작·중지·새로고침·예약 확인 동작을 연결한다. 초기 데이터 로드 실패 시 기본값을 실제 설정처럼 저장하지 않는다. 실행 중 여정 편집을 막고 서버의 저장/시작 응답에 맞게 표시한다.
- [x] SSE 연결 상태, 최근 조회, 실제 이벤트, 예약 수, 확인 필요 상태를 표시한다. SSE 초기 이벤트 중복을 ID로 제거한다. 연결 오류를 감시 성공 상태로 표현하지 않는다. 알림은 사용자 동작에서 권한을 얻은 경우에만 표시한다.
- [x] 입력 레이블, 키보드 포커스, 상태 안내, 좁은 화면 레이아웃을 점검한다. 링크와 모든 버튼은 실제 동작을 갖게 한다.
- [x] JavaScript 문법 점검과 임시 설정을 사용한 브라우저 저장·복원·오류·모바일 점검을 수행한다.

## Task 3: 실행 환경·안내 정리

**Files:** `README.md`, `WEB_README.md`, `run_web.bat`, `run_backend.bat`, `run_frontend.bat`, `install_web.bat`, `.gitignore`, `requirements-dev.txt`.

- [x] Python 선택 순서를 프로젝트 `.venv`, `py`, `python`으로 정리하고 실제 실행 가능한 Python인지 확인한다. 프론트 별도 설치는 제거한다.
- [x] 배치 실행은 사용자 요청 시에만 보이는 터미널로 실행하며 자동 검증의 백그라운드 서버는 숨겨서 실행한다.
- [x] 설명은 `http://127.0.0.1:8000/`, 통합 여정·계정, 구형 설정 이전, 조회와 예약의 차이, 비밀 정보 저장 위치, 프로세스 재시작 한계를 실제 구조에 맞춰 작성한다.
- [x] 개발 의존성에 API 테스트용 httpx를 포함한다. `.venv`와 `.runtime` 등 검증 산출물·설정 백업이 공유되지 않게 제외한다.

## Task 4: 최종 검증

- [x] 최종 서브에이전트가 설계 누락과 연결된 코드 경로를 읽고 지적한 오류를 수정한다.
- [x] 전체 pytest, JavaScript 문법 검사, 임시 서버의 API 호출·정적 파일 제공·SSE 수신을 검증한다.
- [x] 데스크톱·모바일 브라우저에서 여정 선택, 설정 저장·복원, 계정 미설정 오류, 구형 연결 선택, 조회 상태·불확실 예약 확인 UI를 확인한다.
- [x] 최종 결과에는 실행 주소와 검증 결과, 실계정 통합 API 로그인·예약의 미검증 여부를 정확하게 적는다.
