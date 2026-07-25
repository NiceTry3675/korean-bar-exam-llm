/**
 * @file ScoreBarChart.jsx
 * @brief 모델별 점수 가로 막대 차트 컴포넌트
 */

import { useState, useEffect } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Rectangle,
  CartesianGrid,
  LabelList
} from 'recharts'
import { useTranslation } from 'react-i18next'
import { getModelColor, getShortModelName, CHART_COLORS } from '@/utils/colorUtils'
import { useTheme } from '@/hooks/useTheme'
import { useExportImage, README_EXPORT_WIDTH } from '@/hooks/useExportImage'
import { BenchmarkNote, ExportButton, ExportWatermark } from '@/components/common'
import { formatModelDisplayName, getModelFlags } from '@/utils/modelMeta'
import { getLocalizedRegistryText } from '@/utils/benchmarkRegistry'

const MAX_LINE_LENGTH = 21
const MAX_LINES = 3
const MOBILE_WRAP_THRESHOLD = 17

/**
 * @brief v2(관대 채점) 추가 점수 막대 색상
 *
 * 모델 색상과 구분되는 중립 회색이며, 각 테마의 배경에서 대비를 확보한 값이다.
 */
const LENIENT_DELTA_COLOR = { light: '#6B7280', dark: '#9CA3AF' }

/** @brief v1 막대와 v2 막대를 시각적으로 분리하는 간격(px) */
const LENIENT_SEGMENT_GAP = 2

/**
 * @brief v2 막대의 최소 렌더링 두께(px)
 *
 * 2.5점처럼 작은 차이는 간격을 빼면 사라지므로, 끝점(v2 총점)은 유지한 채
 * 최소 두께를 보장한다.
 */
const LENIENT_SEGMENT_MIN_THICKNESS = 1.5

/**
 * @brief 긴 텍스트를 중간 공백에서 줄바꿈 (모바일용)
 * @param {string} text - 분할할 텍스트
 * @param {number} threshold - 줄바꿈 적용 기준 길이
 * @return {Array<string>} 분할된 줄 배열
 */
function _wrapAtMiddle(text, threshold = MOBILE_WRAP_THRESHOLD) {
  if (text.length < threshold) return [text]

  const middle = Math.floor(text.length / 2)

  // 중간에서 가장 가까운 공백 찾기
  let leftSpace = text.lastIndexOf(' ', middle)
  let rightSpace = text.indexOf(' ', middle)

  // 유효한 공백이 없으면 줄바꿈 안 함
  if (leftSpace <= 0 && rightSpace < 0) return [text]

  // 중간에 더 가까운 공백 선택
  let breakPoint
  if (leftSpace <= 0) {
    breakPoint = rightSpace
  } else if (rightSpace < 0) {
    breakPoint = leftSpace
  } else {
    breakPoint = (middle - leftSpace <= rightSpace - middle) ? leftSpace : rightSpace
  }

  return [
    text.slice(0, breakPoint).trim(),
    text.slice(breakPoint).trim()
  ]
}

/**
 * @brief 텍스트를 최대 길이 기준으로 여러 줄로 분할 (최대 3줄)
 * @param {string} text - 분할할 텍스트
 * @param {number} maxLen - 줄당 최대 길이
 * @return {Array<string>} 분할된 줄 배열
 */
function _wrapText(text, maxLen) {
  if (text.length <= maxLen) return [text]

  const lines = []
  let remaining = text

  while (remaining.length > 0 && lines.length < MAX_LINES) {
    if (remaining.length <= maxLen || lines.length === MAX_LINES - 1) {
      lines.push(remaining)
      break
    }

    // 우선순위: 1. 콤마+공백 뒤, 2. 여는괄호 앞, 3. 일반 공백
    let breakPoint = -1

    // 1. 콤마+공백 뒤
    const commaIdx = remaining.lastIndexOf(', ', maxLen - 1)
    if (commaIdx > 0) {
      breakPoint = commaIdx + 1
    }

    // 2. 여는괄호 앞
    if (breakPoint <= 0) {
      const parenIdx = remaining.lastIndexOf(' (', maxLen - 1)
      if (parenIdx > 0) {
        breakPoint = parenIdx
      }
    }

    // 3. 일반 공백
    if (breakPoint <= 0) {
      breakPoint = remaining.lastIndexOf(' ', maxLen)
    }
    if (breakPoint <= 0) {
      breakPoint = maxLen
    }

    lines.push(remaining.slice(0, breakPoint).trim())
    remaining = remaining.slice(breakPoint).trim()
  }

  return lines
}

