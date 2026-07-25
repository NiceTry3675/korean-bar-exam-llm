/**
 * @file ExportWatermark.jsx
 * @brief 내보낸 이미지에만 표시되는 출처 워터마크
 */

/** @brief 내보내기 이미지에 새겨지는 출처 문자열 */
export const EXPORT_WATERMARK = 'Github/NiceTry3675'

/**
 * @brief 화면에서는 숨기고 이미지 내보내기에서만 나타나는 워터마크
 *
 * `hidden` 클래스와 `data-export-show` 속성 조합은 useExportImage가
 * 내보내기 직전에 표시로 전환하는 규약이다. 둘 다 유지해야 한다.
 */
export default function ExportWatermark() {
  return (
    <span className="hidden text-base text-gray-400 mt-8" data-export-show="true">
      {EXPORT_WATERMARK}
    </span>
  )
}
