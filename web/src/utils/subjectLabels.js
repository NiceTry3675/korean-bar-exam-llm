/**
 * @file subjectLabels.js
 * @brief 과목/섹션명 번역 키 매핑과 번역 헬퍼
 */

/**
 * @brief 과목/섹션명 → 번역 키 맵핑
 */
export const SUBJECT_I18N_KEYS = {
  '공법': 'subjects.publicLaw',
  '민사법': 'subjects.civilLaw',
  '형사법': 'subjects.criminalLaw',
  '선택형': 'subjects.multipleChoice'
}

/**
 * @brief 과목/섹션명 번역 헬퍼
 * @param {string} name - 과목/섹션명
 * @param {function} t - 번역 함수
 * @return {string} 번역된 이름 (매핑이 없으면 원래 이름)
 */
export function translateSubject(name, t) {
  return SUBJECT_I18N_KEYS[name] ? t(SUBJECT_I18N_KEYS[name]) : name
}