/**
 * @brief 빗금 패턴 SVG defs (비표준 설정 모델용)
 * @param {{ darkMode: boolean }} props
 */
function HatchPatternDefs({ darkMode }) {
  const strokeColor = darkMode ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.25)'
  const checkerLight = darkMode ? 'rgba(255,255,255,0.20)' : 'rgba(255,255,255,0.24)'
  const checkerDark = darkMode ? 'rgba(0,0,0,0.16)' : 'rgba(0,0,0,0.08)'
  const shadowRgb = darkMode ? '255,255,255' : '0,0,0'
  const shadowAlpha = darkMode ? 0.15 : 0.2
  return (
    <defs>
      <pattern
        id="hatch-nonstandard"
        patternUnits="userSpaceOnUse"
        width="6"
        height="6"
        patternTransform="rotate(45)"
      >
        <line x1="0" y1="0" x2="0" y2="6" stroke={strokeColor} strokeWidth="2" />
      </pattern>
      <pattern
        id="web-service-no-tools-checker"
        patternUnits="userSpaceOnUse"
        width="16"
        height="16"
      >
        <rect x="0" y="0" width="8" height="8" fill={checkerLight} />
        <rect x="8" y="8" width="8" height="8" fill={checkerLight} />
        <rect x="8" y="0" width="8" height="8" fill={checkerDark} />
        <rect x="0" y="8" width="8" height="8" fill={checkerDark} />
      </pattern>
      {/* noVision용 내부 그림자 그라데이션 (좌, 우, 상, 하) */}
      <linearGradient id="shadow-left" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stopColor={`rgba(${shadowRgb},${shadowAlpha})`} />
        <stop offset="100%" stopColor={`rgba(${shadowRgb},0)`} />
      </linearGradient>
      <linearGradient id="shadow-right" x1="1" y1="0" x2="0" y2="0">
        <stop offset="0%" stopColor={`rgba(${shadowRgb},${shadowAlpha})`} />
        <stop offset="100%" stopColor={`rgba(${shadowRgb},0)`} />
      </linearGradient>
      <linearGradient id="shadow-top" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={`rgba(${shadowRgb},${shadowAlpha})`} />
        <stop offset="100%" stopColor={`rgba(${shadowRgb},0)`} />
      </linearGradient>
      <linearGradient id="shadow-bottom" x1="0" y1="1" x2="0" y2="0">
        <stop offset="0%" stopColor={`rgba(${shadowRgb},${shadowAlpha})`} />
        <stop offset="100%" stopColor={`rgba(${shadowRgb},0)`} />
      </linearGradient>
    </defs>
  )
}

/**
 * @brief 모델 플래그를 반영한 막대 렌더링 헬퍼
 * @param {Object} props - Recharts shape props (x, y, width, height, payload)
 * @param {Object} options - { hoveredModel, darkMode, radius, colorOverride }
 * @return {JSX.Element} SVG <g> 또는 <Rectangle>
 */
/**
 * @brief noVision 모델용 내부 그림자 오버레이
 */
function _InnerShadowOverlay({ x, y, width, height }) {
  const depth = Math.min(8, width * 0.25)
  return (
    <>
      <rect x={x} y={y} width={depth} height={height} fill="url(#shadow-left)" />
      <rect x={x + width - depth} y={y} width={depth} height={height} fill="url(#shadow-right)" />
      <rect x={x} y={y} width={width} height={depth} fill="url(#shadow-top)" />
      <rect x={x} y={y + height - depth} width={width} height={depth} fill="url(#shadow-bottom)" />
    </>
  )
}

/**
 * @brief 도구 차단 웹 서비스 환경 모델용 체크무늬 오버레이
 */
