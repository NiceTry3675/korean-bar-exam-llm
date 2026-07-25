/**
 * @file App.jsx
 * @brief 제15회 변호사시험 LLM 풀이 대시보드 - 메인 App 컴포넌트
 */

import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { DataProvider, useData } from '@/hooks/useData'
import { ThemeProvider } from '@/hooks/useTheme'
import { useSidebar } from '@/hooks/useSidebar'
import { Header, Sidebar, Footer, BottomNav } from '@/components/layout'
import {
  ScoreBarChart,
  CostScatterChart,
  TokenUsageChart,
  QuestionHeatmap,
  ModelCompareChart,
  ChoiceSelectionChart
} from '@/components/charts'
import { BenchmarkScoreTable, CostTable } from '@/components/tables'
import { ModelSelectDropdown } from '@/components/common'
import { getCostData } from '@/utils/costTransform'
import { transformToHeatmapData } from '@/utils/heatmapTransform'
import { transformToChoiceData } from '@/utils/choiceTransform'
import { VENDORS, groupModelsByVendor, getSortedVendors, getDefaultSelectedModels } from '@/utils/colorUtils'
import { getDashboardQueryState } from '@/utils/urlState'
import { formatModelDisplayName, getEffortTier } from '@/utils/modelMeta'
import { translateSubject } from '@/utils/subjectLabels'
import { DEFAULT_BENCHMARK_ID, getBenchmarkSections, getLocalizedRegistryText } from '@/utils/benchmarkRegistry'
import {
  calculateAllBenchmarkScores,
  getBenchmarkMaxScore,
  transformBenchmarkRadarData
} from '@/utils/benchmarkTransform'

/**
 * @brief 탭 정의
 */
const TAB_KEYS = ['overview', 'subjects', 'compare', 'cost']

const INITIAL_QUERY_STATE = getDashboardQueryState()

/**
 * @brief 대시보드 메인 컴포넌트
 */
