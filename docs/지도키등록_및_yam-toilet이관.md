# 지도 키 등록 + yam-toilet 이관 가이드 (요청 #4·#7)

작성: 2026-07-18 · 대상 URL: https://yam-total.vercel.app (테스트) → https://yam-toilet.vercel.app (실서비스)

---

## A. 지도가 안 나오는 원인 (요청 #4) — **도메인 미등록**

`yam-total.vercel.app`에서 지도가 안 뜨는 이유는 코드 문제가 아니라 **API 키에 이 도메인이 등록되지 않아서**다. 콘솔에서 실측 확인했다:
- 카카오: `401 domain mismatched! caller=https://yam-total.vercel.app`
- 구글: `RefererNotAllowedMapError — Your site URL to be authorized: https://yam-total.vercel.app/`

로컬 테스트(localhost:3000)에서는 두 지도 모두 정상 작동한다(이미 등록됨). **즉 코드는 정상이고, 배포 도메인만 두 콘솔에 추가하면 된다.**

### A-1. 카카오 도메인 등록 (내가 못 함 — 직접 하셔야 합니다)
1. https://developers.kakao.com → 로그인 → **내 애플리케이션** → 얌(YAM) 앱 선택
2. 좌측 **앱 설정 → 플랫폼** → **Web** 섹션
3. **사이트 도메인**에 아래 두 줄 추가 → 저장
   ```
   https://yam-total.vercel.app
   https://yam-toilet.vercel.app
   ```
   (yam-toilet은 이미 있을 것 — 없으면 함께 추가)

### A-2. 구글 지도 키 등록 (직접 하셔야 합니다)
1. https://console.cloud.google.com → 해당 프로젝트 → **API 및 서비스 → 사용자 인증 정보**
2. Maps용 API 키(`AIzaSyAplYXuzjhjgi...`) 클릭
3. **애플리케이션 제한사항 → HTTP 리퍼러(웹사이트)** → **항목 추가**로 아래 등록 → 저장
   ```
   https://yam-total.vercel.app/*
   https://yam-toilet.vercel.app/*
   ```
4. 반영에 최대 5분 소요. 이후 새로고침하면 지도가 뜬다.

> 등록 후 확인: `yam-total.vercel.app` 접속 → 내 주변 화장실 찾기 → 카카오맵, 일본 카드 → 구글맵(신주쿠)이 나오면 완료.

---

## B. yam-toilet.vercel.app로 이관 (요청 #7 — 1개월 후 실행)

목표: `yam-toilet.vercel.app`(웹+안드로이드 TWA+iOS)의 내용을 통합 앱으로 교체해, 세 플랫폼 모두 3개국 통합 앱이 되게 한다. yam-toilet은 `server.url` 방식이라 **웹만 교체하면 앱도 자동 반영**된다.

### ⚠️ "파일을 전부 지우고"는 위험 — 앱 필수 파일은 반드시 보존
yam-toilet 루트에는 **안드로이드·iOS 앱이 의존하는 파일**이 있다. 이걸 지우면 앱이 깨진다:
- `assetlinks.json`, `.well-known/` — 안드로이드 TWA 앱 링크(지우면 주소창 뜨거나 설치 검증 실패)
- `manifest.json`, `icon-*.png` (72~512) — PWA/홈화면 아이콘
- `privacy.html` — 스토어 심사용 개인정보 URL
- `netlify.toml` / `vercel.json` — 배포 설정

### B-1. 안전한 이관 절차 (교체할 것만 교체)
yam-toilet 저장소(iCloud `Documents/Claude/YAM/` = 이 배포의 소스)에서:

**① 교체 (통합 앱 것으로 덮어쓰기)**
```
통합 yam/index.html   → YAM/index.html
통합 yam/sw.js        → YAM/sw.js   (CACHE_NAME 'yam-total-v1' 확인)
```
**② 추가 (통합 앱에만 있는 데이터·파이프라인·문서)**
```
통합 yam/japan_toilets.json    → YAM/japan_toilets.json
통합 yam/taiwan_toilets.json   → YAM/taiwan_toilets.json
통합 yam/japan_sources.md, taiwan_sources.md → YAM/
통합 yam/pipeline/             → YAM/pipeline/
```
`toilets.json`(한국)은 이미 있으므로 그대로 둠.

**③ 보존 (건드리지 말 것)**
`assetlinks.json`, `.well-known/`, `manifest.json`, `icon*.png`, `privacy.html`, `netlify.toml`, `vercel.json`

**④ 확인**
- `YAM/index.html` head가 `icon.png`·`manifest.json`(상대경로)을 참조 → yam-toilet 루트에 존재하므로 OK
- `vercel.json`에 통합 앱용 rewrite 불필요(단일 index) — 기존 것 유지
- push 후 `yam-toilet.vercel.app`에서 3모드 검증 + 콘솔 에러 0

### B-2. 이관 후 키
`yam-toilet.vercel.app`은 원래 카카오·구글 키에 등록돼 있어 지도가 바로 나온다(A에서 함께 등록하면 완벽). 새 도메인을 안 쓰므로 추가 등록 불필요.

### B-3. 롤백
문제 시 `git revert` 또는 이전 index.html로 복구. 단일 파일이라 즉시 복구 가능.

> **실행 시점**: 사용자가 "이관해줘"라고 하면 위 ①~④를 대신 수행하고 push 명령을 안내한다. 그 전까지 yam-toilet은 현행 한국 전용 앱 유지(실서비스 보호).
