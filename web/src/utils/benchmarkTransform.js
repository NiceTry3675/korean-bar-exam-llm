/**
 * @file benchmarkTransform.js
 * @brief 단순 합산형 벤치마크 점수 및 비교 데이터 변환
 */

import { getBenchmarkSections } from './benchmarkRegistry.js'

/** @brief 섹션을 객체 키로 사용할 수 있는 안정적인 ID로 변환 */
export function getBenchmarkSectionKey(section) {
  return section.sheet || `${section.subject}:${section.section}`
}
/** @brief 결과 레코드가 레지스트리 섹션과 일치하는지 확인 */
function _matchesSection(record, section) {
  if (section.sheet && record.sheet_name === section.sheet) return true
  return record.subject === section.subject && record.section === section.section
}

/** @brief 결과 레코드에서 정답 수를 안전하게 계산 */
function _getCorrectCount(record) {
  if (Number.isFinite(Number(record?.correct_count))) {
    return Number(record.correct_count)
  }
  return (record?.results || []).filter(result => result.is_correct === true).length
}

/** @brief 결과 레코드에서 전체 문항 수를 안전하게 계산 */
function _getQuestionCount(record, section) {
  if (Number.isFinite(Number(record?.total_questions))) {
    return Number(record.total_questions)
  }
  if (record?.results?.length) return record.results.length
  return Number(section.questionCount) || 0
}

/**
 * @brief 단일 모델의 단순 합산형 점수 계산
 * @param {Array} data - 전체 결과 레코드
 * @param {string} modelName - 모델명
 * @param {Object} benchmark - 벤치마크 설정
 * @param {Array<string>} subjectFilter - 선택된 과목
 * @return {Object} 모델별 총점, 정답률, 섹션 점수
 */
export function calculateBenchmarkScore(data, modelName, benchmark, subjectFilter = []) {
  const modelData = data.filter(record => record.model_name === modelName)
  const filterSet = new Set(subjectFilter || [])
  const configuredSections = getBenchmarkSections(benchmark)
  const selectedSections = filterSet.size > 0
    ? configuredSections.filter(section => filterSet.has(section.subject))
    : configuredSections

  const sectionScores = selectedSections.map(section => {
    const record = modelData.find(item => _matchesSection(item, section))
    const score = Number(record?.score) || 0
    const maxScore = Number(section.maxScore ?? record?.total_points) || 0
    const correctCount = _getCorrectCount(record)
    const totalQuestions = _getQuestionCount(record, section)

    return {
      key: getBenchmarkSectionKey(section),
      sheet: section.sheet,
      subject: section.subject,
      section: section.section,
      score,
      maxScore,
      correctCount,
      totalQuestions,
      accuracy: totalQuestions > 0 ? (correctCount / totalQuestions) * 100 : 0
    }
  })

  const total = sectionScores.reduce((sum, section) => sum + section.score, 0)
  const maxScore = sectionScores.reduce((sum, section) => sum + section.maxScore, 0)
  const correctCount = sectionScores.reduce((sum, section) => sum + section.correctCount, 0)
  const totalQuestions = sectionScores.reduce((sum, section) => sum + section.totalQuestions, 0)
  const normalizedScores = Object.fromEntries(sectionScores.map(section => [
    section.key,
    section.maxScore > 0 ? (section.score / section.maxScore) * 100 : 0
  ]))

  return {
    model: modelName,
    total,
    maxScore,
    correctCount,
    totalQuestions,
    accuracy: totalQuestions > 0 ? (correctCount / totalQuestions) * 100 : 0,
    sectionScores,
    normalizedScores
  }
}

/**
 * @brief 모든 모델의 단순 합산형 점수 계산
 * @return {Array<Object>} 총점 내림차순 모델 점수
 */
export function calculateAllBenchmarkScores(data, models, benchmark, subjectFilter = []) {
  return models
    .map(model => calculateBenchmarkScore(data, model, benchmark, subjectFilter))
    .sort((a, b) => b.total - a.total)
}

/**
 * @brief 선택한 과목에 따른 단순 합산형 만점 계산
 * @return {number} 필터 적용 만점
 */
export function getBenchmarkMaxScore(benchmark, subjectFilter = []) {
  if (!subjectFilter?.length) return Number(benchmark?.scoring?.maxScore) || 0
  const selected = new Set(subjectFilter)
  return getBenchmarkSections(benchmark)
    .filter(section => selected.has(section.subject))
    .reduce((sum, section) => sum + (Number(section.maxScore) || 0), 0)
}

/**
 * @brief 레이더 차트용 섹션별 정규화 점수 생성
 * @return {Object} data와 dimensions
 */
export function transformBenchmarkRadarData(overallScores, selectedModels, benchmark) {
  const dimensions = getBenchmarkSections(benchmark).map(section => ({
    key: getBenchmarkSectionKey(section),
    label: section.subject,
    maxScore: Number(section.maxScore) || 0
  }))

  const data = dimensions.map(dimension => {
    const row = { subject: dimension.label }
    selectedModels.forEach(modelName => {
      const score = overallScores.find(item => item.model === modelName)
      row[modelName] = score?.normalizedScores?.[dimension.key] || 0
    })
    return row
  })

  return { data, dimensions }
}
