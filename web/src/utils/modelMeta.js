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
 * @brief 도구 차단 웹 서비스 환경에서 실행한 모델 여부
 * @param {string} modelName - 모델명
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {boolean}
 */
export function hasWebServiceNoTools(modelName, modelMetadata = {}) {
  return modelMetadata?.[modelName]?.webServiceNoTools === true
}

/**
 * @brief 모델의 시각적 플래그 반환
 * @param {string} modelName - 모델명
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {{ noVision: boolean, nonStandard: boolean, postExamKnowledgeCutoff: boolean, webServiceNoTools: boolean }}
 */
export function getModelFlags(modelName, modelMetadata = {}) {
  return {
    noVision: hasNoVision(modelName, modelMetadata),
    nonStandard: isNonStandard(modelName),
    postExamKnowledgeCutoff: hasPostExamKnowledgeCutoff(modelName),
    webServiceNoTools: hasWebServiceNoTools(modelName, modelMetadata)
  }
}

/**
 * @brief 모델 목록에 플래그가 있는 모델이 포함되어 있는지 확인
 * @param {string[]} models - 모델명 배열
 * @param {Object} modelMetadata - 모델별 메타데이터
 * @return {{ hasNoVision: boolean, hasNonStandard: boolean, hasPostExamKnowledgeCutoff: boolean, hasWebServiceNoTools: boolean }}
 */
export function getAnyModelFlags(models = [], modelMetadata = {}) {
  return {
    hasNoVision: models.some(model => hasNoVision(model, modelMetadata)),
    hasNonStandard: models.some(isNonStandard),
    hasPostExamKnowledgeCutoff: models.some(hasPostExamKnowledgeCutoff),
    hasWebServiceNoTools: models.some(model => hasWebServiceNoTools(model, modelMetadata))
  }
}