function _WebServiceNoToolsChecker({ x, y, width, height, radius }) {
  return (
    <Rectangle
      x={x}
      y={y}
      width={width}
      height={height}
      fill="url(#web-service-no-tools-checker)"
      radius={radius}
    />
  )
}

function _renderBar(props, { hoveredModel, radius = [4, 4, 0, 0], colorOverride, modelMetadata = {}, darkMode = false }) {
  const { x, y, width, height, payload } = props
  const color = colorOverride || payload.color || getModelColor(payload.model, darkMode)
  const isHovered = hoveredModel === payload.model
  const hasHover = hoveredModel !== null
  const opacity = hasHover ? (isHovered ? 1 : 0.3) : 1

  const flags = getModelFlags(payload.model, modelMetadata)
  const transitionStyle = { transition: 'opacity 0.15s ease-in-out' }

  if (flags.noVision || flags.nonStandard || flags.webServiceNoTools) {
    return (
      <g style={transitionStyle} opacity={opacity}>
        <Rectangle x={x} y={y} width={width} height={height} fill={color} radius={radius} />
        {flags.noVision && <_InnerShadowOverlay x={x} y={y} width={width} height={height} />}
        {flags.nonStandard && (
          <Rectangle x={x} y={y} width={width} height={height} fill="url(#hatch-nonstandard)" radius={radius} />
        )}
        {flags.webServiceNoTools && (
          <_WebServiceNoToolsChecker x={x} y={y} width={width} height={height} radius={radius} />
        )}
      </g>
    )
  }

  return (
    <Rectangle
      x={x} y={y} width={width} height={height}
      fill={color} radius={radius} opacity={opacity}
      style={transitionStyle}
    />
  )
}

/**
 * @brief v2(관대 채점) 추가 점수 막대 렌더링
 *
 * v1 막대 위에 쌓이며, 막대 끝은 v2 총점 위치를 그대로 유지하고 v1과 맞닿는
 * 쪽만 줄여 두 구간을 구분한다.
 *
 * @param {Object} props - Recharts shape props
 * @param {Object} options - { hoveredModel, darkMode, isMobile }
 * @return {JSX.Element} SVG 요소
 */
function _renderLenientDeltaBar(props, { hoveredModel, darkMode, isMobile }) {
  const { x, y, width, height, payload } = props
  if (!(payload?.lenientDelta > 0)) return <g />

  const isHovered = hoveredModel === payload.model
  const hasHover = hoveredModel !== null
  const opacity = hasHover ? (isHovered ? 1 : 0.3) : 1
  const fill = darkMode ? LENIENT_DELTA_COLOR.dark : LENIENT_DELTA_COLOR.light

  // 막대가 얇을 때는 간격을 줄여 v2 구간이 사라지지 않게 한다
  const thickness = isMobile ? width : height
  const gap = Math.min(LENIENT_SEGMENT_GAP, thickness / 3)
  const drawn = Math.max(thickness - gap, Math.min(thickness, LENIENT_SEGMENT_MIN_THICKNESS))

  const segment = isMobile
    ? { x: x + (width - drawn), y, width: drawn, height, radius: [0, 4, 4, 0] }
    : { x, y, width, height: drawn, radius: [4, 4, 0, 0] }

  if (segment.width <= 0 || segment.height <= 0) return <g />

  return (
    <Rectangle
      {...segment}
      fill={fill}
      opacity={opacity}
      style={{ transition: 'opacity 0.15s ease-in-out' }}
    />
  )
}

/**
 * @brief 점수 레이블 렌더링 (v2가 다르면 "v1 → v2" 병행 표기)
 * @param {Object} props - Recharts LabelList content props
 * @param {Object} options - { rows, fill, fontSize }
 * @return {JSX.Element|null}
 */
function _renderScoreLabel({ x, y, width, index }, { rows, fill, fontSize }) {
  const row = rows[index]
  if (!row) return null

  const strictScore = row.score.toFixed(1)
  const text = row.lenientDelta > 0
    ? `${strictScore} → ${row.lenientScore.toFixed(1)}`
    : strictScore

  return (
    <text
      x={x + width / 2}
      y={y - 6}
      textAnchor="middle"
      fontSize={fontSize}
      fill={fill}
      fontWeight={500}
    >
      {text}
    </text>
  )
}

