/**
 * @file SubjectFilter.jsx
 * @brief 레지스트리 과목 목록 기반 과목 필터 체크박스 컴포넌트
 */

import { useTranslation } from 'react-i18next'
import { translateSubject } from '@/utils/subjectLabels'

/**
 * @brief 과목 필터 컴포넌트
 * @param {string[]} props.selected - 선택된 과목 배열
 * @param {function} props.onChange - 선택 변경 콜백
 * @param {string[]} props.subjects - 표시할 과목 목록 (레지스트리 섹션에서 파생)
 */
export default function SubjectFilter({ selected, onChange, subjects = [] }) {
  const { t } = useTranslation()

  return (
    <div className="mb-6">
      <h3 className="font-semibold mb-2 text-gray-700 dark:text-gray-300">{t('sidebar.subjectFilter')}</h3>
      <div className="space-y-1">
        {subjects.map(subject => (
          <label key={subject} className="flex items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 p-1 rounded text-gray-800 dark:text-gray-200">
            <input
              type="checkbox"
              checked={selected.includes(subject)}
              onChange={() => onChange(
                selected.includes(subject)
                  ? selected.filter(item => item !== subject)
                  : [...selected, subject]
              )}
              className="mr-2 rounded"
            />
            <span className="text-sm font-medium">{translateSubject(subject, t)}</span>
          </label>
        ))}
      </div>
    </div>
  )
}
