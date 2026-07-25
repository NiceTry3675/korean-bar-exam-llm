/**
 * @file costTransform.js
 * @brief 벤치마크 무관 비용/토큰 사용량 변환 유틸리티
 */

/**
 * @brief 비용 데이터 추출 (실제 토큰 사용량 기반)
 * @param {Array} data - 결과 레코드 전체 배열
 * @param {Array} overallScores - calculateAllBenchmarkScores 결과 또는 filteredScores
 * @param {Object} tokenUsage - token_usage 파일의 models 객체
 * @param {Array} subjectFilter - 선택된 과목 배열 (빈 배열이면 전체)
 * @param {number|null} scoreMaximum - 효율성 정규화에 쓸 만점 (없으면 데이터 최고점)
 * @return {Array} 비용 및 효율성 데이터
 */
export function getCostData(data, overallScores, tokenUsage = {}, subjectFilter = [], scoreMaximum = null) {
  // 모델별 가격 정보 수집 ($/1M 토큰)
  const priceMap = {}
  data.forEach(d => {
    if (!priceMap[d.model_name] && d.price) {
      priceMap[d.model_name] = {
        input: d.price.input ?? 0,
        output: d.price.output ?? 0
      }
    }
  })

  // 1단계: 기본 데이터 계산
  const results = overallScores.map(score => {
    const price = priceMap[score.model] || { input: 0, output: 0 }
    const usage = tokenUsage[score.model] || {}

    // 토큰 계산: 과목 필터 적용
    let inputTokens, outputTokens

    if (subjectFilter.length > 0) {
      // 과목 필터 활성화
      if (!usage.sections) {
        // sections 데이터 없으면 토큰 0으로 설정 (테이블에는 표시)
        inputTokens = 0
        outputTokens = 0
      } else {
        // 선택된 과목들의 토큰만 합산
        inputTokens = 0
        outputTokens = 0

        subjectFilter.forEach(subject => {
          // 과목명이 그대로거나 "과목-섹션" 형태인 키를 모두 포함
          Object.keys(usage.sections).forEach(sectionKey => {
            if (sectionKey.startsWith(subject + '-') || sectionKey === subject) {
              const section = usage.sections[sectionKey]
              inputTokens += section.input_tokens || 0
              outputTokens += section.output_tokens || 0
            }
          })
        })
      }
    } else {
      // 전체 토큰
      inputTokens = usage.total_input_tokens || 0
      outputTokens = usage.total_output_tokens || 0
    }

    // 실제 비용 계산: 토큰 수 × (가격 / 1M)
    const inputCostActual = inputTokens * (price.input / 1000000)
    const outputCostActual = outputTokens * (price.output / 1000000)
    const _getFiniteCost = (value) => {
      if (value === null || value === undefined || value === '') return null
      const number = Number(value)
      return Number.isFinite(number) && number >= 0 ? number : null
    }
    const _matchesFilter = (record) => subjectFilter.some(filter => (
      filter === record.subject
      || filter === `${record.subject}-${record.section}`
      || filter === `${record.section}-${record.subject}`
      || filter === record.section
    ))
    const modelRecords = data.filter(record => record.model_name === score.model)
    const selectedRecords = subjectFilter.length > 0
      ? modelRecords.filter(_matchesFilter)
      : modelRecords
    const recordCosts = selectedRecords.map(record => (
      _getFiniteCost(record.actual_cost_usd)
      ?? _getFiniteCost(record.token_usage?.cost_usd)
      ?? _getFiniteCost(record.cost_usd)
    ))

    let reportedCost = null
    if (subjectFilter.length === 0) {
      reportedCost = _getFiniteCost(usage.cost_usd) ?? _getFiniteCost(usage.actual_cost_usd)
    } else if (usage.sections) {
      const matchingSections = Object.entries(usage.sections)
        .filter(([sectionKey]) => subjectFilter.some(subject => (
          sectionKey.startsWith(`${subject}-`) || sectionKey === subject
        )))
        .map(([, section]) => _getFiniteCost(section.cost_usd) ?? _getFiniteCost(section.actual_cost_usd))
      if (matchingSections.length > 0 && matchingSections.every(value => value !== null)) {
        reportedCost = matchingSections.reduce((sum, value) => sum + value, 0)
      }
    }
    if (reportedCost === null && recordCosts.length > 0 && recordCosts.every(value => value !== null)) {
      reportedCost = recordCosts.reduce((sum, value) => sum + value, 0)
    }

    const totalCost = reportedCost ?? (inputCostActual + outputCostActual)

    return {
      model: score.model,
      score: score.total,
      inputPrice: price.input,       // $/1M 토큰 가격
      outputPrice: price.output,     // $/1M 토큰 가격
      inputTokens,
      outputTokens,
      totalCost,                     // 실제 테스트 비용 ($)
      hasReportedCost: reportedCost !== null
    }
  }).filter(Boolean) // sections 데이터 없는 모델 제외

  // 2단계: 효율성 계산 (좌표 기반)
  // 좌상단(고점수-저비용)일수록 높은 효율
  const maxCost = Math.max(...results.map(r => r.totalCost).filter(c => c > 0), 1)
  const configuredMax = Number(scoreMaximum)
  const SCORE_MAX = Number.isFinite(configuredMax) && configuredMax > 0
    ? configuredMax
    : Math.max(...results.map(r => Number(r.score) || 0), 1)

  return results.map(r => {
    if (r.totalCost <= 0) {
      return { ...r, efficiency: 0 }
    }
    // 점수 정규화: 0~1 (높을수록 좋음, 0~maxScore 기준)
    const scoreNorm = Math.max(0, Math.min(1, r.score / SCORE_MAX))
    // 비용 정규화: 0~1 (낮을수록 좋음, 반전하여 높을수록 좋음)
    const costNorm = r.totalCost / maxCost
    // 효율성: 0~100 (성능 70%, 비용 30% 가중치)
    const efficiency = (scoreNorm * 0.7 + (1 - costNorm) * 0.3) * 100

    return { ...r, efficiency }
  })
}
