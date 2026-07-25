/**
 * @file useData.jsx
 * @brief 전역 데이터 상태 관리를 위한 Context 및 Hook
 */

import { createContext, useContext, useState, useEffect } from 'react'
import {
  loadAllResults,
  loadBenchmarkAvailability,
  loadBenchmarkRegistry,
  loadTokenUsage,
  loadModelMetadata,
  loadQuestionsMetadata,
  extractUniqueValues
} from '@/utils/dataLoader'
import { getNavigableBenchmarks, resolveBenchmark } from '@/utils/benchmarkRegistry'

const DataContext = createContext(null)

/**
 * @brief 데이터 제공자 컴포넌트
 * @param {Object} props - { children, mode, benchmarkId }
 */
export function DataProvider({ children, mode = 'default', benchmarkId = '' }) {
  const [data, setData] = useState([])
  const [tokenUsage, setTokenUsage] = useState({})
  const [modelMetadata, setModelMetadata] = useState({})
  const [questionsMetadata, setQuestionsMetadata] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [subjects, setSubjects] = useState([])
  const [sections, setSections] = useState({})
  const [models, setModels] = useState([])
  const [dataMode, setDataMode] = useState(null)
  const [dataBenchmarkId, setDataBenchmarkId] = useState(null)
  const [registry, setRegistry] = useState(null)
  const [benchmark, setBenchmark] = useState(null)
  const [benchmarkAvailability, setBenchmarkAvailability] = useState({})
  const [navigableBenchmarks, setNavigableBenchmarks] = useState([])

  useEffect(() => {
    let cancelled = false

    async function fetchData() {
      try {
        setError(null)
        setLoading(true)

        const registryData = await loadBenchmarkRegistry()
        const selectedBenchmark = resolveBenchmark(registryData, benchmarkId)
        if (!selectedBenchmark) {
          throw new Error('선택한 벤치마크를 찾을 수 없습니다')
        }

        // 현재 결과와 탐색 노출 정보를 병렬로 로드
        const [resultsData, usageData, modelMetadataData, questionsData, availabilityData] = await Promise.all([
          loadAllResults(selectedBenchmark, mode),
          loadTokenUsage(selectedBenchmark, mode),
          loadModelMetadata(),
          loadQuestionsMetadata(selectedBenchmark, mode),
          loadBenchmarkAvailability(registryData)
        ])

        if (cancelled) return

        setData(resultsData)
        setTokenUsage(usageData)
        setModelMetadata(modelMetadataData)
        setQuestionsMetadata(questionsData)
        setRegistry(registryData)
        setBenchmark(selectedBenchmark)
        const completeAvailability = {
          ...availabilityData,
          [selectedBenchmark.id]: (
            availabilityData[selectedBenchmark.id] === true || resultsData.length > 0
          )
        }
        setBenchmarkAvailability(completeAvailability)
        setNavigableBenchmarks(getNavigableBenchmarks(
          registryData,
          completeAvailability
        ))

        // 메타데이터 추출
        const { subjects, sections, models } = extractUniqueValues(resultsData, selectedBenchmark)
        setSubjects(subjects)
        setSections(sections)
        setModels(models)
        setDataMode(mode)
        setDataBenchmarkId(selectedBenchmark.id)

        setLoading(false)
      } catch (err) {
        if (cancelled) return
        setError(err.message)
        setLoading(false)
      }
    }

    fetchData()

    return () => {
      cancelled = true
    }
  }, [mode, benchmarkId])

  const value = {
    data,
    tokenUsage,
    modelMetadata,
    questionsMetadata,
    loading,
    error,
    subjects,
    sections,
    models,
    dataMode,
    dataBenchmarkId,
    registry,
    benchmark,
    benchmarkAvailability,
    navigableBenchmarks,
    resultsPending: !loading && !error && data.length === 0
  }

  return (
    <DataContext.Provider value={value}>
      {children}
    </DataContext.Provider>
  )
}

/**
 * @brief 데이터 컨텍스트 사용 훅
 * @return {Object} { data, tokenUsage, modelMetadata, questionsMetadata, loading, error, subjects, sections, models, dataMode }
 */
export function useData() {
  const context = useContext(DataContext)
  if (!context) {
    throw new Error('useData must be used within a DataProvider')
  }
  return context
}
