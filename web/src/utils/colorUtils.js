/**
 * @file colorUtils.js
 * @brief 모델별 색상 관리 유틸리티
 *
 * generate_charts.py의 ChartConfig 색상 체계를 JavaScript로 포팅
 */

import { formatModelDisplayName, parseEffortSuffix } from './modelMeta.js'

/**
 * @brief 브랜드별 색상 상수
 *
 * 개발사별 색상 램프의 중간 단계를 대표색으로 쓴다. OpenAI는 원래 빨간색이었으나
 * Anthropic의 주황색과 색상이 인접해, 두 계열을 모두 어둡게 만들면 적록색약에서
 * 구분되지 않았다. 실제 브랜드에 더 가까운 청록색으로 옮겨 분리도를 확보했다.
 */
export const MODEL_COLORS = {
  GPT: '#0baa81',       // OpenAI - 청록색
  Gemini: '#5177fa',    // Google - 파란색
  Claude: '#bc5300',    // Anthropic - 주황색~갈색
  Mistral: '#FF6B35',   // Mistral AI - 주황색
  Grok: '#6A4C93',      // xAI - 보라색
  DeepSeek: '#1E3A8A',  // DeepSeek - 어두운 파란색
  EXAONE: '#A50034',    // LG - 자홍색
  Solar: '#B19CD9',     // Upstage - 연보라색
  Kimi: '#2D2D2D',      // Moonshot - 매우 어두운 회색
  GLM: '#1A73E8',       // Zhipu AI - 파란색
  Qwen: '#FF6A00',      // Alibaba - 주황색
  Kakao: '#FEE500',     // Kakao - 노란색
  MiniMax: '#E2167E',   // MiniMax - 로고 핑크색
  default: '#6B7280'    // 기타 - 회색
}

/**
 * @brief 개발사(Vendor) 정의
 * - pattern: 모델명 매칭용 정규식
 * - color: 브랜드 색상 (MODEL_COLORS 참조)
 */
export const VENDORS = [
  { id: 'openai', name: 'OpenAI', pattern: /gpt|^o\d/i, color: MODEL_COLORS.GPT },
  { id: 'google', name: 'Google', pattern: /gemini|gemma/i, color: MODEL_COLORS.Gemini },
  { id: 'anthropic', name: 'Anthropic', pattern: /claude/i, color: MODEL_COLORS.Claude },
  { id: 'mistral', name: 'Mistral AI', pattern: /mistral/i, color: MODEL_COLORS.Mistral },
  { id: 'xai', name: 'xAI', pattern: /grok/i, color: MODEL_COLORS.Grok },
  { id: 'deepseek', name: 'DeepSeek', pattern: /deepseek/i, color: MODEL_COLORS.DeepSeek },
  { id: 'lg', name: 'LG AI Research', pattern: /exaone/i, color: MODEL_COLORS.EXAONE },
  { id: 'upstage', name: 'Upstage', pattern: /solar/i, color: MODEL_COLORS.Solar },
  { id: 'moonshot', name: 'Moonshot', pattern: /kimi/i, color: MODEL_COLORS.Kimi },
  { id: 'zhipu', name: 'Zhipu AI', pattern: /glm/i, color: MODEL_COLORS.GLM },
  { id: 'alibaba', name: 'Alibaba', pattern: /qwen/i, color: MODEL_COLORS.Qwen },
  { id: 'kakao', name: 'Kakao', pattern: /kanana/i, color: MODEL_COLORS.Kakao },
  { id: 'minimax', name: 'MiniMax', pattern: /minimax/i, color: MODEL_COLORS.MiniMax },
  { id: 'other', name: '기타', pattern: null, color: MODEL_COLORS.default }
]

/**
 * @brief 모델명으로 개발사 객체 반환
 * @param {string} modelName - 모델명
 * @return {Object} 개발사 객체 { id, name, pattern, color }
 */
export function getVendor(modelName) {
  for (const v of VENDORS) {
    if (v.pattern?.test(modelName)) return v
  }
  return VENDORS.find(v => v.id === 'other')
}

/**
 * @brief 모델 목록을 개발사별로 그룹화
 * @param {string[]} models - 모델명 배열
 * @return {Object} { vendorId: [모델명, ...], ... }
 */
export function groupModelsByVendor(models) {
  const groups = {}
  VENDORS.forEach(v => { groups[v.id] = [] })

  models.forEach(model => {
    const vendor = getVendor(model)
    groups[vendor.id].push(model)
  })

  // 각 그룹 내 이름 내림차순 정렬 (최신 모델 위로)
  Object.keys(groups).forEach(id => {
    groups[id].sort((a, b) => b.localeCompare(a))
  })

  return groups
}

