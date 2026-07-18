# 얌 YAM — 한국·일본·대만 통합 화장실 찾기 앱

급할 때 가장 먼저 찾는 화장실 앱. 한국(52,480건)·일본(8,835건)·대만(17,561건), 총 78,000여 곳의 화장실 데이터를 한국어로 검색·안내한다. 순수 HTML/CSS/JS 단일 파일(`index.html`) 앱이며 외부 프레임워크·빌드 과정이 없다.

## 실행법 (로컬 테스트)

지도 SDK(Kakao/Google)와 GPS는 `file://`로 직접 열면 정상 동작하지 않는다(HTTPS 또는 localhost 필요). 로컬 정적 서버로 열 것:

```bash
cd "통합 yam"
npx serve .          # 또는: python3 -m http.server 8000
```

브라우저에서 `http://localhost:<포트>` 접속. 국가 모드를 직접 확인하려면 쿼리스트링으로 딥링크한다:

- `?country=jp` — 일본 모드로 진입 (랜딩부터 일본 버전 문구)
- `?country=tw` — 대만 모드로 진입
- `?lang=en` — 한국 모드 + 영어 UI (해외 카드 자동 숨김)

## 구조

```
통합 yam/
├── index.html            앱 코드 전체 (HTML+CSS+JS, 유일한 앱 코드)
├── sw.js                 서비스워커 (앱 셸 캐시, 해외 JSON은 캐시 안 함)
├── vercel.json            배포 설정 (cleanUrls)
├── manifest.json          PWA 매니페스트
├── icon.png               앱 아이콘
├── toilets.json            한국 공중화장실 데이터 (행정안전부, 52,480건)
├── japan_toilets.json      일본 화장실 데이터 (OSM, 8,835건)
├── taiwan_toilets.json     대만 화장실 데이터 (환경부+OSM, 17,561건)
├── japan_sources.md / taiwan_sources.md   데이터 출처 상세 기록
├── pipeline/japan/, pipeline/taiwan/       각국 데이터 수집·정제 스크립트 (배포 대상 아님)
├── docs/, 마케팅/                          기획·마케팅 산출물 폴더 (현재 비어 있음)
```

## 아키텍처 요약

- 상태 축 2개: `curLang`('ko'|'en', KR 모드에서만 토글) × `curCountry`('kr'|'jp'|'tw')
- `COUNTRIES` 테이블이 국가별 데이터 파일·리전·Places 쿼리·bbox·기본 화면 좌표의 단일 진실 공급원
- 모든 국가 전환은 `setCountry(cc, {manual, silent})` 하나로 수렴 (`localStorage.yam_country`, `sessionStorage.yam_manual` 수동우선 매너 규칙, URL `?country=` 딥링크 최우선)
- GPS 자동 국가 전환: `countryOf(lat,lng)` → `maybeAutoSwitch()`가 `goMyLoc()` 성공 콜백에서 호출
- Google Maps SDK는 정적 태그가 아닌 동적 로더(`ensureGoogleLang`)로 로드 — language 파라미터는 로드 시점에 고정되므로, 이미 다른 language로 로드된 상태에서 전환이 필요하면 URL 파라미터를 유지한 채 1회 `location.replace`로 리로드한다
- Kakao SDK는 정적 태그 유지 (KR 모드 전용)
- KR 모드 로직은 무수정 원칙 — 신규 분기는 전부 `curCountry!=='kr'` 뒤에만 존재

자세한 설계 배경은 `YAM/기획/통합 기획서들/YAM_3개국통합_기술설계서_v1.md` (정본) 참조.

## 배포 시 주의

- Google Maps API 키의 HTTP 리퍼러 허용 목록에 실제 배포 도메인을 등록해야 지도가 뜬다 (현재 키는 다른 YAM 배포에서 쓰던 키를 재사용 — 아래 "남은 리스크" 참조).
- `sw.js`는 `/index.html`·`/manifest.json`을 루트 경로로 캐시하므로 서브패스 배포 시 경로를 맞춰야 한다.
