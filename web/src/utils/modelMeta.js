/**
 * @file modelMeta.js
 * @brief 모델 메타데이터 유틸리티 (부분 벤치마크, 이미지 미지원, 비표준 설정, 지식 컷오프, 웹 서비스 환경)
 */

// 틀린 문항만 재실행한 부분 벤치마크 모델. 이번 변호사시험 실행에는 없다.
const PARTIAL_BENCHMARK_MODELS = {}

// 표시용 모델명 치환. 이번 실행에는 치환이 필요한 모델이 없다.
const MODEL_DISPLAY_NAMES = {}

/**
 * @brief 지식 컷오프가 시험일(제15회 변호사시험, 2026-01) 이후인 모델 패턴
 *
 * 시험 문제와 정답이 공개된 뒤 학습 데이터에 포함됐을 가능성을 표시하기 위한
 * 목록이며, 모델 계열의 실제 지식 컷오프를 확인해 갱신해야 한다.
 */
const POST_EXAM_KNOWLEDGE_CUTOFF_PATTERNS = [
  /^Claude Opus 5\b/
]

/**
 * @brief 비표준 설정 모델 (부분 벤치마크 모델과 동일)
 */
const NON_STANDARD_MODELS = new Set(Object.keys(PARTIAL_BENCHMARK_MODELS))

export function getModelMeta(modelName) {
  return PARTIAL_BENCHMARK_MODELS[modelName] || null
}

export function isPartialBenchmarkModel(modelName) {
  return Boolean(getModelMeta(modelName)?.isPartialBenchmark)
}

export function formatModelDisplayName(modelName) {
  const meta = getModelMeta(modelName)
  const displayName = MODEL_DISPLAY_NAMES[modelName] || modelName
  if (!meta?.displaySuffix) return displayName
  if (displayName.includes(meta.displaySuffix)) return displayName
  return `${displayName}${meta.displaySuffix}`
}

export function hasPartialBenchmark(models = []) {
  return models.some(model => isPartialBenchmarkModel(model))
}

/**
 * @brief 이미지 미지원 모델 여부
 * @param {string} modelName - 모델명
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {boolean}
 */
export function hasNoVision(modelName, modelMetadata = {}) {
  return modelMetadata?.[modelName]?.supportsVision === false
}

/**
 * @brief 비표준 설정 모델 여부
 * @param {string} modelName - 모델명
 * @return {boolean}
 */
export function isNonStandard(modelName) {
  return NON_STANDARD_MODELS.has(modelName)
}

/**
 * @brief 지식 컷오프가 시험일 이후인 모델 여부
 * @param {string} modelName - 모델명
 * @return {boolean}
 */
export function hasPostExamKnowledgeCutoff(modelName) {
  return POST_EXAM_KNOWLEDGE_CUTOFF_PATTERNS.some(p => p.test(modelName))
}

/**
 * @brief 지식 컷오프가 시험일 이후인 모델의 계열명 목록
 *
 * 어떤 모델을 가리키는지 범례에 직접 적기 위한 목록이며, 추론 강도 접미사를
 * 제거하고 중복을 없앤 계열명을 등장 순서대로 반환한다.
 *
 * @param {string[]} models - 모델명 배열
 * @return {string[]} 계열명 배열 (예: ['Claude Opus 5'])
 */
export function getPostExamKnowledgeCutoffModels(models = []) {
  const names = []
  models.forEach(model => {
    if (!hasPostExamKnowledgeCutoff(model)) return
    const { base } = parseEffortSuffix(formatModelDisplayName(model))
    if (!names.includes(base)) names.push(base)
  })
  return names
}

/**
 * @brief 도구 차단 웹 서비스 환경에서 실행한 모델 여부
 * @param {string} modelName - 모델명
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {boolean}
 */
export function hasWebServiceNoTools(modelName, modelMetadata = {}) {
  return modelMetadata?.[modelName]?.webServiceNoTools === true
}

/** @brief 모델명 끝의 추론 강도 접미사 */
const EFFORT_SUFFIX_PATTERN = /\s*\((max|high|low|none)\)\s*$/i

/**
 * @brief 모델명에서 추론 강도 접미사를 분리한다.
 *
 * 공개 JSON에는 추론 설정이 별도 필드로 없고 모델명 접미사만 남아 있으므로
 * 이름에서 직접 추출한다.
 *
 * @param {string} modelName - 모델명
 * @return {{ base: string, effort: string|null }} 접미사를 제거한 이름과 추론 강도
 */
export function parseEffortSuffix(modelName) {
  const name = typeof modelName === 'string' ? modelName : ''
  const matched = name.match(EFFORT_SUFFIX_PATTERN)
  if (!matched) return { base: name.trim(), effort: null }
  return {
    base: name.slice(0, matched.index).trim(),
    effort: matched[1].toLowerCase()
  }
}

/**
 * @brief 추론 강도를 고추론/저추론 두 등급으로 묶는다.
 *
 * Gemini는 max·none 단계가 없어 high/low만 사용하므로 같은 규칙으로 처리된다.
 *
 * @param {string} modelName - 모델명
 * @return {string|null} 'high' | 'low' | 접미사가 없으면 null
 */
export function getEffortTier(modelName) {
  const { effort } = parseEffortSuffix(modelName)
  if (effort === 'max' || effort === 'high') return 'high'
  if (effort === 'low' || effort === 'none') return 'low'
  return null
}

/**
 * @brief 모델의 시각적 플래그 반환
 * @param {string} modelName - 모델명
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {{ noVision: boolean, nonStandard: boolean, webServiceNoTools: boolean }}
 */
export function getModelFlags(modelName, modelMetadata = {}) {
  return {
    noVision: hasNoVision(modelName, modelMetadata),
    nonStandard: isNonStandard(modelName),
    webServiceNoTools: hasWebServiceNoTools(modelName, modelMetadata)
  }
}

/**
 * @brief 모델 목록에 플래그가 있는 모델이 포함되어 있는지 확인
 * @param {string[]} models - 모델명 배열
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {{ hasNoVision: boolean, hasNonStandard: boolean, hasWebServiceNoTools: boolean }}
 */
export function getAnyModelFlags(models = [], modelMetadata = {}) {
  return {
    hasNoVision: models.some(model => hasNoVision(model, modelMetadata)),
    hasNonStandard: models.some(isNonStandard),
    hasWebServiceNoTools: models.some(model => hasWebServiceNoTools(model, modelMetadata))
  }
}