/**
 * @brief 개발사를 모델 수 기준 내림차순으로 정렬
 * @param {Object} groupedModels - groupModelsByVendor 결과
 * @return {Array} 정렬된 개발사 객체 배열
 */
export function getSortedVendors(groupedModels) {
  return VENDORS
    .filter(v => groupedModels[v.id]?.length > 0)
    .sort((a, b) => groupedModels[b.id].length - groupedModels[a.id].length)
}

/**
 * @brief 차트용 공통 색상 상수
 */
export const CHART_COLORS = {
  common: '#34A853',      // 공통 영역 - 초록색
  elective: '#FBBC04',    // 선택 영역 - 노란색
  correct: '#22c55e',     // 정답 - 초록색
  incorrect: '#ef4444',   // 오답 - 빨간색
  perfect: '#EA4335'      // 만점 - 빨간색
}

/**
 * @brief 개발사별 모델 계열 색상 램프 (어두운 단계 → 밝은 단계)
 *
 * 같은 개발사 모델을 구분하기 위한 램프이며, 색상(hue)은 개발사마다 고정해
 * 개발사 정체성을 유지한다. 다크 모드는 밝기를 자동으로 뒤집는 것이 아니라
 * 어두운 배경에서 대비를 확보한 별도 단계를 사용한다.
 *
 * dataviz 검증기(all-pairs)로 명도 밴드·채도 하한·색약 분리도·대비를 확인한 값이다.
 */
const MODEL_FAMILY_RAMPS = {
  light: {
    anthropic: ['#943f00', '#bc5300', '#e56701', '#ff8946'],
    openai: ['#028968', '#0baa81', '#00cc9b'],
    google: ['#2e4aca', '#5177fa', '#8caafd']
  },
  dark: {
    anthropic: ['#ae5600', '#cf6800', '#f07b01', '#fe9b55'],
    openai: ['#0b9583', '#02b49e', '#0fd4bb'],
    google: ['#2867e4', '#558ffe', '#91b7fe']
  }
}

/**
 * @brief 개발사별 모델 계열 순서 (램프 단계와 1:1 대응)
 *
 * 색은 점수 순위가 아니라 모델 계열에 고정된다. 필터로 표시 모델이 줄어도
 * 남은 모델의 색이 바뀌지 않도록 이 순서는 데이터와 무관하게 선언한다.
 * 점수가 비슷해 차트에서 나란히 놓이는 계열끼리 램프의 양 끝을 갖도록 배치했다.
 * 목록에 없는 계열은 이름 해시로 램프 단계를 정한다.
 */
const MODEL_FAMILY_ORDER = {
  anthropic: ['Claude Opus 5', 'Claude Opus 4.8', 'Claude Fable 5', 'Claude Sonnet 5'],
  openai: ['GPT-5.6 Terra', 'GPT-5.6 Sol', 'GPT-5.6 Luna'],
  google: ['Gemini 3.1 Pro Preview', 'Gemini 3.5 Flash-Lite', 'Gemini 3.6 Flash']
}

/**
 * @brief 계열명을 램프 길이 안의 안정적인 인덱스로 바꾼다.
 * @param {string} familyName - 추론 강도 접미사를 제거한 모델명
 * @param {number} length - 램프 단계 수
 * @return {number} 인덱스
 */
function _hashFamilyIndex(familyName, length) {
  let hash = 0
  for (let i = 0; i < familyName.length; i++) {
    hash = (hash * 31 + familyName.charCodeAt(i)) % 1000003
  }
  return hash % length
}

/**
 * @brief 모델명으로 색상 반환 (같은 개발사 안에서는 계열별로 다른 단계)
 * @param {string} modelName - 모델명
 * @param {boolean} darkMode - 다크 모드 여부
 * @return {string} HEX 색상 코드
 */
export function getModelColor(modelName, darkMode = false) {
  const vendor = getVendor(modelName)
  const ramp = MODEL_FAMILY_RAMPS[darkMode ? 'dark' : 'light'][vendor.id]

  if (ramp) {
    const { base } = parseEffortSuffix(formatModelDisplayName(modelName))
    const declared = MODEL_FAMILY_ORDER[vendor.id]?.indexOf(base) ?? -1
    const index = declared >= 0 ? declared : _hashFamilyIndex(base, ramp.length)
    return ramp[Math.min(index, ramp.length - 1)]
  }

  return _getVendorFallbackColor(modelName)
}

/**
 * @brief 램프가 없는 개발사의 단일 브랜드 색상
 * @param {string} modelName - 모델명
 * @return {string} HEX 색상 코드
 */
