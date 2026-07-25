import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const repoRoot = path.resolve(__dirname, '..', '..')
const imagesDir = path.join(repoRoot, 'docs', 'images')
export const EXPORT_GROUP_ORDER = ['overview', 'subjects', 'cost']

function createImageTarget({ id, group, exportKey, fileName, params }) {
  return {
    id,
    group,
    exportKey,
    outputPath: path.join(imagesDir, fileName),
    params: {
      theme: 'light',
      // 기본 표시 규칙은 개발사당 상위 몇 개만 고르므로 README 이미지에서는
      // 전체 모델을 명시한다.
      models: 'all',
      ...params
    }
  }
}

/**
 * @description 추론 수준별 점수 차트의 내보내기 키
 *
 * 개요 탭은 고추론·저추론 차트를 따로 렌더링하므로 대상도 등급별로 나눈다.
 */
const EFFORT_TIERS = [
  { tier: 'high', exportKey: 'overview-score-chart-high', suffix: '고추론' },
  { tier: 'low', exportKey: 'overview-score-chart-low', suffix: '저추론' }
]

/** @description 전체 점수 비교 내보내기 대상 생성 (추론 수준별) */
function createOverallScoreTargets() {
  return EFFORT_TIERS.map(({ tier, exportKey, suffix }) => createImageTarget({
    id: `overview-total-${tier}`,
    group: 'overview',
    exportKey,
    fileName: `전체_${suffix}.png`,
    params: { tab: 'overview' }
  }))
}

/** @description 과목별 점수 비교 내보내기 대상 생성 (추론 수준별) */
function createSubjectScoreTargets({ id, subject }) {
  return EFFORT_TIERS.map(({ tier, exportKey, suffix }) => createImageTarget({
    id: `${id}-${tier}`,
    group: 'overview',
    exportKey,
    fileName: `${subject}_${suffix}.png`,
    params: { tab: 'overview', subjects: subject }
  }))
}

/** @description 과목별 정오표(히트맵) 내보내기 대상 생성 */
function createHeatmapTarget({ id, fileName, subject }) {
  return createImageTarget({
    id,
    group: 'subjects',
    exportKey: 'question-heatmap',
    fileName,
    params: {
      tab: 'subjects',
      selectedSubject: subject,
      selectedSection: subject
    }
  })
}

export const EXPORT_TARGETS = [
  ...createOverallScoreTargets(),
  ...createSubjectScoreTargets({ id: 'subject-public-law', subject: '공법' }),
  ...createSubjectScoreTargets({ id: 'subject-civil-law', subject: '민사법' }),
  ...createSubjectScoreTargets({ id: 'subject-criminal-law', subject: '형사법' }),
  createImageTarget({
    id: 'score-table',
    group: 'overview',
    exportKey: 'benchmark-score-table',
    fileName: '점수표.png',
    params: { tab: 'overview' }
  }),
  createHeatmapTarget({
    id: 'heatmap-public-law',
    fileName: '정오표_공법.png',
    subject: '공법'
  }),
  createHeatmapTarget({
    id: 'heatmap-civil-law',
    fileName: '정오표_민사법.png',
    subject: '민사법'
  }),
  createHeatmapTarget({
    id: 'heatmap-criminal-law',
    fileName: '정오표_형사법.png',
    subject: '형사법'
  }),
  createImageTarget({
    id: 'cost-analysis',
    group: 'cost',
    exportKey: 'cost-scatter',
    fileName: '비용_분석.png',
    params: { tab: 'cost' }
  }),
  createImageTarget({
    id: 'token-usage',
    group: 'cost',
    exportKey: 'token-usage',
    fileName: '토큰_사용량.png',
    params: { tab: 'cost' }
  }),
  createImageTarget({
    id: 'cost-table',
    group: 'cost',
    exportKey: 'cost-table',
    fileName: '비용표.png',
    params: { tab: 'cost' }
  })
]

export function getExportTargetById(id) {
  return EXPORT_TARGETS.find(target => target.id === id) || null
}

export function getExportTargetsByGroup(group) {
  return EXPORT_TARGETS.filter(target => target.group === group)
}
