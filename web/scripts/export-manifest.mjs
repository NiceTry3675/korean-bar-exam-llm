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

/** @description 과목별 점수 비교 내보내기 대상 생성 */
function createSubjectScoreTarget({ id, fileName, subject }) {
  return createImageTarget({
    id,
    group: 'overview',
    exportKey: 'overview-score-chart',
    fileName,
    params: { tab: 'overview', subjects: subject }
  })
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
  createImageTarget({
    id: 'overview-total',
    group: 'overview',
    exportKey: 'overview-score-chart',
    fileName: '전체.png',
    params: { tab: 'overview' }
  }),
  createSubjectScoreTarget({
    id: 'subject-public-law',
    fileName: '공법.png',
    subject: '공법'
  }),
  createSubjectScoreTarget({
    id: 'subject-civil-law',
    fileName: '민사법.png',
    subject: '민사법'
  }),
  createSubjectScoreTarget({
    id: 'subject-criminal-law',
    fileName: '형사법.png',
    subject: '형사법'
  }),
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
