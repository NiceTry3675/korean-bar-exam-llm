/**
 * @file heatmapTransform.js
 * @brief 히트맵 및 레이더 차트용 데이터 변환 유틸리티
 */

/**
 * @brief all_results.json 데이터를 히트맵용 형식으로 변환
 * @param {Array} data - all_results.json 전체 데이터
 * @param {string} subject - 과목명
 * @param {string} section - 섹션명
 * @return {Object} { questionNumber: { modelName: { isCorrect, points } } }
 */
export function transformToHeatmapData(data, subject, section) {
  const filtered = data.filter(d => d.subject === subject && d.section === section)
  const heatmap = {}

  filtered.forEach(entry => {
    if (!entry.results) return

    entry.results.forEach(result => {
      const qNum = result.question_number
      if (!heatmap[qNum]) {
        heatmap[qNum] = {}
      }
      heatmap[qNum][entry.model_name] = {
        isCorrect: result.is_correct,
        points: result.points,
        extractedAnswer: result.extracted_answer,
        correctAnswer: result.correct_answer,
        answerStatus: result.answer_status || (result.extracted_answer === -1 ? 'no_answer' : 'answered'),
        providerStopReason: result.provider_stop_reason || null
      }
    })
  })

  return heatmap
}

/**
 * @brief 히트맵 데이터에서 문항 번호 목록 추출 (정렬됨)
 * @param {Object} heatmapData - transformToHeatmapData 결과
 * @return {Array<number>} 정렬된 문항 번호 배열
 */
export function getQuestionNumbers(heatmapData) {
  return Object.keys(heatmapData)
    .map(Number)
    .sort((a, b) => a - b)
}

/**
 * @brief 모델별 정답 수 계산
 * @param {Object} heatmapData - transformToHeatmapData 결과
 * @param {string} modelName - 모델명
 * @return {Object} { correct, total, accuracy }
 */
export function calculateModelAccuracy(heatmapData, modelName) {
  const questions = Object.keys(heatmapData)
  let correct = 0
  let total = 0

  questions.forEach(qNum => {
    const cell = heatmapData[qNum]?.[modelName]
    if (cell !== undefined) {
      total++
      if (cell.isCorrect) correct++
    }
  })

  return {
    correct,
    total,
    accuracy: total > 0 ? (correct / total) * 100 : 0
  }
}