function _getVendorFallbackColor(modelName) {
  const name = modelName.toLowerCase()

  if (name.includes('gpt') || /^o\d/.test(name)) {
    return MODEL_COLORS.GPT
  }
  if (name.includes('gemini') || name.includes('gemma')) {
    return MODEL_COLORS.Gemini
  }
  if (name.includes('claude')) {
    return MODEL_COLORS.Claude
  }
  if (name.includes('mistral')) {
    return MODEL_COLORS.Mistral
  }
  if (name.includes('grok')) {
    return MODEL_COLORS.Grok
  }
  if (name.includes('deepseek')) {
    return MODEL_COLORS.DeepSeek
  }
  if (name.includes('exaone')) {
    return MODEL_COLORS.EXAONE
  }
  if (name.includes('solar')) {
    return MODEL_COLORS.Solar
  }
  if (name.includes('kimi')) {
    return MODEL_COLORS.Kimi
  }
  if (name.includes('glm')) {
    return MODEL_COLORS.GLM
  }
  if (name.includes('qwen')) {
    return MODEL_COLORS.Qwen
  }
  if (name.includes('kanana')) {
    return MODEL_COLORS.Kakao
  }
  if (name.includes('minimax')) {
    return MODEL_COLORS.MiniMax
  }

  return MODEL_COLORS.default
}

/**
 * @brief 모델명을 짧은 이름으로 변환 (규칙 기반)
 *
 * 규칙:
 * 1. '-'를 띄어쓰기로 변경 (단, 버전 번호 제외: V3.2 등)
 * 2. 'Preview, ' 제거
 * 3. K-EXAONE: '236B-A23B' 또는 '236B A23B' 삭제
 * 4. 괄호 처리:
 *    - 'Non-Thinking', 'minimal' → 괄호 전체 제거
 *    - 'Thinking', 'XXK Thinking', 'high' → 💡로 대체
 *    - 'low', 'max', 'none' → 추론 수준별 차트에서 구분이 필요하므로 그대로 유지
 *
 * @param {string} modelName - 원본 모델명
 * @return {string} 짧은 모델명
 */
export function getShortModelName(modelName) {
  let name = formatModelDisplayName(modelName)

  // 1. K-EXAONE 특수 처리: '236B-A23B' 또는 '236B A23B' 제거
  name = name.replace(/[-\s]?236B[-\s]?A23B/gi, '')

  // 2. 'Preview, ' 제거
  name = name.replace(/Preview,?\s*/gi, '')

  // 3. 괄호 내용 처리
  const parenMatch = name.match(/\(([^)]+)\)/)
  if (parenMatch) {
    const inner = parenMatch[1].toLowerCase()
    if (inner.includes('non-thinking') || inner === 'minimal') {
      // Non-Thinking, minimal → 괄호 전체 제거
      name = name.replace(/\s*\([^)]+\)/, '')
    } else if (inner.includes('thinking') || inner === 'high') {
      // Thinking, XXK Thinking, high → 💡
      name = name.replace(/\s*\([^)]+\)/, ' 💡')
    }
  }

  // 4. '-'를 띄어쓰기로 (버전 번호 V3.2 등은 유지)
  // DeepSeek-V3.2 → DeepSeek V3.2, GPT-5.1 → GPT 5.1
  name = name.replace(/-(?=[A-Za-z])/g, ' ')

  // 5. 중복 공백 정리
  name = name.replace(/\s+/g, ' ').trim()

  return name
}

/**
 * @brief HEX 색상을 밝게 조정
 * @param {string} hex - HEX 색상 코드 (예: '#EA4335')
 * @param {number} factor - 밝기 조정 비율 (0~1, 1이면 흰색)
 * @return {string} 조정된 HEX 색상 코드
 */
export function lightenColor(hex, factor = 0.5) {
  const hexColor = hex.replace('#', '')
  const r = parseInt(hexColor.slice(0, 2), 16)
  const g = parseInt(hexColor.slice(2, 4), 16)
  const b = parseInt(hexColor.slice(4, 6), 16)

  const newR = Math.round(r + (255 - r) * factor)
  const newG = Math.round(g + (255 - g) * factor)
  const newB = Math.round(b + (255 - b) * factor)

  return `#${newR.toString(16).padStart(2, '0')}${newG.toString(16).padStart(2, '0')}${newB.toString(16).padStart(2, '0')}`
}

/**
 * @brief HEX 색상을 어둡게 조정
 * @param {string} hex - HEX 색상 코드
 * @param {number} factor - 어둡기 조정 비율 (0~1, 1이면 검정)
 * @return {string} 조정된 HEX 색상 코드
 */
export function darkenColor(hex, factor = 0.3) {
  const hexColor = hex.replace('#', '')
  const r = parseInt(hexColor.slice(0, 2), 16)
  const g = parseInt(hexColor.slice(2, 4), 16)
  const b = parseInt(hexColor.slice(4, 6), 16)

  const newR = Math.round(r * (1 - factor))
  const newG = Math.round(g * (1 - factor))
  const newB = Math.round(b * (1 - factor))

  return `#${newR.toString(16).padStart(2, '0')}${newG.toString(16).padStart(2, '0')}${newB.toString(16).padStart(2, '0')}`
}

