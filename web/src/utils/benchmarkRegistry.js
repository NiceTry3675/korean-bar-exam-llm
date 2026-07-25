/**
 * @file benchmarkRegistry.js
 * @brief 벤치마크 레지스트리 조회 및 표시 정책 유틸리티
 */

export const DEFAULT_BENCHMARK_ID = 'bar-exam-15'

/**
 * @brief 레지스트리에서 기본 벤치마크 ID를 결정
 * @param {Object} registry - 벤치마크 레지스트리
 * @return {string} 기본 벤치마크 ID
 */
export function getDefaultBenchmarkId(registry) {
  const benchmarks = registry?.benchmarks || []
  const configured = registry?.defaultBenchmark

  if (configured && benchmarks.some(benchmark => benchmark.id === configured)) {
    return configured
  }
  if (benchmarks.some(benchmark => benchmark.id === DEFAULT_BENCHMARK_ID)) {
    return DEFAULT_BENCHMARK_ID
  }
  return benchmarks[0]?.id || DEFAULT_BENCHMARK_ID
}

/**
 * @brief 요청된 ID를 실제 벤치마크 설정으로 해석
 * @param {Object} registry - 벤치마크 레지스트리
 * @param {string} requestedId - URL 등에서 요청된 벤치마크 ID
 * @return {Object|null} 벤치마크 설정
 */
export function resolveBenchmark(registry, requestedId) {
  const benchmarks = registry?.benchmarks || []
  const requested = benchmarks.find(benchmark => benchmark.id === requestedId)
  if (requested) return requested

  const defaultId = getDefaultBenchmarkId(registry)
  return benchmarks.find(benchmark => benchmark.id === defaultId) || benchmarks[0] || null
}

/**
 * @brief 현재 언어에 맞는 레지스트리 문구 반환
 * @param {Object|string} value - { ko, en } 또는 문자열
 * @param {string} language - 현재 언어
 * @return {string} 표시 문구
 */
export function getLocalizedRegistryText(value, language = 'ko') {
  if (typeof value === 'string') return value
  if (!value) return ''
  const normalizedLanguage = language?.startsWith('en') ? 'en' : 'ko'
  return value[normalizedLanguage] || value.ko || value.en || ''
}

/**
 * @brief 벤치마크에서 실행 모드 설정 조회
 * @param {Object} benchmark - 벤치마크 설정
 * @param {'default'|'hard'} mode - 실행 모드
 * @return {Object|null} 모드 설정
 */
export function getBenchmarkMode(benchmark, mode = 'default') {
  return benchmark?.modes?.[mode]
    || benchmark?.modes?.default
    || Object.values(benchmark?.modes || {})[0]
    || null
}

/**
 * @brief 탐색 메뉴에서 표시할 벤치마크 계산
 * @param {Object} registry - 벤치마크 레지스트리
 * @param {Object} availability - 벤치마크별 결과 존재 여부
 * @return {Array<Object>} 표시 가능한 벤치마크 목록
 */
export function getNavigableBenchmarks(registry, availability = {}) {
  const defaultId = getDefaultBenchmarkId(registry)
  return (registry?.benchmarks || []).filter(benchmark => {
    const navigation = benchmark.navigation || {}
    if (navigation.visible === true) return true
    if (benchmark.id === defaultId && navigation.visible !== false) return true
    return navigation.visibleWhenResults === true && availability[benchmark.id] === true
  })
}

/**
 * @brief 차트에 표시할 기준선 목록 반환
 *
 * 기준선 점수는 전체 문항을 기준으로 정의되므로, 과목 필터로 만점이 달라지면
 * 비교 대상이 성립하지 않는다. 이때는 빈 배열을 반환해 선을 숨긴다.
 *
 * @param {Object} benchmark - 벤치마크 설정
 * @param {Array<string>} subjectFilter - 선택된 과목
 * @return {Array<Object>} 기준선 목록
 */
export function getBenchmarkReferenceLines(benchmark, subjectFilter = []) {
  if (subjectFilter?.length) return []
  const maxScore = Number(benchmark?.scoring?.maxScore) || 0
  return (benchmark?.referenceLines || []).filter(line => {
    const score = Number(line?.score)
    return Number.isFinite(score) && score > 0 && (maxScore === 0 || score <= maxScore)
  })
}

/**
 * @brief 벤치마크의 섹션 목록을 중복 없이 정규화
 * @param {Object} benchmark - 벤치마크 설정
 * @return {Array<Object>} 정규화된 섹션 목록
 */
export function getBenchmarkSections(benchmark) {
  const seen = new Set()
  return (benchmark?.sections || []).filter(section => {
    const key = section.sheet || `${section.subject}:${section.section}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
