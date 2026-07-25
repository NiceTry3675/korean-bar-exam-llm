/**
 * @file urlState.js
 * @brief URL 쿼리 기반 대시보드 초기 상태 유틸리티
 */

const VALID_TABS = new Set(['overview', 'subjects', 'compare', 'cost'])
const VALID_THEMES = new Set(['light', 'dark'])
const VALID_MODES = new Set(['default', 'hard'])

function _getSearchParams() {
  if (typeof window === 'undefined') return new URLSearchParams()
  return new URLSearchParams(window.location.search)
}

function _getEnumValue(value, validSet, fallback = '') {
  return value && validSet.has(value) ? value : fallback
}

function _getListValue(value) {
  if (!value) return []
  return value
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
}

/**
 * @brief 대시보드 초기 URL 상태 파싱
 * @return {Object} 초기 상태
 */
export function parseDashboardQueryState(searchParams) {
  const params = searchParams instanceof URLSearchParams
    ? searchParams
    : new URLSearchParams(searchParams || '')

  return {
    tab: _getEnumValue(params.get('tab'), VALID_TABS, 'overview'),
    subjects: _getListValue(params.get('subjects')),
    // README 이미지 내보내기에서 기본 표시 규칙 대신 모델을 명시할 때 사용한다.
    // 'all'을 주면 전체 모델을 표시한다.
    models: _getListValue(params.get('models')),
    selectedSubject: params.get('selectedSubject') || '',
    selectedSection: params.get('selectedSection') || '',
    theme: _getEnumValue(params.get('theme'), VALID_THEMES, ''),
    mode: _getEnumValue(params.get('mode'), VALID_MODES, 'default'),
    benchmark: params.get('benchmark') || ''
  }
}

/**
 * @brief 현재 URL의 대시보드 초기 상태 파싱
 * @return {Object} 초기 상태
 */
export function getDashboardQueryState() {
  return parseDashboardQueryState(_getSearchParams())
}

/**
 * @brief URL로 강제된 테마 반환
 * @return {'light' | 'dark' | ''} 강제 테마
 */
export function getForcedThemeFromUrl() {
  return getDashboardQueryState().theme
}
