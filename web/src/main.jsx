import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './i18n'  // i18n 초기화
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

/**
 * @brief 서비스 워커 등록
 *
 * Vite가 문자열 리터럴은 base로 재작성하지 않으므로, 인라인 스크립트 대신
 * 여기서 import.meta.env.BASE_URL을 사용한다. 저장소 이름이 바뀌어도
 * vite.config.js의 base 한 곳만 고치면 된다.
 */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register(`${import.meta.env.BASE_URL}sw.js`)
      .catch((error) => {
        console.warn('SW registration failed:', error)
      })
  })
}
