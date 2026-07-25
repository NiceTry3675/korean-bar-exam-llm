import test from 'node:test'
import assert from 'node:assert/strict'

import { parseDashboardQueryState } from './urlState.js'

test('파라미터가 없으면 기본 벤치와 기본 모드를 사용한다', () => {
  const state = parseDashboardQueryState('?tab=subjects')
  assert.equal(state.benchmark, '')
  assert.equal(state.mode, 'default')
  assert.equal(state.tab, 'subjects')
})

test('변호사시험 벤치와 실행 모드를 URL에서 읽는다', () => {
  const state = parseDashboardQueryState('?benchmark=bar-exam-15&mode=hard')
  assert.equal(state.benchmark, 'bar-exam-15')
  assert.equal(state.mode, 'hard')
})

test('잘못된 모드는 기본 모드로 안전하게 되돌린다', () => {
  const state = parseDashboardQueryState('?benchmark=bar-exam-15&mode=invalid')
  assert.equal(state.mode, 'default')
})

test('법 과목 필터를 URL에서 읽는다', () => {
  const state = parseDashboardQueryState(
    '?subjects=%EA%B3%B5%EB%B2%95,%ED%98%95%EC%82%AC%EB%B2%95'
  )
  assert.deepEqual(state.subjects, ['공법', '형사법'])
})

test('models 파라미터로 표시 모델을 명시할 수 있다', () => {
  assert.deepEqual(parseDashboardQueryState('?models=all').models, ['all'])
  assert.deepEqual(
    parseDashboardQueryState('?models=Claude Fable 5 (max),GPT-5.6 Sol (max)').models,
    ['Claude Fable 5 (max)', 'GPT-5.6 Sol (max)']
  )
  assert.deepEqual(parseDashboardQueryState('').models, [])
})