/**
 * @brief 기준선 색상 (모델 색상과 구분되는 중립 잉크)
 */
const REFERENCE_LINE_COLOR = { light: '#374151', dark: '#d1d5db' }

/**
 * @brief 점수 기준선 렌더링
 *
 * 모델 계열이 아닌 주석이므로 모델 색상을 쓰지 않고 중립 잉크로 그린다.
 *
 * @param {Object} props - { lines, language, darkMode, isMobile }
 * @return {Array<JSX.Element>} ReferenceLine 목록
 */
function _renderReferenceLines({ lines, language, darkMode, isMobile }) {
  const stroke = darkMode ? REFERENCE_LINE_COLOR.dark : REFERENCE_LINE_COLOR.light
  return lines.map(line => {
    const text = getLocalizedRegistryText(line.label, language)
    const axisProps = isMobile ? { x: line.score } : { y: line.score }
    return (
      <ReferenceLine
        key={line.id || line.score}
        {...axisProps}
        stroke={stroke}
        strokeDasharray="6 4"
        strokeWidth={1.5}
        strokeOpacity={0.75}
        ifOverflow="extendDomain"
        label={{
          value: text,
          position: isMobile ? 'top' : 'insideTopLeft',
          fill: stroke,
          fontSize: isMobile ? 10 : 12,
          fontWeight: 600
        }}
      />
    )
  })
}

/**
 * @brief 기준선 설명 범례 (export 이미지에도 포함)
 * @param {Object} props - { lines, language, darkMode }
 */
function _ReferenceLineLegend({ lines, language, darkMode }) {
  const stroke = darkMode ? REFERENCE_LINE_COLOR.dark : REFERENCE_LINE_COLOR.light
  return (
    <div className="flex flex-col gap-0.5 mb-3 text-xs text-gray-500 dark:text-gray-400">
      {lines.map(line => (
        <div key={line.id || line.score} className="flex items-center gap-1.5">
          <svg width="22" height="8" aria-hidden="true" className="shrink-0">
            <line
              x1="0" y1="4" x2="22" y2="4"
              stroke={stroke}
              strokeWidth="1.5"
              strokeDasharray="6 4"
            />
          </svg>
          <span>
            {getLocalizedRegistryText(line.label, language)}
            {line.description ? ` — ${getLocalizedRegistryText(line.description, language)}` : ''}
          </span>
        </div>
      ))}
    </div>
  )
}

/**
 * @brief v2 추가 점수 막대 범례 (export 이미지에도 포함)
 * @param {Object} props - { t, darkMode }
 */
function _LenientLegend({ t, darkMode }) {
  return (
    <div className="flex items-center gap-1.5 mb-3 text-xs text-gray-500 dark:text-gray-400">
      <span
        className="inline-block w-3 h-3 rounded-sm shrink-0"
        style={{ backgroundColor: darkMode ? LENIENT_DELTA_COLOR.dark : LENIENT_DELTA_COLOR.light }}
      />
      <span>{t('charts.lenientLegend')}</span>
    </div>
  )
}

/**
 * @brief 커스텀 Y축 틱 컴포넌트 생성 함수
 * @param {string} hoveredModel - 현재 호버된 모델명
 * @param {function} onModelHover - 모델 호버 콜백
 * @param {boolean} darkMode - 다크모드 여부
 * @param {boolean} isMobile - 모바일 여부
 * @return {function} Recharts tick 렌더 함수
 */
