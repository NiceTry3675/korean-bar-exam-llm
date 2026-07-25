import test from 'node:test'
import assert from 'node:assert/strict'

import {
  getEffortTier,
  hasPostExamKnowledgeCutoff,
  parseEffortSuffix
} from './modelMeta.js'
import { getShortModelName } from './colorUtils.js'

test('추론 강도 접미사를 이름과 분리한다', () => {
  assert.deepEqual(
    parseEffortSuffix('Claude Opus 5 (max)'),
    { base: 'Claude Opus 5', effort: 'max' }
  )
  assert.deepEqual(
    parseEffortSuffix('Gemini 3.1 Pro Preview (low)'),
    { base: 'Gemini 3.1 Pro Preview', effort: 'low' }
  )
  assert.deepEqual(
    parseEffortSuffix('GPT-5.6 Luna (none)'),
    { base: 'GPT-5.6 Luna', effort: 'none' }
  )
})

test('접미사가 없거나 다른 괄호는 추론 강도로 보지 않는다', () => {
  assert.deepEqual(
    parseEffortSuffix('Some Model'),
    { base: 'Some Model', effort: null }
  )
  assert.deepEqual(
    parseEffortSuffix('Some Model (Preview)'),
    { base: 'Some Model (Preview)', effort: null }
  )
  assert.equal(getEffortTier('Some Model'), null)
})

test('max·high는 고추론, none·low는 저추론으로 묶인다', () => {
  assert.equal(getEffortTier('Claude Opus 5 (max)'), 'high')
  assert.equal(getEffortTier('Claude Opus 5 (high)'), 'high')
  assert.equal(getEffortTier('Gemini 3.6 Flash (high)'), 'high')
  assert.equal(getEffortTier('Claude Opus 5 (none)'), 'low')
  assert.equal(getEffortTier('Gemini 3.6 Flash (low)'), 'low')
})

test('문제 학습 가능성 표시는 Opus 5 계열에만 적용된다', () => {
  assert.equal(hasPostExamKnowledgeCutoff('Claude Opus 5 (max)'), true)
  assert.equal(hasPostExamKnowledgeCutoff('Claude Opus 5 (none)'), true)
  assert.equal(hasPostExamKnowledgeCutoff('Claude Opus 4.8 (max)'), false)
  assert.equal(hasPostExamKnowledgeCutoff('Claude Fable 5 (max)'), false)
  assert.equal(hasPostExamKnowledgeCutoff('Gemini 3.1 Pro Preview (high)'), false)
})

test('추론 수준별 차트에서 구분이 필요한 접미사는 짧은 이름에도 남는다', () => {
  // low·max·none은 같은 차트에 함께 놓이므로 제거하면 서로 구별할 수 없다
  assert.match(getShortModelName('Gemini 3.6 Flash (low)'), /\(low\)/)
  assert.match(getShortModelName('Claude Opus 5 (max)'), /\(max\)/)
  assert.match(getShortModelName('Claude Opus 5 (none)'), /\(none\)/)
  assert.match(getShortModelName('Claude Opus 5 (high)'), /💡/)
})
