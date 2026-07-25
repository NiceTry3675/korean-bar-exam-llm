import test from 'node:test'
import assert from 'node:assert/strict'

import {
  calculateAllBenchmarkScores,
  calculateBenchmarkScore,
  getBenchmarkMaxScore,
  transformBenchmarkRadarData
} from './benchmarkTransform.js'
import {
  getBenchmarkReferenceLines,
  getDefaultBenchmarkId,
  getNavigableBenchmarks,
  resolveBenchmark
} from './benchmarkRegistry.js'
import { getCostData } from './costTransform.js'

const benchmark = {
  id: 'bar-exam-15',
  scoring: { maxScore: 375, totalQuestions: 150, pointsPerQuestion: 2.5 },
  sections: [
    { sheet: '공법', subject: '공법', section: '공법', questionCount: 40, maxScore: 100 },
    { sheet: '민사법', subject: '민사법', section: '민사법', questionCount: 70, maxScore: 175 },
    { sheet: '형사법', subject: '형사법', section: '형사법', questionCount: 40, maxScore: 100 }
  ]
}

const data = [
  { sheet_name: '공법', subject: '공법', section: '공법', model_name: 'Fixture', score: 97.5, correct_count: 39, total_questions: 40 },
  { sheet_name: '민사법', subject: '민사법', section: '민사법', model_name: 'Fixture', score: 150, correct_count: 60, total_questions: 70 },
  { sheet_name: '형사법', subject: '형사법', section: '형사법', model_name: 'Fixture', score: 75, correct_count: 30, total_questions: 40 }
]

test('변호사시험 점수는 2.5점 단위와 정답률을 보존한다', () => {
  const result = calculateBenchmarkScore(data, 'Fixture', benchmark)
  assert.equal(result.total, 322.5)
  assert.equal(result.maxScore, 375)
  assert.equal(result.correctCount, 129)
  assert.equal(result.totalQuestions, 150)
  assert.equal(result.accuracy, 86)
})

test('과목 필터는 해당 과목의 점수와 만점만 합산한다', () => {
  const result = calculateBenchmarkScore(data, 'Fixture', benchmark, ['민사법'])
  assert.equal(result.total, 150)
  assert.equal(result.maxScore, 175)
  assert.equal(getBenchmarkMaxScore(benchmark, ['민사법']), 175)
})

test('375점 만점 모델을 공식점수 기준으로 가장 먼저 정렬한다', () => {
  const perfect = data.map(record => ({
    ...record,
    model_name: 'Perfect Fixture',
    score: record.total_questions * 2.5,
    correct_count: record.total_questions
  }))
  const scores = calculateAllBenchmarkScores(
    [...data, ...perfect],
    ['Fixture', 'Perfect Fixture'],
    benchmark
  )

  assert.equal(scores[0].model, 'Perfect Fixture')
  assert.equal(scores[0].total, 375)
  assert.equal(scores[0].correctCount, 150)
})

test('레이더 데이터는 각 법 과목을 100점으로 정규화한다', () => {
  const score = calculateBenchmarkScore(data, 'Fixture', benchmark)
  const radar = transformBenchmarkRadarData([score], ['Fixture'], benchmark)
  assert.equal(radar.data.length, 3)
  assert.equal(radar.data[0].Fixture, 97.5)
  assert.equal(radar.data[1].Fixture, (150 / 175) * 100)
})

test('단일 벤치마크 레지스트리는 변호사시험을 기본값으로 노출한다', () => {
  const registry = {
    defaultBenchmark: 'bar-exam-15',
    benchmarks: [
      { id: 'bar-exam-15', navigation: { visible: true, visibleWhenResults: true } }
    ]
  }

  assert.equal(getDefaultBenchmarkId(registry), 'bar-exam-15')
  assert.equal(resolveBenchmark(registry, 'unknown').id, 'bar-exam-15')
  assert.deepEqual(
    getNavigableBenchmarks(registry, {}).map(item => item.id),
    ['bar-exam-15'],
    'visible이 참이면 결과 존재 여부와 무관하게 노출한다'
  )
})

test('결과가 없으면 숨김 정책 벤치마크는 메뉴에 노출하지 않는다', () => {
  const registry = {
    defaultBenchmark: 'bar-exam-15',
    benchmarks: [
      { id: 'bar-exam-15', navigation: { visible: true } },
      { id: 'bar-exam-16', navigation: { visible: false, visibleWhenResults: true } }
    ]
  }

  assert.deepEqual(
    getNavigableBenchmarks(registry, { 'bar-exam-16': false }).map(item => item.id),
    ['bar-exam-15']
  )
  assert.deepEqual(
    getNavigableBenchmarks(registry, { 'bar-exam-16': true }).map(item => item.id),
    ['bar-exam-15', 'bar-exam-16']
  )
})

