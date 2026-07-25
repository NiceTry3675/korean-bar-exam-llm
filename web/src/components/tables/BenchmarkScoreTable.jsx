/**
 * @file BenchmarkScoreTable.jsx
 * @brief 단순 합산형 벤치마크의 모델별 공식점수 및 정답률 표
 */

import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getModelColor } from '@/utils/colorUtils'
import { formatModelDisplayName } from '@/utils/modelMeta'
import { translateSubject } from '@/utils/subjectLabels'
import { useExportImage, README_EXPORT_WIDTH } from '@/hooks/useExportImage'
import { BenchmarkNote, ExportButton, ExportWatermark } from '@/components/common'

function _formatScore(value) {
  const number = Number(value) || 0
  return Number.isInteger(number) ? String(number) : number.toFixed(1)
}

/**
 * @brief 변호사시험 등 합산형 벤치마크 점수표
 * @param {Object} props - 점수표 데이터와 상호작용 콜백
 */
export default function BenchmarkScoreTable({
  data,
  title,
  maxScore,
  totalQuestions,
  hoveredModel,
  onModelHover
}) {
  const { t } = useTranslation()
  const { ref, exportImage } = useExportImage({ exportWidth: README_EXPORT_WIDTH })
  const [sortConfig, setSortConfig] = useState({ key: 'total', direction: 'desc' })
  const sections = data?.[0]?.sectionScores || []

  const sortedData = useMemo(() => {
    const rows = [...(data || [])]
    return rows.sort((left, right) => {
      const _getValue = (row) => {
        if (sortConfig.key.startsWith('section:')) {
          const key = sortConfig.key.slice('section:'.length)
          return row.sectionScores.find(section => section.key === key)?.score || 0
        }
        return row[sortConfig.key] ?? ''
      }
      const leftValue = _getValue(left)
      const rightValue = _getValue(right)
      if (typeof leftValue === 'string') {
        const order = leftValue.localeCompare(rightValue)
        return sortConfig.direction === 'asc' ? order : -order
      }
      return sortConfig.direction === 'asc'
        ? leftValue - rightValue
        : rightValue - leftValue
    })
  }, [data, sortConfig])

  const _handleSort = (key) => {
    setSortConfig(previous => ({
      key,
      direction: previous.key === key && previous.direction === 'desc' ? 'asc' : 'desc'
    }))
  }

  const _sortMarker = (key) => {
    if (sortConfig.key !== key) return '↕'
    return sortConfig.direction === 'desc' ? '↓' : '↑'
  }

  if (!data?.length) {
    return <div className="flex items-center justify-center h-48 text-gray-500 dark:text-gray-400">{t('common.noData')}</div>
  }

  return (
    <div ref={ref} className="w-full">
      <div className="flex items-start justify-between mb-4">
        {title && <h3 className="text-xl font-semibold text-gray-800 dark:text-gray-200">{title}</h3>}
        <div className="flex items-start gap-2">
          <ExportWatermark />
          <ExportButton
            onClick={() => exportImage(`${t('charts.scoreTable')}.png`)}
            exportKey="benchmark-score-table"
          />
        </div>
      </div>
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700">
            <tr>
              <th className="px-3 py-2 text-left cursor-pointer" onClick={() => _handleSort('model')}>
                {t('table.model')} {_sortMarker('model')}
              </th>
              {sections.map(section => (
                <th
                  key={section.key}
                  className="px-3 py-2 text-right cursor-pointer whitespace-nowrap"
                  onClick={() => _handleSort(`section:${section.key}`)}
                >
                  {translateSubject(section.subject, t)}<br />
                  <span className="text-xs font-normal text-gray-500 dark:text-gray-400">
                    / {section.maxScore} {_sortMarker(`section:${section.key}`)}
                  </span>
                </th>
              ))}
              <th className="px-3 py-2 text-right cursor-pointer whitespace-nowrap" onClick={() => _handleSort('total')}>
                {t('table.officialScore')}<br />
                <span className="text-xs font-normal text-gray-500 dark:text-gray-400">
                  / {maxScore} {_sortMarker('total')}
                </span>
              </th>
              <th className="px-3 py-2 text-right cursor-pointer whitespace-nowrap" onClick={() => _handleSort('accuracy')}>
                {t('table.accuracy')} {_sortMarker('accuracy')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedData.map((row, index) => {
              const isHovered = hoveredModel === row.model
              return (
                <tr
                  key={row.model}
                  className={`border-t border-gray-100 dark:border-gray-700 ${isHovered ? 'bg-blue-50 dark:bg-blue-900/30' : 'hover:bg-gray-50 dark:hover:bg-gray-700'}`}
                  onMouseEnter={() => onModelHover?.(row.model)}
                  onMouseLeave={() => onModelHover?.(null)}
                >
                  <td className="px-3 py-2 text-gray-800 dark:text-gray-200 whitespace-nowrap">
                    <span className="text-gray-400 mr-2">#{index + 1}</span>
                    <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ backgroundColor: getModelColor(row.model) }} />
                    {formatModelDisplayName(row.model)}
                  </td>
                  {sections.map(section => {
                    const value = row.sectionScores.find(item => item.key === section.key)
                    return (
                      <td key={section.key} className="px-3 py-2 text-right text-gray-800 dark:text-gray-200">
                        {_formatScore(value?.score)}
                      </td>
                    )
                  })}
                  <td className={`px-3 py-2 text-right font-bold ${row.total >= maxScore ? 'text-red-600 dark:text-red-400' : 'text-gray-800 dark:text-gray-200'}`}>
                    {_formatScore(row.total)}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-800 dark:text-gray-200 whitespace-nowrap">
                    {row.correctCount}/{row.totalQuestions || totalQuestions}
                    <span className="block text-xs text-gray-500 dark:text-gray-400">{row.accuracy.toFixed(1)}%</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <BenchmarkNote modelNames={sortedData.map(row => row.model)} />
    </div>
  )
}
