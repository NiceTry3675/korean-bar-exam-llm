/**
 * @file sw.js
 * @brief 서비스 워커 - 기본 캐싱 전략
 */

// 캐시 이름을 바꾸면 activate 단계에서 이전 캐시가 모두 삭제된다.
// 배포 내용이 크게 바뀌면 재방문자에게 낡은 셸이 남지 않도록 버전을 올린다.
const CACHE_NAME = 'bar-exam-llm-v1'
// 워커 자신의 위치에서 base를 유도해 저장소 이름 변경에 영향받지 않는다
const BASE_PATH = self.location.pathname.replace(/\/sw\.js$/, '')

// 캐시할 정적 에셋
const STATIC_ASSETS = [
  `${BASE_PATH}/`,
  `${BASE_PATH}/index.html`,
  `${BASE_PATH}/benchmark_registry.json`,
  `${BASE_PATH}/bar_exam_all_results.json`,
  `${BASE_PATH}/bar_exam_token_usage.json`
]

/**
 * @brief 설치 이벤트 - 정적 에셋 캐시
 */
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        // addAll은 원자적이라 파일 하나가 404면 설치 전체가 실패한다.
        // 선택적 데이터 파일이 없어도 워커는 활성화되어야 한다.
        return Promise.all(
          STATIC_ASSETS.map((asset) => cache.add(asset).catch(() => null))
        )
      })
      .then(() => {
        // 대기 중인 서비스 워커 즉시 활성화
        return self.skipWaiting()
      })
  )
})

/**
 * @brief 활성화 이벤트 - 오래된 캐시 정리
 */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== CACHE_NAME)
            .map((name) => caches.delete(name))
        )
      })
      .then(() => {
        // 모든 클라이언트 제어
        return self.clients.claim()
      })
  )
})

/**
 * @brief 페치 이벤트 - 네트워크 우선, 캐시 폴백
 */
self.addEventListener('fetch', (event) => {
  // 같은 오리진 요청만 처리
  if (!event.request.url.startsWith(self.location.origin)) {
    return
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // 성공적인 응답은 캐시에 저장
        if (response.status === 200) {
          const responseClone = response.clone()
          caches.open(CACHE_NAME)
            .then((cache) => {
              cache.put(event.request, responseClone)
            })
        }
        return response
      })
      .catch(() => {
        // 네트워크 실패 시 캐시에서 반환
        return caches.match(event.request)
      })
  )
})
