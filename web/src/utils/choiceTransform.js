/**
 * @file choiceTransform.js
 * @brief heatmapData를 선지 선택률 차트용으로 변환하는 유틸리티
 */

/**
 * @brief heatmapData를 선지 선택률 차트용으로 변환
 * @param {Object} heatmapData - { questionNumber: { modelName: { extractedAnswer, correctAnswer, ... } } }
 * @param {Array<string>} models - 모델 목록
 * @return {Array} 문항별 선지 선택률 배열
 */
export function transformToChoiceData(heatmapData, models) {
  const questions = Object.keys(heatmapData)
    .map(Number)
    .sort((a, b) => a - b)

  return questions.map(qNum => {
    const choices = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 }
    let validModels = 0
    let correctAnswer = null

    models.forEach(model => {
      const cell = heatmapData[qNum]?.[model]
      if (
        cell
        && cell.answerStatus === 'answered'
        && cell.extractedAnswer >= 1
        && cell.extractedAnswer <= 5
      ) {
        choices[cell.extractedAnswer]++
        validModels++
        if (!correctAnswer) correctAnswer = cell.correctAnswer
      }
    })

    const result = { question: qNum, correctAnswer, totalModels: validModels }
    for (let i = 1; i <= 5; i++) {
      result[`choice${i}`] = choices[i]
      result[`choice${i}Pct`] = validModels > 0 ? (choices[i] / validModels) * 100 : 0
    }
    return result
  })
}
