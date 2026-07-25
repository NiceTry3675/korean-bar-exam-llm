/**
 * @file dataLoader.js
 * @brief all_results.json 데이터 로드 및 파싱 유틸리티
 */

import {
  getBenchmarkMode,
  getBenchmarkSections
} from './benchmarkRegistry.js'

function _getBasePath() {
  return import.meta.env?.BASE_URL || '/'
}

export async function _loadJsonFile(
  fileName,
  { optional = false, fallback = null, fallbackOnError = false } = {}
) {
  if (!fileName) {
    if (optional) return fallback
    throw new Error('데이터 파일 경로가 설정되지 않았습니다')
  }

  try {
    const response = await fetch(`${_getBasePath()}${fileName}`)
    if (!response.ok) {
      if (optional && response.status === 404) return fallback
      throw new Error(`데이터 로드 실패: ${response.status} (${fileName})`)
    }
    return await response.json()
  } catch (error) {
    if (fallbackOnError) {
      console.warn(`선택 데이터 로드 실패: ${fileName}`)
      return fallback
    }
    throw error
  }
}

/**
 * @brief 공개 벤치마크 레지스트리 로드
 * @return {Promise<Object>} 벤치마크 레지스트리
 */
export async function loadBenchmarkRegistry() {
  const registry = await _loadJsonFile('benchmark_registry.json')
  if (!Array.isArray(registry?.benchmarks) || registry.benchmarks.length === 0) {
    throw new Error('유효한 벤치마크 설정이 없습니다')
  }
  return registry
}

/**
 * @brief 결과 JSON 데이터를 fetch로 로드
 * @param {Object} benchmark - 벤치마크 설정
 * @param {'default' | 'hard'} mode - 벤치마크 모드
 * @return {Promise<Array>} 파싱된 JSON 배열
 * @throws {Error} 데이터 로드 실패 시
 */
export async function loadAllResults(benchmark, mode = 'default') {
  const fileName = getBenchmarkMode(benchmark, mode)?.results
  // 기본 모드 결과는 대시보드의 필수 데이터이므로 없으면 오류로 드러낸다.
  const optional = mode !== 'default'
  const result = await _loadJsonFile(fileName, { optional, fallback: [] })

  if (!Array.isArray(result)) {
    throw new Error(`결과 데이터 형식이 배열이 아닙니다: ${fileName}`)
  }
  return result
}

/**
 * @brief 탐색 노출 정책을 위한 벤치마크별 결과 존재 여부 조회
 * @param {Object} registry - 벤치마크 레지스트리
 * @param {string} currentId - 이미 결과를 불러온 현재 벤치마크 ID
 * @return {Promise<Object>} { benchmarkId: boolean }
 */
export async function loadBenchmarkAvailability(registry) {
  const candidates = (registry?.benchmarks || []).filter(
    benchmark => benchmark.navigation?.visibleWhenResults === true
  )
  const entries = await Promise.all(candidates.map(async benchmark => {
    const fileNames = [...new Set(
      Object.values(benchmark.modes || {}).map(mode => mode?.results).filter(Boolean)
    )]
    const resultsByMode = await Promise.all(fileNames.map(async fileName => {
      try {
        return await _loadJsonFile(fileName, { optional: true, fallback: [] })
      } catch {
        console.warn(`벤치마크 메뉴용 결과 확인 실패: ${fileName}`)
        return []
      }
    }))
    return [
      benchmark.id,
      resultsByMode.some(result => Array.isArray(result) && result.length > 0)
    ]
  }))
  return Object.fromEntries(entries)
}

/**
 * @brief 데이터에서 고유 값들을 추출
 * @param {Array} data - all_results.json 데이터 배열
 * @return {Object} { subjects, sections, models }
 */
export function extractUniqueValues(data, benchmark = null) {
  const configuredSections = getBenchmarkSections(benchmark)
  const configuredSubjects = [...new Set(configuredSections.map(section => section.subject).filter(Boolean))]
  const dataSubjects = [...new Set(data.map(d => d.subject).filter(Boolean))]
  const subjects = [...configuredSubjects, ...dataSubjects.filter(subject => !configuredSubjects.includes(subject))]
  const models = [...new Set(data.map(d => d.model_name))]

  // 과목별 섹션 맵 생성
  const sections = {}
  subjects.forEach(subj => {
    const configured = configuredSections
      .filter(section => section.subject === subj)
      .map(section => section.section)
      .filter(Boolean)
    const fromData = data
      .filter(d => d.subject === subj)
      .map(d => d.section)
      .filter(Boolean)
    sections[subj] = [...new Set([...configured, ...fromData])]
  })

  return { subjects, sections, models }
}

/**
 * @brief 특정 모델의 특정 과목/섹션 데이터 조회
 * @param {Array} data - all_results.json 데이터 배열
 * @param {string} modelName - 모델명
 * @param {string} subject - 과목명
 * @param {string} section - 섹션명 (선택)
 * @return {Object|null} 해당 데이터 또는 null
 */
export function getModelSubjectData(data, modelName, subject, section = null) {
  return data.find(d =>
    d.model_name === modelName &&
    d.subject === subject &&
    (section === null || d.section === section)
  ) || null
}

/**
 * @brief 특정 모델의 모든 데이터 조회
 * @param {Array} data - all_results.json 데이터 배열
 * @param {string} modelName - 모델명
 * @return {Array} 해당 모델의 모든 데이터
 */
export function getModelData(data, modelName) {
  return data.filter(d => d.model_name === modelName)
}

/**
 * @brief 토큰 사용량 데이터를 fetch로 로드
 * @param {Object} benchmark - 벤치마크 설정
 * @param {'default' | 'hard'} mode - 벤치마크 모드
 * @return {Promise<Object>} 모델별 토큰 사용량 객체
 */
export async function loadTokenUsage(benchmark, mode = 'default') {
  const fileName = getBenchmarkMode(benchmark, mode)?.tokenUsage
  const data = await _loadJsonFile(fileName, {
    optional: true,
    fallback: {},
    fallbackOnError: true
  })
  return data?.models || data || {}
}

/**
 * @brief 안전한 모델 메타데이터 로드
 * @return {Promise<Object>} 모델별 메타데이터 ({ modelName: { supportsVision } })
 */
export async function loadModelMetadata() {
  try {
    const response = await fetch(`${_getBasePath()}model_metadata.json`)
    if (!response.ok) {
      console.warn('모델 메타데이터 없음')
      return {}
    }
    return response.json()
  } catch {
    console.warn('모델 메타데이터 로드 실패')
    return {}
  }
}

/**
 * @brief 레지스트리에 선언된 문항 메타데이터를 fetch로 로드
 *
 * 벤치마크가 questionsMetadata를 선언하지 않으면 빈 객체를 돌려준다.
 *
 * @param {Object} benchmark - 벤치마크 설정
 * @param {string} mode - 실행 모드
 * @return {Promise<Object>} 과목-섹션별 문항 메타데이터 (이미지 유무, 배점)
 */
export async function loadQuestionsMetadata(benchmark = null, mode = 'default') {
  const configuredFile = benchmark
    ? (getBenchmarkMode(benchmark, mode)?.questionsMetadata || benchmark.questionsMetadata)
    : null
  if (!configuredFile) return {}
  return _loadJsonFile(configuredFile, {
    optional: true,
    fallback: {},
    fallbackOnError: true
  })
}