/**
 * @brief 히트맵 셀 색상 반환 (정답/오답 × 배점)
 * @param {boolean} isCorrect - 정답 여부
 * @param {number} points - 배점
 * @param {boolean} darkMode - 다크모드 여부
 * @return {string} HEX 색상 코드
 */
export function getHeatmapColor(isCorrect, points, darkMode = false) {
  if (isCorrect === undefined || isCorrect === null) {
    return darkMode ? '#374151' : '#f0f0f0'
  }

  if (isCorrect) {
    // 정답: 배점에 따라 초록색 진하기 조절
    if (points >= 3) {
      return darkMode ? '#16a34a' : '#22c55e'
    }
    return darkMode ? '#166534' : '#86efac'
  } else {
    // 오답: 배점에 따라 빨간색 진하기 조절
    if (points >= 3) {
      return darkMode ? '#dc2626' : '#ef4444'
    }
    return darkMode ? '#991b1b' : '#fca5a5'
  }
}

/**
 * @brief CSS 변수 값 가져오기
 * @param {string} varName - CSS 변수명 (예: '--color-bg-primary')
 * @return {string} CSS 변수 값
 */
export function getCSSVariable(varName) {
  if (typeof document === 'undefined') return ''
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
}

/**
 * @brief 현재 테마가 다크모드인지 확인
 * @return {boolean} 다크모드 여부
 */
export function isDarkMode() {
  if (typeof document === 'undefined') return false
  return document.documentElement.classList.contains('dark')
}

/**
 * @brief 개발사별 상위 모델 기본 선택 목록 계산
 * @param {string[]} models - 전체 모델명 배열
 * @param {Array} scoreData - calculateAllModelScores 결과 (총점 내림차순)
 * @return {string[]} 기본 선택된 모델명 배열
 *
 * 규칙:
 * - 개발사가 6개 이상이면 최고 총점 모델 기준으로 개발사 상위 50%를 계산
 * - 개발사가 6개 이상이면 상위 50% 개발사만 여러 모델 선택
 * - 개발사가 5개 이하이면 모든 개발사가 여러 모델 선택
 * - 여러 모델 선택 대상 개발사: min(floor(50%), 3개) 모델 선택 (최소 1개)
 * - 그 외 개발사: 기본 표기 최대 1개
 * - 동점 개발사는 최고 점수 → 평균 점수 → 개발사 ID 순으로 정렬
 * - scoreData에 없는 모델은 점수 0으로 처리
 */
export function getDefaultSelectedModels(models, scoreData) {
  const grouped = groupModelsByVendor(models)
  const scoreMap = new Map(scoreData.map(s => [s.model, s.total]))
  const result = []
  const vendorEntries = Object.entries(grouped).filter(([, vendorModels]) => vendorModels.length > 0)

  const getModelScore = (model) => scoreMap.get(model) || 0

  const multiModelVendorIds = new Set(vendorEntries.map(([vendorId]) => vendorId))
  if (vendorEntries.length >= 6) {
    const vendorRankings = vendorEntries
      .map(([vendorId, vendorModels]) => {
        const scores = vendorModels.map(getModelScore)
        const topScore = scores.length ? Math.max(...scores) : 0
        const averageScore = scores.length
          ? scores.reduce((sum, score) => sum + score, 0) / scores.length
          : 0

        return {
          vendorId,
          topScore,
          averageScore
        }
      })
      .sort((a, b) => {
        if (b.topScore !== a.topScore) return b.topScore - a.topScore
        if (b.averageScore !== a.averageScore) return b.averageScore - a.averageScore
        return a.vendorId.localeCompare(b.vendorId)
      })

    const topVendorCount = Math.ceil(vendorRankings.length / 2)
    multiModelVendorIds.clear()
    vendorRankings.slice(0, topVendorCount).forEach(vendor => {
      multiModelVendorIds.add(vendor.vendorId)
    })
  }

  vendorEntries.forEach(([vendorId, vendorModels]) => {
    if (vendorModels.length === 0) return

    // 점수 기준 내림차순 정렬
    const sorted = [...vendorModels].sort((a, b) => {
      const scoreDiff = getModelScore(b) - getModelScore(a)
      if (scoreDiff !== 0) return scoreDiff
      return a.localeCompare(b)
    })

    const limit = multiModelVendorIds.has(vendorId)
      ? Math.min(Math.max(1, Math.floor(vendorModels.length * 0.5)), 3)
      : 1

    result.push(...sorted.slice(0, limit))
  })

  return result
}
