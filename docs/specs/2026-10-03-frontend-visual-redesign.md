# 프론트엔드 비주얼 리디자인 — "역 시각표(Timetable)" 콘셉트

## 범위 (Scope)
- **수정 파일: `frontend/styles.css` 단 하나.**
- `index.html`, `app.js`, `station-picker.js`, `station-model.js`, `api.js`, 백엔드, 테스트는 **수정하지 않음**.
- 모든 id / class / ARIA / DOM 구조 유지 → JS가 토글하는 클래스(`connected`, `failed`, `active`, `warning`,
  `is-open`, `is-active`, `event-item.error|warning`, `field-error`, `station-*`)는 전부 새 스타일로 재정의.

## 왜 기존 디자인이 "AI스러운가"
| 증상 | 원인 |
|---|---|
| 둥근 카드 + 연한 그림자 + 민트/네이비 | SaaS 템플릿의 기본값 |
| "YOUR NEXT JOURNEY" 같은 영문 eyebrow + 그라데이션 없는 무난한 팔레트 | 출처 없는 무취향 장식 |
| 모든 요소가 같은 무게 | 위계(hierarchy)가 없음 |

## 레퍼런스 (Pinterest 조사)
1. **SBB/CFF 스위스 철도 사인 시스템**: 빨간 정사각 심볼, 흑백 대비, 굵은 그로테스크 서체
2. **Vignelli NYC Subway 매뉴얼**: 검정 띠 위 흰 글자, 굵은 수평 룰(rule)
3. **Split-flap 출발 안내판**: 검정 바탕 + 앰버 글자, 플랩 중앙 분할선
4. **일본 JR 時刻表 / 시각표**: 미색 종이, 괘선 그리드, 모노스페이스 숫자 열
5. **Swiss/Brutalist UI 키트**: 각진 모서리, 하드 섀도(offset 그림자), 반전(inverted) 선택 상태
6. **빈티지 에드먼슨 승차권**: 펀칭 구멍, 점선 절취선

## 디자인 시스템
- **팔레트**: 시각표 종이 `#ebe5d6` / 패널 `#f7f3ea` / 잉크 `#121212` / 신호 빨강 `#d52b1e` / 안내판 앰버 `#ffb81c`
- **서체**: Gothic A1(국문 헤드라인 900, 본문 400–600) + IBM Plex Mono(숫자·시간·라벨) + Archivo 콘덴스드(영문 브랜드)
  - Google Fonts `@import` — 오프라인이면 Malgun Gothic / Consolas로 자연스럽게 폴백
- **형태 규칙**: radius 0, 1px 잉크 테두리, 섹션 상단 4px 굵은 룰, 하드 섀도 `4px 4px 0 ink`
- **섹션 번호**: 승강장 번호판처럼 검정 정사각 타일 + 흰 모노 숫자
- **선택 상태**: 체크 시 검정 반전 (SBB 안내판 느낌)
- **모니터 카드**: split-flap 출발 안내판 (앰버 글자, 플랩 분할선, 승차권 펀칭)
- **이벤트 로그**: 시각표 행 — 모노 시간 열 + 각인(stamp)형 레벨 태그
- **배경**: 시각표 괘선 그리드 (아주 옅은 repeating-linear-gradient)

## 반응형 / 접근성
- 기존 브레이크포인트(1040 / 800 / 560px) 유지
- focus-visible: 3px 앰버 + 잉크 아웃라인 (대비 확보)
- `prefers-reduced-motion` 유지