test('v2 필드가 없으면 관대 채점 합계는 공식 점수와 같다', () => {
  const result = calculateBenchmarkScore(data, 'Fixture', benchmark)
  assert.equal(result.totalLenient, result.total)
  assert.equal(result.correctCountLenient, result.correctCount)
  result.sectionScores.forEach(section => {
    assert.equal(section.scoreLenient, section.score)
  })
})

test('v2 점수는 과목별로 합산되고 공식 점수를 바꾸지 않는다', () => {
  const lenientData = data.map(record => (
    record.sheet_name === '공법'
      ? { ...record, score_lenient: 100, correct_count_lenient: 40 }
      : record
  ))
  const result = calculateBenchmarkScore(lenientData, 'Fixture', benchmark)

  assert.equal(result.total, 322.5, '공식 점수는 v1 그대로다')
  assert.equal(result.totalLenient, 325)
  assert.equal(result.correctCount, 129)
  assert.equal(result.correctCountLenient, 130)

  const publicLaw = result.sectionScores.find(section => section.sheet === '공법')
  assert.equal(publicLaw.score, 97.5)
  assert.equal(publicLaw.scoreLenient, 100)
})

test('과목 필터를 적용하면 해당 과목의 v2 점수만 합산한다', () => {
  const lenientData = data.map(record => (
    record.sheet_name === '공법'
      ? { ...record, score_lenient: 100 }
      : { ...record, score_lenient: record.score + 5 }
  ))
  const result = calculateBenchmarkScore(lenientData, 'Fixture', benchmark, ['공법'])

  assert.equal(result.total, 97.5)
  assert.equal(result.totalLenient, 100)
})

test('기준선은 전체 만점 기준일 때만 노출된다', () => {
  const withLine = {
    ...benchmark,
    referenceLines: [{ id: 'human-pass-line', score: 247.5, correctCount: 99 }]
  }

  assert.deepEqual(
    getBenchmarkReferenceLines(withLine).map(line => line.score),
    [247.5]
  )
  assert.deepEqual(
    getBenchmarkReferenceLines(withLine, ['민사법']),
    [],
    '과목 필터로 만점이 달라지면 총점 기준선은 비교 대상이 아니다'
  )
})

test('만점을 넘거나 값이 잘못된 기준선은 버린다', () => {
  const lines = getBenchmarkReferenceLines({
    ...benchmark,
    referenceLines: [
      { id: 'ok', score: 200 },
      { id: 'over', score: 400 },
      { id: 'zero', score: 0 },
      { id: 'nan', score: 'abc' },
      {}
    ]
  })
  assert.deepEqual(lines.map(line => line.id), ['ok'])
})

test('기준선이 없는 벤치마크는 빈 배열을 반환한다', () => {
  assert.deepEqual(getBenchmarkReferenceLines(benchmark), [])
  assert.deepEqual(getBenchmarkReferenceLines(null), [])
})

test('공급자가 기록한 실제 비용을 가격표 재계산보다 우선한다', () => {
  const costData = getCostData(
    [{
      model_name: 'Fixture',
      subject: '공법',
      section: '공법',
      price: { input: 100, output: 100 },
      actual_cost_usd: 0.25
    }],
    [{ model: 'Fixture', total: 90 }],
    {
      Fixture: {
        total_input_tokens: 1000,
        total_output_tokens: 1000,
        cost_usd: 0.25
      }
    },
    [],
    100
  )

  assert.equal(costData[0].totalCost, 0.25)
  assert.equal(costData[0].hasReportedCost, true)
})

test('과목 필터에서는 선택한 섹션의 실제 비용만 합산한다', () => {
  const costData = getCostData(
    [
      { model_name: 'Fixture', subject: '공법', section: '공법', price: { input: 1, output: 1 } },
      { model_name: 'Fixture', subject: '민사법', section: '민사법', price: { input: 1, output: 1 } }
    ],
    [{ model: 'Fixture', total: 90 }],
    {
      Fixture: {
        sections: {
          공법: { input_tokens: 10, output_tokens: 20, cost_usd: 0.1 },
          민사법: { input_tokens: 30, output_tokens: 40, cost_usd: 0.2 }
        }
      }
    },
    ['민사법'],
    175
  )

  assert.equal(costData[0].inputTokens, 30)
  assert.equal(costData[0].outputTokens, 40)
  assert.equal(costData[0].totalCost, 0.2)
})
