"""@brief HWP 5.x 최소 파서의 합성 레코드 테스트"""

from pathlib import Path
import struct
import sys
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from hwp5_reader import (  # noqa: E402
    Hwp5Error,
    HWPTAG_PARA_TEXT,
    decode_paragraph_text,
    iter_hwp_records,
)


def _record(tag_id: int, level: int, payload: bytes, extended: bool = False) -> bytes:
    """
    @brief 테스트용 HWP 레코드 생성

    @param tag_id 태그 ID
    @param level 레코드 레벨
    @param payload 페이로드
    @param extended 확장 길이 헤더 사용 여부
    @return 직렬화된 레코드
    """
    size = 0x0FFF if extended else len(payload)
    header = tag_id | (level << 10) | (size << 20)
    result = struct.pack("<I", header)
    if extended:
        result += struct.pack("<I", len(payload))
    return result + payload


def _extended_control(code: int) -> bytes:
    """
    @brief UTF-16 코드 단위 8개인 테스트 제어 문자 생성

    @param code 제어 코드
    @return 16바이트 제어 데이터
    """
    return struct.pack("<H", code) + (b"\x00" * 14)


class HwpRecordTest(unittest.TestCase):
    """@brief HWP 레코드 헤더 파싱 테스트"""

    def test_iterates_compact_and_extended_records(self):
        """@brief 일반/확장 길이 레코드를 순서대로 읽음"""
        first = "첫 문단".encode("utf-16le")
        second = "표 셀".encode("utf-16le")
        stream = _record(HWPTAG_PARA_TEXT, 1, first) + _record(
            HWPTAG_PARA_TEXT, 3, second, extended=True
        )

        records = list(iter_hwp_records(stream))

        self.assertEqual([record.level for record in records], [1, 3])
        self.assertEqual([record.payload for record in records], [first, second])

    def test_rejects_truncated_payload(self):
        """@brief 헤더가 선언한 길이보다 페이로드가 짧으면 실패"""
        header = HWPTAG_PARA_TEXT | (1 << 10) | (20 << 20)
        with self.assertRaisesRegex(Hwp5Error, "스트림 끝"):
            list(iter_hwp_records(struct.pack("<I", header) + b"short"))


class ParagraphDecodeTest(unittest.TestCase):
    """@brief HWP 문단 텍스트 제어 문자 테스트"""

    def test_preserves_text_tabs_breaks_and_skips_object_controls(self):
        """@brief 표 제어 데이터는 제거하고 가시적 공백은 보존"""
        payload = (
            "① 첫째".encode("utf-16le")
            + _extended_control(0x09)
            + "ㄱ".encode("utf-16le")
            + _extended_control(0x0B)
            + struct.pack("<H", 0x0A)
            + "② 둘째".encode("utf-16le")
            + struct.pack("<H", 0x1E)
            + "끝".encode("utf-16le")
        )

        self.assertEqual(decode_paragraph_text(payload), "① 첫째\tㄱ\n② 둘째 끝")

    def test_rejects_odd_utf16_payload(self):
        """@brief 홀수 바이트 문단을 거부"""
        with self.assertRaisesRegex(Hwp5Error, "UTF-16"):
            decode_paragraph_text(b"\x01")

    def test_rejects_invalid_utf16_sequence(self):
        """@brief 잘못된 서로게이트를 대체문자로 숨기지 않음"""
        with self.assertRaisesRegex(Hwp5Error, "UTF-16"):
            decode_paragraph_text(struct.pack("<H", 0xD800))

    def test_rejects_truncated_extended_control(self):
        """@brief 8코드 단위 제어 문자가 잘리면 실패"""
        with self.assertRaisesRegex(Hwp5Error, "잘렸습니다"):
            decode_paragraph_text(struct.pack("<H", 0x0B))


if __name__ == "__main__":
    unittest.main()
