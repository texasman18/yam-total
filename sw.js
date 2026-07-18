const CACHE_NAME = 'yam-total-v3'; // KR/JP/TW 통합 앱 — 캐시 네임스페이스 분리(기존 yam-cache-v3와 충돌 없음)
const urlsToCache = [
  '/',
  '/index.html',
  '/manifest.json'
];

// 해외 번들 데이터는 절대 캐시하지 않는다 (lazy-load 취지 — INTEGRATION.md 요구사항)
const NEVER_CACHE = ['japan_toilets.json', 'taiwan_toilets.json'];

self.addEventListener('install', event => {
  self.skipWaiting(); // 새 SW를 기존 탭 종료 없이 즉시 활성화
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(cacheNames =>
        Promise.all(
          cacheNames
            .filter(name => name !== CACHE_NAME)
            .map(name => caches.delete(name)) // 이전 버전 캐시 자동 삭제
        )
      )
      .then(() => self.clients.claim()) // 이미 열려있는 화면(WebView 포함)에도 즉시 새 SW 적용
  );
});

self.addEventListener('fetch', event => {
  // GET 외 요청(POST 등)은 캐시 대상 아님 — cache.put(POST)는 예외 발생
  if (event.request.method !== 'GET') return;

  const url = event.request.url;

  // 카카오맵 API 관련 요청은 항상 네트워크 전용
  if (url.includes('dapi.kakao.com') || url.includes('t1.daumcdn.net')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 구글맵 API 관련 요청도 네트워크 전용 (동적 로더로 로드되는 스크립트·타일)
  if (url.includes('maps.googleapis.com') || url.includes('maps.gstatic.com')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 일본·대만 화장실 데이터는 어떤 캐시에도 넣지 않는다 (네트워크 전용 패스스루)
  if (NEVER_CACHE.some(name => url.includes(name))) {
    event.respondWith(fetch(event.request));
    return;
  }

  const isAppShell =
    event.request.mode === 'navigate' ||
    url.endsWith('/index.html') ||
    url.endsWith('/manifest.json') ||
    new URL(url).pathname === '/';

  if (isAppShell) {
    // 앱 셸(HTML/manifest)은 Network First — 웹에 새로 올리면 앱(TWA)도 바로 반영됨
    // 네트워크 요청 실패(오프라인) 시에만 캐시된 버전으로 대체
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response && response.ok) { // 404/500 응답을 앱 셸로 캐시하지 않도록 가드
            const cloned = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, cloned));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // 그 외 정적 리소스(toilets.json 등 용량 크고 자주 안 바뀌는 파일)는 Cache First
  // + 최초 네트워크 응답을 캐시에 저장해야 이후 오프라인 검색이 실제로 동작함
  event.respondWith(
    caches.match(event.request)
      .then(response => response || fetch(event.request).then(netRes => {
        if (netRes && netRes.ok && url.startsWith(self.location.origin)) {
          const cloned = netRes.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, cloned));
        }
        return netRes;
      }))
  );
});
