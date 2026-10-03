# 프로젝트 폴더 정리 계획 (2026-10-03)

## 목표

웹 통합(Rail Monitor) 이후 쓰이지 않는 파일을 지우고, 루트에는 "설치·실행·설정"에 필요한 것만 남긴다.
현재 실행 중인 서버(`127.0.0.1:8000`)와 웹 실행 경로(`backend/`, `frontend/`, `*.bat`)는 건드리지 않는다.

## 삭제 (휴지통으로 이동 — 복원 가능)

| 대상 | 이유 |
|---|---|
| `SRT Monitor _standalone_.html` (+ `.bak`, 각 24MB) | `frontend/index.html`이 없을 때만 쓰던 구형 단일 HTML. 현재 화면은 `frontend/` |
| `extracted_app.js`, `rebundle_html.py` | 위 단일 HTML 재번들 전용 도구 |
| `frontend/styles.css.bak-2026-10-03` | 리디자인 전 백업 |
| `have_to_fix.md` | 2026-05 백로그. React/Vite 구조 기준이라 현재 코드와 맞지 않고 항목 대부분 완료 |
| `.claude/docs/ai/srt-monitor/api-handoff.md` | 구형 `/api/config` 기준 문서. 현재 API는 README에 정리 |
| `.runtime/` (스크린샷·xlsx 제외) | 검증용 임시 서버·로그·pid·캡처 HTML·runner 점검 스크립트 |
| `.pytest_cache/`, `__pycache__/` | 자동 재생성 캐시 |

## 이동·이름 변경

```
srt_monitor/
├─ backend/            (변경 없음)
├─ frontend/           (백업 파일만 제거)
├─ tests/              (변경 없음)
├─ tools/
│  ├─ update_station_routes.py
│  └─ source/korail-2026-10-01.xlsx   ← .runtime/ 에서 이동 (역 구간 원본 시간표)
├─ legacy_cli/         ← 기존 SRT 전용 CLI 묶음
│  ├─ srt_monitor.py, config.py, config.ini
│  ├─ build.bat, srt_monitor.spec
│  └─ dist/srt_monitor.exe
├─ docs/
│  ├─ architecture/    ← korail-support-architecture.md, station-route-sources.md
│  ├─ specs/           ← docs/superpowers/specs
│  ├─ plans/           ← docs/superpowers/plans
│  └─ verification/    (+ images/ ← .runtime 스크린샷)
├─ install_web.bat, run_web.bat, run_backend.bat, run_frontend.bat
├─ README.md, WEB_README.md, CLAUDE.md
├─ requirements-web.txt, requirements-dev.txt
└─ web_config.json, srt_monitor.log, .env, .gitignore
```

`backend/`, `frontend/` 이름은 그대로 둔다. 이미 직관적이고, 바꾸면 `backend.app:app` 실행 경로·테스트 import·실행 중 서버가 모두 깨진다.

## 코드 수정

1. `backend/app.py`: `STANDALONE_HTML` 폴백 제거, `/`는 `frontend/index.html`만 제공.
2. `legacy_cli/srt_monitor.py`: 프로젝트 루트를 `sys.path`에 추가해 `backend.*` import 유지.
3. `legacy_cli/build.bat`: 자기 폴더로 이동 후 `--paths ..`로 PyInstaller가 `backend`를 찾게 함.
4. `tools/update_station_routes.py`: 기본 다운로드 위치를 `tools/source/`로.
5. 문서 경로 갱신: README, `docs/architecture/station-route-sources.md`, 검증 기록 스크린샷 경로, specs·plans 상호 링크.

## 유지 (사용 중이거나 비밀 정보)

- `web_config.json` (웹 설정·계정), `srt_monitor.log` (백엔드가 기록 중), `.venv/`
- `.env`: 코드에서 읽지 않지만 계정 정보가 들어 있어 자동 삭제하지 않음

## 검증

- `pytest` 273개 통과 유지
- `python legacy_cli/srt_monitor.py` import 단계 확인 (`config.ini` 로드까지)
- `GET /` 응답 확인