function createCustomYAxisTick(hoveredModel, onModelHover, darkMode, isMobile) {
  const defaultColor = darkMode ? '#d1d5db' : '#374151'
  const hoverColor = darkMode ? '#60a5fa' : '#1d4ed8'

  return function CustomYAxisTick({ x, y, payload }) {
    // 모바일에서는 짧은 모델명 사용 + 17자 이상 시 중간 공백에서 줄바꿈
    const displayName = isMobile ? getShortModelName(payload.value) : formatModelDisplayName(payload.value)
    const lines = isMobile ? _wrapAtMiddle(displayName) : _wrapText(displayName, MAX_LINE_LENGTH)
    const lineHeight = 14
    const startY = -((lines.length - 1) * lineHeight) / 2 + 3
    const isHovered = hoveredModel === payload.value
    const hasHover = hoveredModel !== null

    return (
      <g
        style={{ cursor: 'pointer' }}
        onMouseEnter={() => onModelHover?.(payload.value)}
        onMouseLeave={() => onModelHover?.(null)}
      >
        {/* 투명한 호버 영역 (텍스트보다 넓게) */}
        <rect
          x={x - 145}
          y={y - 20}
          width={150}
          height={40}
          fill="transparent"
        />
        <text
          x={x}
          y={y}
          textAnchor="end"
          fontSize={12}
          fill={isHovered ? hoverColor : defaultColor}
          fontWeight={isHovered ? 600 : 400}
          style={{
            opacity: hasHover ? (isHovered ? 1 : 0.5) : 1,
            transition: 'opacity 0.15s ease-in-out'
          }}
        >
          {lines.map((line, i) => (
            <tspan key={i} x={x} dy={i === 0 ? startY : lineHeight}>
              {line}
            </tspan>
          ))}
        </text>
      </g>
    )
  }
}

/**
 * @brief 커스텀 툴팁 컴포넌트
 * @param {Object} props - { active, payload, t }
 */
function CustomTooltip({ active, payload, t }) {
  if (!active || !payload?.length) return null

  const data = payload[0].payload
  const hasLenientDelta = data.lenientDelta > 0
  const displayScore = Number.isInteger(data.score)
    ? data.score
    : parseFloat(data.score.toFixed(3))
  const accuracy = data.totalPoints > 0
    ? ((data.score / data.totalPoints) * 100).toFixed(1)
    : 0

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-3">
      <p className="font-semibold text-gray-800 dark:text-gray-200 mb-1">{formatModelDisplayName(data.model)}</p>
      <p className="text-sm text-gray-600 dark:text-gray-400">
        {t('tooltip.score')}: <span className="font-medium">{displayScore}</span> / {data.totalPoints}{t('tooltip.points')}
      </p>
      {hasLenientDelta && (
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {t('tooltip.lenientScore')}: <span className="font-medium">{data.lenientScore.toFixed(1)}</span> / {data.totalPoints}{t('tooltip.points')}
          <span className="ml-1">(+{data.lenientDelta.toFixed(1)})</span>
        </p>
      )}
      <p className="text-sm text-gray-600 dark:text-gray-400">
        {t('tooltip.accuracy')}: <span className="font-medium">{accuracy}%</span>
      </p>
      {data.correctCount !== undefined && (
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {t('tooltip.correctCount')}: <span className="font-medium">{data.correctCount}</span> / {data.totalQuestions}{t('tooltip.totalQuestions')}
        </p>
      )}
    </div>
  )
}

/**
 * @brief 점수 막대 차트 컴포넌트
 * @param {Object} props - { data, maxScore, title, height, hoveredModel, onModelHover }
 * @param {Array} props.data - [{ model, score, totalPoints, correctCount?, totalQuestions?,
 *                                lenientScore?, lenientDelta? }] 점수 내림차순 정렬
 * @param {number} props.maxScore - 차트 Y축 최대값 (기본: 데이터에서 자동 계산)
 * @param {string} props.title - 차트 제목
 * @param {number} props.height - 차트 높이 (기본: 400)
 * @param {string} props.hoveredModel - 현재 호버된 모델명
 * @param {function} props.onModelHover - 모델 호버 콜백
 * @param {string} props.exportKey - export 이미지 대상 키
 * @param {Array} props.referenceLines - [{ id, score, label, description }] 기준선
 */