function Dashboard({ benchmarkMode = 'default', onBenchmarkModeChange, onBenchmarkChange }) {
  const { t, i18n } = useTranslation()
  const {
    data,
    tokenUsage,
    modelMetadata,
    loading,
    error,
    models,
    subjects,
    sections,
    dataMode,
    dataBenchmarkId,
    benchmark,
    navigableBenchmarks,
    resultsPending
  } = useData()
  const sidebar = useSidebar()

  const [filters, setFilters] = useState({
    subjects: INITIAL_QUERY_STATE.subjects,
    models: [],
    sortBy: 'score_desc'
  })
  const [activeTab, setActiveTab] = useState(INITIAL_QUERY_STATE.tab)
  const [selectedSubject, setSelectedSubject] = useState(INITIAL_QUERY_STATE.selectedSubject)
  const [selectedSection, setSelectedSection] = useState(INITIAL_QUERY_STATE.selectedSection)
  const [compareModels, setCompareModels] = useState([])
  const [hoveredModel, setHoveredModel] = useState(null)
  const [isModelSelectionTouched, setIsModelSelectionTouched] = useState(false)
  const subjectSelectRef = useRef(null)
  const sectionSelectRef = useRef(null)
  const mainRef = useRef(null)
  const scrollPositions = useRef({})
  const [isDefaultModelSelectionReady, setIsDefaultModelSelectionReady] = useState(false)
  const [headerVisible, setHeaderVisible] = useState(false)
  const [scrolledPastHeader, setScrolledPastHeader] = useState(false)
  const headerTimeoutRef = useRef(null)
  const originalHeaderRef = useRef(null)
  const previousBenchmarkModeRef = useRef(benchmarkMode)
  const previousBenchmarkIdRef = useRef(null)

  useEffect(() => {
    if (previousBenchmarkModeRef.current === benchmarkMode) return
    previousBenchmarkModeRef.current = benchmarkMode
    setIsDefaultModelSelectionReady(false)
    setSelectedSubject('')
    setSelectedSection('')
  }, [benchmarkMode])

  useEffect(() => {
    const currentBenchmarkId = benchmark?.id
    if (!currentBenchmarkId) return
    if (previousBenchmarkIdRef.current === null) {
      previousBenchmarkIdRef.current = currentBenchmarkId
      return
    }
    if (previousBenchmarkIdRef.current === currentBenchmarkId) return
    previousBenchmarkIdRef.current = currentBenchmarkId

    setIsDefaultModelSelectionReady(false)
    setSelectedSubject('')
    setSelectedSection('')
    setFilters(previous => ({ ...previous, subjects: [], models: [] }))
    setCompareModels([])
    setIsModelSelectionTouched(false)
  }, [benchmark?.id])

  const _areArraysEqual = useCallback((a = [], b = []) => {
    if (a.length !== b.length) return false
    return a.every((value, index) => value === b[index])
  }, [])

  const handleFilterChange = useCallback((nextFilters) => {
    if (!_areArraysEqual(filters.models, nextFilters.models)) {
      setIsModelSelectionTouched(true)
    }
    setFilters(nextFilters)
  }, [_areArraysEqual, filters.models])

  // 전체 모델 점수 계산 (과목 필터 적용)
  const overallScores = useMemo(() => {
    if (!data?.length || !models?.length || !benchmark) return []
    return calculateAllBenchmarkScores(data, models, benchmark, filters.subjects)
  }, [data, models, filters.subjects, benchmark])

  // 동적 만점 계산
  const maxScore = useMemo(() => {
    return getBenchmarkMaxScore(benchmark, filters.subjects)
  }, [benchmark, filters.subjects])

  /**
   * @brief 데이터 로드 완료 후 기본 모델 필터 설정
   * - 모델 필터를 건드리지 않은 상태에서는 각 모드의 기본 표시 규칙 적용
   * - 모델 필터를 건드린 뒤에는 새 모드에 있는 선택 모델 유지
   * - 유지되는 모델이 없으면 각 모드의 기본 표시 규칙 적용
   * - 비교 탭 선택 모델도 새 모드에 있는 모델만 유지
   */
  useEffect(() => {
    if (loading || isDefaultModelSelectionReady) return
    if (dataMode !== benchmarkMode || dataBenchmarkId !== benchmark?.id) return

    if (!data?.length || !models?.length) {
      setFilters(prev => ({ ...prev, models: [] }))
      setCompareModels([])
      setIsDefaultModelSelectionReady(true)
      return
    }

    const allScores = calculateAllBenchmarkScores(data, models, benchmark, [])
    const availableModels = new Set(models)
    // URL의 models 파라미터가 기본 표시 규칙보다 우선한다. 기본 규칙은 개발사당
    // 상위 몇 개만 고르므로, README 이미지처럼 전체를 보여야 할 때 ?models=all을 쓴다.
    const requestedModels = INITIAL_QUERY_STATE.models
    const urlModels = requestedModels.includes('all')
      ? models
      : requestedModels.filter(model => availableModels.has(model))
    const defaultModels = urlModels.length > 0
      ? urlModels
      : getDefaultSelectedModels(models, allScores)

    setFilters(prev => {
      const retainedModels = prev.models.filter(model => availableModels.has(model))
      if (!isModelSelectionTouched) {
        return {
          ...prev,
          models: defaultModels
        }
      }
      return {
        ...prev,
        models: retainedModels.length > 0 ? retainedModels : defaultModels
      }
    })
    setCompareModels(prev => prev.filter(model => availableModels.has(model)))
    setIsDefaultModelSelectionReady(true)
  }, [loading, data, models, dataMode, dataBenchmarkId, benchmark, benchmarkMode, isDefaultModelSelectionReady, isModelSelectionTouched])

  // 모델 필터만 적용 (정렬 전 단계 — 비용 데이터 계산의 기준)
  const modelFilteredScores = useMemo(() => {
    if (filters.models.length === 0) return overallScores
    return overallScores.filter(s => filters.models.includes(s.model))
  }, [overallScores, filters.models])

  // 비용 데이터 (모델 필터링 + 과목 필터링 적용, 정렬과 무관)
  const costBasis = useMemo(() => {
    if (!data?.length || !modelFilteredScores?.length) return []
    return getCostData(data, modelFilteredScores, tokenUsage || {}, filters.subjects, maxScore)
  }, [data, modelFilteredScores, tokenUsage, filters.subjects, maxScore])

  const costByModel = useMemo(
    () => new Map(costBasis.map(item => [item.model, item])),
    [costBasis]
  )

  // 필터 및 정렬 적용
  const filteredScores = useMemo(() => {
    const result = [...modelFilteredScores]

    // 정렬
    switch (filters.sortBy) {
      case 'score_asc':
        result.sort((a, b) => (a.totalLenient - b.totalLenient) || (a.total - b.total))
        break
      case 'cost_asc':
        // 비용 정보가 없는 모델은 뒤로 보낸다
        result.sort((a, b) => {
          const costA = costByModel.get(a.model)?.totalCost || 0
          const costB = costByModel.get(b.model)?.totalCost || 0
          if (costA > 0 !== costB > 0) return costA > 0 ? -1 : 1
          if (costA !== costB) return costA - costB
          return a.model.localeCompare(b.model)
        })
        break
      case 'efficiency_desc':
        result.sort((a, b) => {
          const efficiencyA = costByModel.get(a.model)?.efficiency || 0
          const efficiencyB = costByModel.get(b.model)?.efficiency || 0
          if (efficiencyA !== efficiencyB) return efficiencyB - efficiencyA
          return b.total - a.total
        })
        break
      case 'name_asc':
        result.sort((a, b) => a.model.localeCompare(b.model))
        break
      case 'name_desc':
        result.sort((a, b) => b.model.localeCompare(a.model))
        break
      case 'vendor':
        // VENDORS 배열 순서대로, 같은 개발사 내에서는 이름 내림차순
        result.sort((a, b) => {
          const vendorA = VENDORS.findIndex(v => v.pattern?.test(a.model))
          const vendorB = VENDORS.findIndex(v => v.pattern?.test(b.model))
          const idxA = vendorA === -1 ? VENDORS.length : vendorA
          const idxB = vendorB === -1 ? VENDORS.length : vendorB
          if (idxA !== idxB) return idxA - idxB
          return b.model.localeCompare(a.model) // 같은 개발사 내에서는 이름 내림차순
        })
        break
      case 'score_desc':
      default:
        // v2(관대 채점) 점수를 우선 기준으로 삼고, 동점이면 공식 점수로 가른다
        result.sort((a, b) => (b.totalLenient - a.totalLenient) || (b.total - a.total))
    }

    return result
  }, [modelFilteredScores, filters.sortBy, costByModel])

  // 보기 모드에 따른 점수 차트 데이터
  const scoreChartData = useMemo(() => {
    if (!data?.length || !filteredScores?.length) return []

    return filteredScores.map(s => ({
      model: s.model,
      score: s.total,
      lenientScore: s.totalLenient,
      lenientDelta: Math.max(0, (s.totalLenient ?? s.total) - s.total),
      totalPoints: maxScore,
      correctCount: s.correctCount,
      totalQuestions: s.totalQuestions
    }))
  }, [data, filteredScores, maxScore])

  // 추론 수준별 점수 차트 데이터 (고추론: max·high / 저추론: none·low)
  const scoreChartDataByEffort = useMemo(() => ({
    high: scoreChartData.filter(item => getEffortTier(item.model) !== 'low'),
    low: scoreChartData.filter(item => getEffortTier(item.model) === 'low')
  }), [scoreChartData])

  // 양쪽 등급에 모델이 있을 때만 차트를 나눈다 (접미사가 없는 벤치마크는 단일 차트)
  const scoreChartSplitByEffort = (
    scoreChartDataByEffort.high.length > 0 && scoreChartDataByEffort.low.length > 0
  )

  const scoreChartSubtitle = filters.subjects.length === 0
    ? null
    : filters.subjects.map(subject => translateSubject(subject, t)).join(', ')

  // 정렬 순서를 반영한 비용 데이터
  const costData = useMemo(
    () => filteredScores.map(s => costByModel.get(s.model)).filter(Boolean),
    [filteredScores, costByModel]
  )

  // 히트맵 데이터
  const heatmapData = useMemo(() => {
    if (!data?.length || !selectedSubject || !selectedSection) return {}
    return transformToHeatmapData(data, selectedSubject, selectedSection)
  }, [data, selectedSubject, selectedSection])

  // 레이더 차트 데이터
  const radarData = useMemo(() => {
    if (!overallScores?.length || !compareModels?.length) return []
    return transformBenchmarkRadarData(overallScores, compareModels, benchmark).data
      .map(row => ({ ...row, subject: translateSubject(row.subject, t) }))
  }, [overallScores, compareModels, t, benchmark])

  const radarDimensions = useMemo(() => {
    return transformBenchmarkRadarData([], [], benchmark).dimensions
      .map(dimension => ({ ...dimension, label: translateSubject(dimension.label, t) }))
  }, [benchmark, t])

  // 표시할 모델 목록 (필터 및 정렬 적용 - filteredScores 순서 따름)
  const displayModels = useMemo(() => {
    return filteredScores.map(s => s.model)
  }, [filteredScores])

  // 선지 선택률 데이터
  const choiceData = useMemo(() => {
    if (!heatmapData || !Object.keys(heatmapData).length || !displayModels?.length) return []
    return transformToChoiceData(heatmapData, displayModels)
  }, [heatmapData, displayModels])

  // 과목 선택 시 섹션 목록
  const availableSections = useMemo(() => {
    if (!selectedSubject) return []

    // 섹션이 과목 자기 자신뿐이면 섹션 선택이 의미 없으므로 숨긴다
    const secs = sections[selectedSubject] || []
    if (secs.length === 1 && secs[0] === selectedSubject) {
      return []
    }

    return secs
  }, [selectedSubject, sections])

  /**
   * @brief PC 헤더 스크롤 감지 (원본 헤더가 화면 밖으로 나갔는지)
   */
  useEffect(() => {
    const handleScroll = () => {
      if (originalHeaderRef.current) {
        const rect = originalHeaderRef.current.getBoundingClientRect()
        setScrolledPastHeader(rect.bottom < 0)
      }
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  /**
   * @brief PC 헤더 호버 핸들러
   */
  const handleHeaderTriggerEnter = useCallback(() => {
    if (!scrolledPastHeader) return
    if (headerTimeoutRef.current) {
      clearTimeout(headerTimeoutRef.current)
      headerTimeoutRef.current = null
    }
    setHeaderVisible(true)
  }, [scrolledPastHeader])

  const handleHeaderTriggerLeave = useCallback(() => {
    headerTimeoutRef.current = setTimeout(() => {
      setHeaderVisible(false)
    }, 300)
  }, [])

  /**
   * @brief 탭 전환 시 스크롤 위치 저장/복원
   * @param {string} newTab - 새 탭 키
   */
  const handleTabChange = useCallback((newTab) => {
    // 현재 탭 스크롤 위치 저장
    if (mainRef.current) {
      scrollPositions.current[activeTab] = mainRef.current.scrollTop
    }

    setActiveTab(newTab)

    // 새 탭 스크롤 위치 복원 (다음 렌더 사이클에서)
    requestAnimationFrame(() => {
      if (mainRef.current) {
        mainRef.current.scrollTop = scrollPositions.current[newTab] || 0
      }
    })
  }, [activeTab])

  /**
   * @brief 과목 선택 시 자동 섹션 설정
   */
  const handleSubjectChange = useCallback((newSubject) => {
    setSelectedSubject(newSubject)

    if (!newSubject) {
      setSelectedSection('')
      return
    }

    // 섹션이 과목 자기 자신인 경우 자동 설정
    if (sections[newSubject]?.length === 1 && sections[newSubject][0] === newSubject) {
      setSelectedSection(newSubject)
    }
    // 그 외에는 첫 번째 섹션 자동 선택
    else {
      const secs = sections[newSubject] || []
      setSelectedSection(secs[0] || '')
    }
  }, [sections])

  // 데이터 로드 완료 후 기본 과목 선택
  useEffect(() => {
    if (!loading && subjects.length > 0 && !selectedSubject) {
      handleSubjectChange(subjects[0])
    }
  }, [loading, subjects, selectedSubject, handleSubjectChange])

  // 과목 드롭다운 휠 스크롤 이벤트 등록 (passive: false로 스크롤 방지)
  useEffect(() => {
    const el = subjectSelectRef.current
    if (!el) return

    const handler = (e) => {
      e.preventDefault()
      e.stopPropagation()
      if (!subjects.length) return

      const currentIndex = subjects.indexOf(selectedSubject)
      let newIndex

      if (e.deltaY > 0) {
        newIndex = currentIndex >= subjects.length - 1 ? 0 : currentIndex + 1
      } else {
        newIndex = currentIndex <= 0 ? subjects.length - 1 : currentIndex - 1
      }

      handleSubjectChange(subjects[newIndex])
    }

    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [subjects, selectedSubject, handleSubjectChange, activeTab])

  // 섹션 드롭다운 휠 스크롤 이벤트 등록
  useEffect(() => {
    const el = sectionSelectRef.current
    if (!el) return

    const handler = (e) => {
      e.preventDefault()
      e.stopPropagation()
      if (!availableSections.length) return

      const currentIndex = availableSections.indexOf(selectedSection)
      let newIndex

      if (e.deltaY > 0) {
        newIndex = currentIndex >= availableSections.length - 1 ? 0 : currentIndex + 1
      } else {
        newIndex = currentIndex <= 0 ? availableSections.length - 1 : currentIndex - 1
      }

      setSelectedSection(availableSections[newIndex])
    }

    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [availableSections, selectedSection, activeTab])

  if (loading) {
    return (
      <div
        className="flex items-center justify-center h-screen bg-gray-100 dark:bg-gray-900"
        data-benchmark-mode={benchmarkMode}
      >
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent mx-auto mb-4" />
          <p className="text-gray-600 dark:text-gray-400">{t('common.loading')}</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-100 dark:bg-gray-900">
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 max-w-md">
          <h2 className="text-xl font-bold text-red-600 dark:text-red-400 mb-2">{t('common.error')}</h2>
          <p className="text-gray-600 dark:text-gray-400">{error}</p>
        </div>
      </div>
    )
  }

  if (resultsPending) {
    const configuredSections = getBenchmarkSections(benchmark)
    return (
      <div
        className="min-h-screen bg-gray-100 dark:bg-gray-900 flex flex-col"
        data-benchmark-id={benchmark.id}
        data-benchmark-mode={benchmarkMode}
        data-results-pending="true"
        data-dashboard-ready="true"
      >
        <Header
          onMenuToggle={sidebar.toggle}
          mode={benchmarkMode}
          onModeChange={onBenchmarkModeChange}
          benchmark={benchmark}
          benchmarks={navigableBenchmarks}
          onBenchmarkChange={onBenchmarkChange}
        />
        <main className="flex-1 container mx-auto px-4 py-10 md:py-16">
          <section className="max-w-4xl mx-auto bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 p-6 md:p-10 shadow-sm">
            <div className="inline-flex items-center rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300 px-3 py-1 text-sm font-medium mb-5">
              {t('benchmark.resultsPendingBadge')}
            </div>
            <h2 className="text-2xl md:text-3xl font-bold text-gray-900 dark:text-gray-100 mb-3">
              {t('benchmark.resultsPendingTitle')}
            </h2>
            <p className="text-gray-600 dark:text-gray-400 mb-8">
              {t('benchmark.resultsPendingDescription')}
            </p>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
              <div className="rounded-xl bg-gray-50 dark:bg-gray-700/60 p-4">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('benchmark.officialMaximum')}</p>
                <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">{benchmark.scoring?.maxScore || 0}</p>
              </div>
              <div className="rounded-xl bg-gray-50 dark:bg-gray-700/60 p-4">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('benchmark.totalQuestions')}</p>
                <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">{benchmark.scoring?.totalQuestions || 0}</p>
              </div>
              <div className="rounded-xl bg-gray-50 dark:bg-gray-700/60 p-4">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('benchmark.pointsPerQuestion')}</p>
                <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">{benchmark.scoring?.pointsPerQuestion || 0}</p>
              </div>
              <div className="rounded-xl bg-gray-50 dark:bg-gray-700/60 p-4">
                <p className="text-xs text-gray-500 dark:text-gray-400">{t('benchmark.executionMode')}</p>
                <p className="text-lg font-bold text-gray-900 dark:text-gray-100">
                  {getLocalizedRegistryText(benchmark.modes?.[benchmarkMode]?.label, i18n.language) || benchmarkMode}
                </p>
              </div>
            </div>

            <div className="grid md:grid-cols-3 gap-3">
              {configuredSections.map(section => (
                <div key={section.sheet || `${section.subject}:${section.section}`} className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
                  <h3 className="font-semibold text-gray-900 dark:text-gray-100">{translateSubject(section.subject, t)}</h3>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    {t('benchmark.sectionSummary', { questions: section.questionCount, score: section.maxScore })}
                  </p>
                </div>
              ))}
            </div>
          </section>
        </main>
        <Footer />
      </div>
    )
  }

  return (
      <div
      className="min-h-screen bg-gray-100 dark:bg-gray-900 flex flex-col"
      data-benchmark-id={benchmark?.id}
      data-benchmark-mode={benchmarkMode}
      data-dashboard-ready={!loading && !error && isDefaultModelSelectionReady ? 'true' : 'false'}
    >
      {/* PC 헤더 호버 트리거 영역 (스크롤 시에만 활성화) */}
      {scrolledPastHeader && (
        <div
          className="hidden md:block fixed top-0 left-0 right-0 h-3 z-50"
          onMouseEnter={handleHeaderTriggerEnter}
        />
      )}
      {/* PC 헤더 (스크롤 후 호버 시 표시) */}
      <div
        className={`hidden md:block fixed top-0 left-0 right-0 z-40 transition-transform duration-300 ${
          headerVisible && scrolledPastHeader ? 'translate-y-0' : '-translate-y-full'
        }`}
        onMouseEnter={handleHeaderTriggerEnter}
        onMouseLeave={handleHeaderTriggerLeave}
      >
        <Header
          onMenuToggle={sidebar.toggle}
          mode={benchmarkMode}
          onModeChange={onBenchmarkModeChange}
          benchmark={benchmark}
          benchmarks={navigableBenchmarks}
          onBenchmarkChange={onBenchmarkChange}
        />
      </div>
      {/* 원본 헤더 (항상 표시) */}
      <div ref={originalHeaderRef}>
        <Header
          onMenuToggle={sidebar.toggle}
          mode={benchmarkMode}
          onModeChange={onBenchmarkModeChange}
          benchmark={benchmark}
          benchmarks={navigableBenchmarks}
          onBenchmarkChange={onBenchmarkChange}
        />
      </div>
      <div className="flex flex-1">
        <Sidebar
          filters={filters}
          onFilterChange={handleFilterChange}
          hoveredModel={hoveredModel}
          onModelHover={setHoveredModel}
          isOpen={sidebar.isOpen}
          onClose={sidebar.close}
          benchmark={benchmark}
        />
        <main
          ref={mainRef}
          className="flex-1 p-4 md:p-6 overflow-auto pb-20 md:pb-6"
          onClick={(e) => {
            // 빈 공간 클릭 시 호버 효과 해제
            if (e.target === e.currentTarget) {
              setHoveredModel(null)
            }
          }}
        >
          {/* 탭 네비게이션 (데스크톱) */}
          <div className="desktop-tabs hidden md:flex gap-2 mb-6">
            {TAB_KEYS.map(tabKey => (
              <button
                key={tabKey}
                className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                  activeTab === tabKey
                    ? 'bg-blue-500 text-white shadow-md'
                    : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-700'
                }`}
                onClick={() => handleTabChange(tabKey)}
              >
                {t(`tabs.${tabKey}`)}
              </button>
            ))}
          </div>

          {/* 콘텐츠 영역 */}
          <div className="space-y-6">
            {/* 종합 대시보드 탭 */}
            {activeTab === 'overview' && (
              <>
                {scoreChartSplitByEffort ? (
                  [
                    { tier: 'high', label: t('charts.effortHigh'), exportKey: 'overview-score-chart-high' },
                    { tier: 'low', label: t('charts.effortLow'), exportKey: 'overview-score-chart-low' }
                  ].map(({ tier, label, exportKey }) => (
                    <div key={tier} className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                      <ScoreBarChart
                        data={scoreChartDataByEffort[tier]}
                        maxScore={maxScore}
                        title={`${t('charts.officialScore')} · ${label} (${t('charts.maxPoints', { max: maxScore })})`}
                        subtitle={scoreChartSubtitle}
                        hoveredModel={hoveredModel}
                        onModelHover={setHoveredModel}
                        modelMetadata={modelMetadata}
                        exportKey={exportKey}
                      />
                    </div>
                  ))
                ) : (
                  <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                    <ScoreBarChart
                      data={scoreChartData}
                      maxScore={maxScore}
                      title={`${t('charts.officialScore')} (${t('charts.maxPoints', { max: maxScore })})`}
                      subtitle={scoreChartSubtitle}
                      hoveredModel={hoveredModel}
                      onModelHover={setHoveredModel}
                      modelMetadata={modelMetadata}
                    />
                  </div>
                )}
                {/* 점수 차트의 SVG가 카드 경계를 넘어 그려지므로, 아래 카드가
                    그 위에 오도록 stacking context를 만든다 (내보내기 버튼 클릭 차단 방지) */}
                <div className="relative z-10 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                  <CostScatterChart
                    data={costData}
                    title={t('charts.costVsPerformance')}
                    maxScore={maxScore}
                    exportKey="overview-cost-scatter"
                  />
                </div>
                <div className="relative z-10 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                  <BenchmarkScoreTable
                    data={filteredScores}
                    title={t('charts.scoreTable')}
                    maxScore={maxScore}
                    totalQuestions={benchmark?.scoring?.totalQuestions}
                    hoveredModel={hoveredModel}
                    onModelHover={setHoveredModel}
                  />
                </div>
              </>
            )}

            {/* 과목별 상세 탭 */}
            {activeTab === 'subjects' && (
              <>
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                  <div className="flex gap-4 mb-6">
                    <select
                      ref={subjectSelectRef}
                      className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                      value={selectedSubject}
                      onChange={(e) => handleSubjectChange(e.target.value)}
                    >
                      <option value="">{t('charts.selectSubject')}</option>
                      {subjects.map(s => (
                        <option key={s} value={s}>{translateSubject(s, t)}</option>
                      ))}
                    </select>
                    {selectedSubject && availableSections.length > 0 && (
                      <select
                        ref={sectionSelectRef}
                        className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                        value={selectedSection}
                        onChange={(e) => setSelectedSection(e.target.value)}
                      >
                        {availableSections.map(s => (
                          <option key={s} value={s}>{translateSubject(s, t)}</option>
                        ))}
                      </select>
                    )}
                  </div>

                  {selectedSubject && selectedSection && Object.keys(heatmapData).length > 0 && (
                    <QuestionHeatmap
                      data={heatmapData}
                      models={displayModels}
                      title={`${translateSubject(selectedSubject, t)} - ${translateSubject(selectedSection, t)} ${t('charts.questionStatus')}`}
                      subjectName={`${translateSubject(selectedSubject, t)}_${translateSubject(selectedSection, t)}`}
                      modelMetadata={modelMetadata}
                    />
                  )}

                  {selectedSubject && selectedSection && choiceData.length > 0 && (
                    <div className="mt-6 pt-6 border-t border-gray-200 dark:border-gray-700">
                      <ChoiceSelectionChart
                        data={choiceData}
                        title={`${translateSubject(selectedSubject, t)} - ${translateSubject(selectedSection, t)} ${t('charts.choiceRate')}`}
                      />
                    </div>
                  )}

                  {!selectedSubject && (
                    <p className="text-gray-500 dark:text-gray-400 text-center py-8">
                      {t('charts.selectSubject')}
                    </p>
                  )}
                </div>
              </>
            )}

            {/* 모델 비교 탭 */}
            {activeTab === 'compare' && (() => {
              // 정렬된 모델 순서로 개발사별 그룹화
              const sortedModels = filteredScores.map(s => s.model)
              const groupedCompareModels = groupModelsByVendor(sortedModels)
              const sortedVendors = getSortedVendors(groupedCompareModels)

              return (
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 md:p-6">
                  <div className="mb-6">
                    <h4 className="font-medium text-gray-700 dark:text-gray-300 mb-3">
                      {t('charts.compareModels')}
                    </h4>

                    {/* 모바일: 드롭다운 */}
                    <div className="md:hidden">
                      <ModelSelectDropdown
                        models={sortedModels}
                        selected={compareModels}
                        onChange={setCompareModels}
                        maxSelect={5}
                      />
                    </div>

                    {/* 데스크톱: 체크박스 그룹 */}
                    <div className="hidden md:block space-y-3">
                      {sortedVendors.map(vendor => {
                        const vendorModels = groupedCompareModels[vendor.id]
                        if (!vendorModels?.length) return null

                        return (
                          <div key={vendor.id}>
                            {/* 개발사 헤더 */}
                            <div className="flex items-center gap-2 mb-1.5">
                              <span
                                className="w-3 h-3 rounded-full"
                                style={{ backgroundColor: vendor.color }}
                              />
                              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                                {vendor.name}
                              </span>
                              <span className="text-xs text-gray-400 dark:text-gray-500">
                                ({vendorModels.length})
                              </span>
                            </div>
                            {/* 모델 목록 */}
                            <div className="flex flex-wrap gap-2 ml-5">
                              {vendorModels.map(model => {
                                const isSelected = compareModels.includes(model)
                                const isFiltered = filters.models.length === 0 ||
                                                   filters.models.includes(model)
                                return (
                                  <label
                                    key={model}
                                    className={`flex items-center gap-2 px-3 py-1.5 rounded-full cursor-pointer transition-colors border ${
                                      isSelected
                                        ? 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 border-blue-300 dark:border-blue-700'
                                        : isFiltered
                                          ? 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-gray-300 dark:border-gray-600 hover:bg-gray-200 dark:hover:bg-gray-600'
                                          : 'bg-gray-50 dark:bg-gray-800 text-gray-400 dark:text-gray-500 border-gray-200 dark:border-gray-700 opacity-60'
                                    }`}
                                    onMouseEnter={() => setHoveredModel(model)}
                                    onMouseLeave={() => setHoveredModel(null)}
                                  >
                                    <input
                                      type="checkbox"
                                      className="hidden"
                                      checked={isSelected}
                                      onChange={(e) => {
                                        if (e.target.checked && compareModels.length < 5) {
                                          setCompareModels([...compareModels, model])
                                        } else if (!e.target.checked) {
                                          setCompareModels(compareModels.filter(m => m !== model))
                                        }
                                      }}
                                    />
                                    <span className="text-sm">{formatModelDisplayName(model)}</span>
                                  </label>
                                )
                              })}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                  <ModelCompareChart
                    data={radarData}
                    selectedModels={compareModels}
                    allScores={overallScores}
                    title={t('charts.modelCompare')}
                    height={450}
                    hoveredModel={hoveredModel}
                    onModelHover={setHoveredModel}
                    dimensions={radarDimensions}
                  />
                </div>
              )
            })()}

            {/* 비용 분석 탭 */}
            {activeTab === 'cost' && (
              <>
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                  <CostScatterChart
                    data={costData}
                    title={t('charts.costVsPerformance')}
                    maxScore={maxScore}
                  />
                </div>
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                  <TokenUsageChart
                    data={tokenUsage}
                    models={displayModels}
                    subjectFilter={filters.subjects}
                    title={t('charts.tokenUsage')}
                    modelMetadata={modelMetadata}
                  />
                </div>
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                  <CostTable
                    data={costData}
                    title={t('charts.costInfo')}
                  />
                </div>
              </>
            )}
          </div>
        </main>
      </div>
      <Footer />
      <BottomNav activeTab={activeTab} onTabChange={handleTabChange} />
    </div>
  )
}

/**
 * @brief App 루트 컴포넌트
 */
export default function App() {
  const [benchmarkMode, setBenchmarkMode] = useState(INITIAL_QUERY_STATE.mode)
  const [benchmarkId, setBenchmarkId] = useState(INITIAL_QUERY_STATE.benchmark)

  const handleBenchmarkModeChange = useCallback((nextMode) => {
    setBenchmarkMode(nextMode)
    if (typeof window === 'undefined') return

    const url = new URL(window.location.href)
    if (nextMode === 'hard') {
      url.searchParams.set('mode', 'hard')
    } else if (benchmarkId && benchmarkId !== DEFAULT_BENCHMARK_ID) {
      url.searchParams.set('mode', 'default')
    } else {
      url.searchParams.delete('mode')
    }
    window.history.replaceState({}, '', url)
  }, [benchmarkId])

  const handleBenchmarkChange = useCallback((nextBenchmarkId) => {
    setBenchmarkId(nextBenchmarkId)
    setBenchmarkMode('default')
    if (typeof window === 'undefined') return

    const url = new URL(window.location.href)
    if (nextBenchmarkId === DEFAULT_BENCHMARK_ID) {
      url.searchParams.delete('benchmark')
      url.searchParams.delete('mode')
    } else {
      url.searchParams.set('benchmark', nextBenchmarkId)
      url.searchParams.set('mode', 'default')
    }
    url.searchParams.delete('subjects')
    url.searchParams.delete('selectedSubject')
    url.searchParams.delete('selectedSection')
    url.searchParams.delete('scoreView')
    window.history.replaceState({}, '', url)
  }, [])

  return (
    <ThemeProvider>
      <DataProvider mode={benchmarkMode} benchmarkId={benchmarkId}>
        <Dashboard
          benchmarkMode={benchmarkMode}
          onBenchmarkModeChange={handleBenchmarkModeChange}
          onBenchmarkChange={handleBenchmarkChange}
        />
      </DataProvider>
    </ThemeProvider>
  )
}
