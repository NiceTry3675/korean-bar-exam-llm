import test from 'node:test'
import assert from 'node:assert/strict'

import { getModelColor, getVendor, MODEL_COLORS } from './colorUtils.js'

test('같은 개발사라도 모델 계열마다 다른 색을 쓴다', () => {
  const anthropic = [
    'Claude Opus 5 (max)',
    'Claude Fable 5 (max)',
    'Claude Opus 4.8 (max)',
    'Claude Sonnet 5 (max)'
  ].map(model => getModelColor(model))

  assert.equal(new Set(anthropic).size, 4, '네 계열이 모두 다른 색이어야 한다')

  const openai = ['GPT-5.6 Sol (high)', 'GPT-5.6 Terra (high)', 'GPT-5.6 Luna (high)']
    .map(model => getModelColor(model))
  assert.equal(new Set(openai).size, 3)

  const google = [
    'Gemini 3.1 Pro Preview (high)',
    'Gemini 3.6 Flash (high)',
    'Gemini 3.5 Flash-Lite (high)'
  ].map(model => getModelColor(model))
  assert.equal(new Set(google).size, 3)
})

test('같은 계열의 추론 강도는 같은 색을 공유한다', () => {
  const color = getModelColor('Claude Opus 5 (max)')
  assert.equal(getModelColor('Claude Opus 5 (high)'), color)
  assert.equal(getModelColor('Claude Opus 5 (none)'), color)
})

test('색은 표시 목록이 아니라 모델에 고정된다', () => {
  // 필터로 모델 수가 줄어도 남은 모델의 색이 바뀌지 않아야 한다
  const before = getModelColor('Claude Sonnet 5 (high)')
  const others = ['Claude Opus 5 (max)', 'Claude Fable 5 (max)'].map(m => getModelColor(m))
  const after = getModelColor('Claude Sonnet 5 (high)')
  assert.equal(before, after)
  assert.ok(!others.includes(after))
})

test('다크 모드는 어두운 배경용 단계를 따로 쓴다', () => {
  const light = getModelColor('Claude Opus 5 (max)', false)
  const dark = getModelColor('Claude Opus 5 (max)', true)
  assert.notEqual(light, dark)
  // 기본값은 라이트 모드
  assert.equal(getModelColor('Claude Opus 5 (max)'), light)
})

test('개발사 대표색은 램프의 중간 단계와 같은 계열이다', () => {
  // OpenAI는 Anthropic 주황과 겹치지 않도록 청록 계열을 쓴다
  assert.equal(getVendor('GPT-5.6 Sol (high)').color, MODEL_COLORS.GPT)
  assert.equal(getVendor('Claude Opus 5 (max)').color, MODEL_COLORS.Claude)
  assert.equal(getVendor('Gemini 3.6 Flash (low)').color, MODEL_COLORS.Gemini)
})

test('램프가 없는 개발사는 단일 브랜드 색으로 되돌아간다', () => {
  assert.equal(getModelColor('Mistral Large (high)'), MODEL_COLORS.Mistral)
  assert.equal(getModelColor('Grok 4 (high)'), MODEL_COLORS.Grok)
  assert.equal(getModelColor('무명 모델'), MODEL_COLORS.default)
})

test('목록에 없는 계열도 항상 같은 색을 받는다', () => {
  const first = getModelColor('Claude Nova 9 (high)')
  const second = getModelColor('Claude Nova 9 (none)')
  assert.equal(first, second)
  assert.match(first, /^#[0-9a-f]{6}$/i)
})