export default function ScoreBarChart({
  data,
  maxScore,
  title,
  subtitle,
  height = 400,
  hoveredModel,
  onModelHover,
  modelMetadata = {},
  exportKey = 'overview-score-chart',
  referenceLines = []
}) {
  const { t, i18n } = useTranslation()
  const { isDark: darkMode } = useTheme()
  const { ref, exportImage, isExporting } = useExportImage({ exportWidth: README_EXPORT_WIDTH })
  const [showLabels, setShowLabels] = useState(true)

  // 모바일 감지
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768)
  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  if (!data?.length) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 dark:text-gray-400">
        {t('common.noData')}
      </div>
    )
  }

  // 최대 점수 계산 (전달되지 않은 경우)
  const computedMaxScore = maxScore ?? Math.max(...data.map(d => d.totalPoints || d.score))

  // 데스크톱 세로 막대는 높은 점수를 오른쪽에 두므로 렌더링 순서만 뒤집는다.
  // 모바일 가로 막대는 높은 점수가 위에 오도록 전달된 순서를 유지한다.
  const renderData = isMobile ? data : [...data].reverse()
  const hasLenientDelta = data.some(item => item.lenientDelta > 0)

  // 다크모드용 색상
  const cursorColor = darkMode ? 'rgba(55, 65, 81, 0.5)' : '#f3f4f6'
  const axisColor = darkMode ? '#4b5563' : '#e5e7eb'
  const tickColor = darkMode ? '#9ca3af' : '#6b7280'
  const xTickColor = darkMode ? '#d1d5db' : '#374151'

  // 모바일: 가로 막대 차트
  if (isMobile) {
    const dynamicHeight = Math.max(height, data.length * 40 + 60)

    return (
      <div ref={ref} className="w-full">
        <div className="flex items-start justify-between mb-4">
          <div>
            {title && (
              <h3 className="text-lg font-semibold text-gray-800 dark:text-gray-200">{title}</h3>
            )}
            {subtitle && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{subtitle}</p>
            )}
          </div>
          <ExportButton
            onClick={() => exportImage(`${subtitle || t('common.all')}.png`)}
            exportKey={exportKey}
          />
        </div>
        {hasLenientDelta && <_LenientLegend t={t} darkMode={darkMode} />}
      {referenceLines.length > 0 && (
        <_ReferenceLineLegend lines={referenceLines} language={i18n.language} darkMode={darkMode} />
      )}
        {referenceLines.length > 0 && (
          <_ReferenceLineLegend lines={referenceLines} language={i18n.language} darkMode={darkMode} />
        )}
        <ResponsiveContainer width="100%" height={dynamicHeight}>
          <BarChart
            key={renderData.map(d => d.model).join(',')}
            data={renderData}
            layout="vertical"
            margin={{ top: 10, right: 30, left: 5, bottom: 10 }}
            onMouseMove={(state) => {
              if (state?.activeTooltipIndex !== undefined) {
                const model = renderData[state.activeTooltipIndex]?.model
                if (model && model !== hoveredModel) {
                  onModelHover?.(model)
                }
              }
            }}
            onMouseLeave={() => onModelHover?.(null)}
          >
            <XAxis
              type="number"
              domain={[0, computedMaxScore]}
              tickLine={false}
              axisLine={{ stroke: axisColor }}
              tick={{ fill: tickColor }}
            />
            <YAxis
              type="category"
              dataKey="model"
              tickLine={false}
              axisLine={false}
              width={100}
              tick={createCustomYAxisTick(hoveredModel, onModelHover, darkMode, true)}
            />
            <Tooltip content={<CustomTooltip t={t} />} cursor={{ fill: cursorColor }} />
            <ReferenceLine
              x={computedMaxScore}
              stroke={CHART_COLORS.perfect}
              strokeDasharray="3 3"
              strokeWidth={2}
            />
            {_renderReferenceLines({ lines: referenceLines, language: i18n.language, darkMode, isMobile: true })}
            <HatchPatternDefs darkMode={darkMode} />
            <Bar
              dataKey="score"
              stackId="score"
              barSize={24}
              shape={(props) => _renderBar(props, {
                hoveredModel,
                radius: props.payload?.lenientDelta > 0 ? [0, 0, 0, 0] : [0, 4, 4, 0],
                modelMetadata,
                darkMode
              })}
            />
            <Bar
              dataKey="lenientDelta"
              stackId="score"
              barSize={24}
              isAnimationActive={false}
              shape={(props) => _renderLenientDeltaBar(props, { hoveredModel, darkMode, isMobile: true })}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }

  // 데스크톱: 세로 막대 차트 (TokenUsageChart 스타일)
  // 모델 수에 따른 레이블 글자 크기 (적을수록 크게)
  const labelFontSize = data.length <= 15 ? 12 : 10
  const desktopChartMargin = {
    top: 30,
    right: 30,
    left: 20,
    bottom: isExporting ? 20 : 100
  }
  const desktopXAxisHeight = isExporting ? 135 : 100

  return (
    <div ref={ref} className="w-full">
      <div className="flex items-start justify-between mb-2">
        <div>
          {title && (
            <h3 className="text-xl font-semibold text-gray-800 dark:text-gray-200">{title}</h3>
          )}
          {subtitle && (
            <p className="text-base text-gray-500 dark:text-gray-400 mt-1">{subtitle}</p>
          )}
        </div>
        <div className="flex items-start gap-2">
          <ExportWatermark />
          <ExportButton
            onClick={() => exportImage(`${subtitle || t('common.all')}.png`)}
            exportKey={exportKey}
          />
        </div>
      </div>
      {/* 레이블 표시 토글 */}
      <div className="flex items-center gap-2 mb-4 flex-wrap" data-export-hide="true">
        <button
          onClick={() => setShowLabels(!showLabels)}
          className={`px-2 py-1 text-xs rounded transition-colors ${
            showLabels
              ? 'bg-blue-500 text-white'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
          }`}
        >
          {t('charts.showScores')}
        </button>
      </div>
      {hasLenientDelta && <_LenientLegend t={t} darkMode={darkMode} />}
      {referenceLines.length > 0 && (
        <_ReferenceLineLegend lines={referenceLines} language={i18n.language} darkMode={darkMode} />
      )}
      <ResponsiveContainer width="100%" height={600}>
        <BarChart
            key={renderData.map(d => d.model).join(',')}
            data={renderData}
            margin={desktopChartMargin}
            onMouseMove={(state) => {
              if (state?.activeTooltipIndex !== undefined) {
                const model = renderData[state.activeTooltipIndex]?.model
                if (model && model !== hoveredModel) {
                  onModelHover?.(model)
                }
              }
            }}
            onMouseLeave={() => onModelHover?.(null)}
          >
            <XAxis
              dataKey="model"
              tickFormatter={(value) => formatModelDisplayName(value)}
              angle={-45}
              textAnchor="end"
              interval={0}
              tick={{ fontSize: 11, fill: xTickColor }}
              tickLine={false}
              axisLine={{ stroke: axisColor }}
              height={desktopXAxisHeight}
            />
            <YAxis
              domain={[0, computedMaxScore]}
              tickLine={false}
              axisLine={{ stroke: axisColor }}
              tick={{ fill: tickColor }}
            />
            <CartesianGrid
              horizontal={true}
              vertical={false}
              stroke={axisColor}
              strokeDasharray="3 3"
            />
            {_renderReferenceLines({ lines: referenceLines, language: i18n.language, darkMode, isMobile: false })}
            <Tooltip content={<CustomTooltip t={t} />} cursor={{ fill: cursorColor }} />
            <HatchPatternDefs darkMode={darkMode} />
            <Bar
              dataKey="score"
              stackId="score"
              isAnimationActive={false}
              shape={(props) => _renderBar(props, {
                hoveredModel,
                radius: props.payload?.lenientDelta > 0 ? [0, 0, 0, 0] : [4, 4, 0, 0],
                modelMetadata,
                darkMode
              })}
            />
            <Bar
              dataKey="lenientDelta"
              stackId="score"
              isAnimationActive={false}
              shape={(props) => _renderLenientDeltaBar(props, { hoveredModel, darkMode, isMobile: false })}
            >
              {showLabels && (
                <LabelList
                  dataKey="lenientDelta"
                  position="top"
                  content={(props) => _renderScoreLabel(props, {
                    rows: renderData,
                    fill: xTickColor,
                    fontSize: labelFontSize
                  })}
                />
              )}
            </Bar>
          </BarChart>
      </ResponsiveContainer>
      <BenchmarkNote modelNames={data.map(item => item.model)} modelMetadata={modelMetadata} />
    </div>
  )
}
