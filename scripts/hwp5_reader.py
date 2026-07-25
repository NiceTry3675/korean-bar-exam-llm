"""
@brief HWP 5.x OLE 문서에서 문단 텍스트를 읽는 최소 파서

한컴이 공개한 HWP 5.x 파일 형식 중 FileHeader, BodyText 섹션과
HWPTAG_PARA_TEXT 레코드만 처리합니다. 표 셀의 문단도 BodyText 레코드
순서에 포함되므로 별도의 표 재배치 없이 원문의 읽기 순서를 보존합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import struct
from typing import Iterator
import zlib


HWP5_SIGNATURE = b"HWP Document File"
HWP5_COMPRESSED_FLAG = 1 << 0
HWP5_ENCRYPTED_FLAG = 1 << 1
HWPTAG_PARA_TEXT = 0x010 + 51

# HWP 5.x 인라인/확장 제어 문자는 UTF-16 코드 단위 8개를 차지합니다.
_EIGHT_UNIT_CONTROLS = frozenset(
    {
        0x01,
        0x02,
        0x03,
        0x04,
        0x05,
        0x06,
        0x07,
        0x08,
        0x09,
        0x0B,
        0x0C,
        0x0E,
        0x0F,
        0x10,
        0x11,
        0x12,
        0x13,
        0x14,
        0x15,
        0x16,
        0x17,
    }
)


class Hwp5Error(ValueError):
    """@brief 지원하지 않거나 손상된 HWP 5.x 파일 오류"""


@dataclass(frozen=True)
class HwpRecord:
    """@brief HWP 레코드 헤더와 페이로드"""

    tag_id: int
    level: int
    payload: bytes


@dataclass(frozen=True)
class HwpParagraph:
    """@brief BodyText에서 추출한 문단"""

    text: str
    level: int
    section: int


def _load_olefile():
    """
    @brief 선택 의존성인 olefile 모듈을 지연 로드

    @return olefile 모듈
    @throws RuntimeError olefile이 설치되지 않은 경우
    """
    try:
        import olefile  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "HWP 변환에는 olefile이 필요합니다. requirements.txt 의존성을 설치하세요."
        ) from exc
    return olefile


def _read_u32(data: bytes, offset: int) -> int:
    """
    @brief 리틀엔디언 unsigned 32-bit 값을 읽음

    @param data 원본 바이트
    @param offset 시작 위치
    @return 정수 값
    @throws Hwp5Error 값이 잘린 경우
    """
    if offset + 4 > len(data):
        raise Hwp5Error(f"레코드 헤더가 {offset}바이트 위치에서 잘렸습니다.")
    return struct.unpack_from("<I", data, offset)[0]


def iter_hwp_records(data: bytes) -> Iterator[HwpRecord]:
    """
    @brief 압축 해제된 HWP 레코드 스트림을 순회

    @param data 레코드 스트림 바이트
    @return HwpRecord 반복자
    @throws Hwp5Error 헤더 또는 페이로드가 손상된 경우
    """
    offset = 0
    while offset < len(data):
        header = _read_u32(data, offset)
        offset += 4
        tag_id = header & 0x03FF
        level = (header >> 10) & 0x03FF
        size = (header >> 20) & 0x0FFF

        if size == 0x0FFF:
            size = _read_u32(data, offset)
            offset += 4

        end = offset + size
        if end > len(data):
            raise Hwp5Error(
                f"태그 {tag_id}의 {size}바이트 페이로드가 스트림 끝을 넘습니다."
            )
        yield HwpRecord(tag_id=tag_id, level=level, payload=data[offset:end])
        offset = end


def decode_paragraph_text(payload: bytes) -> str:
    """
    @brief HWPTAG_PARA_TEXT 페이로드를 일반 문자열로 변환

    탭, 줄바꿈, 고정폭/줄바꿈 없는 공백은 사람이 읽을 수 있는 문자로
    보존하고 레이아웃·필드·표 개체 제어 정보만 제거합니다.

    @param payload UTF-16LE 문단 페이로드
    @return 제어 정보가 제거된 문단 문자열
    @throws Hwp5Error UTF-16 코드 단위가 잘린 경우
    """
    if len(payload) % 2:
        raise Hwp5Error("문단 텍스트 페이로드 길이가 UTF-16 단위와 맞지 않습니다.")

    chunks: list[str] = []
    offset = 0
    while offset < len(payload):
        code = struct.unpack_from("<H", payload, offset)[0]
        if code >= 0x20:
            end = offset + 2
            while end < len(payload):
                next_code = struct.unpack_from("<H", payload, end)[0]
                if next_code < 0x20:
                    break
                end += 2
            try:
                chunks.append(payload[offset:end].decode("utf-16le"))
            except UnicodeDecodeError as exc:
                raise Hwp5Error("문단 텍스트의 UTF-16 인코딩이 손상되었습니다.") from exc
            offset = end
            continue

        if code == 0x09:
            chunks.append("\t")
        elif code == 0x0A:
            chunks.append("\n")
        elif code == 0x18:
            chunks.append("-")
        elif code in (0x1E, 0x1F):
            chunks.append(" ")

        control_size = 16 if code in _EIGHT_UNIT_CONTROLS else 2
        if offset + control_size > len(payload):
            raise Hwp5Error(f"제어 문자 0x{code:02x}가 문단 끝에서 잘렸습니다.")
        offset += control_size

    return "".join(chunks).replace("\u00a0", " ").strip()


def _section_sort_key(stream_name: str) -> tuple[int, str]:
    """
    @brief BodyText/SectionN 스트림을 숫자 순으로 정렬

    @param stream_name OLE 스트림 이름
    @return 정렬 키
    """
    match = re.fullmatch(r"BodyText/Section(\d+)", stream_name)
    return (int(match.group(1)), stream_name) if match else (2**31 - 1, stream_name)


def _decompress_section(data: bytes, compressed: bool, stream_name: str) -> bytes:
    """
    @brief BodyText 섹션의 raw DEFLATE 압축을 해제

    @param data 섹션 바이트
    @param compressed 압축 플래그
    @param stream_name 오류 메시지용 스트림 이름
    @return 압축 해제된 섹션
    @throws Hwp5Error 압축 데이터가 손상된 경우
    """
    if not compressed:
        return data
    try:
        decompressor = zlib.decompressobj(wbits=-15)
        return decompressor.decompress(data) + decompressor.flush()
    except zlib.error as exc:
        raise Hwp5Error(f"{stream_name} 압축을 해제할 수 없습니다: {exc}") from exc


def extract_hwp5_paragraphs(path: Path | str) -> list[HwpParagraph]:
    """
    @brief HWP 5.x 파일의 모든 BodyText 문단을 원래 순서대로 추출

    @param path HWP 파일 경로
    @return 비어 있지 않은 문단 목록
    @throws FileNotFoundError 파일이 없는 경우
    @throws Hwp5Error HWP 5.x 형식이 아니거나 암호화/손상된 경우
    """
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    olefile = _load_olefile()
    try:
        ole = olefile.OleFileIO(str(source_path))
    except (OSError, IOError) as exc:
        raise Hwp5Error(f"OLE 문서를 열 수 없습니다: {source_path}") from exc

    try:
        if not ole.exists("FileHeader"):
            raise Hwp5Error("FileHeader 스트림이 없습니다.")
        header = ole.openstream("FileHeader").read()
        if not header.startswith(HWP5_SIGNATURE) or len(header) < 40:
            raise Hwp5Error("HWP 5.x FileHeader 서명이 올바르지 않습니다.")

        flags = _read_u32(header, 36)
        if flags & HWP5_ENCRYPTED_FLAG:
            raise Hwp5Error("암호화된 HWP 파일은 지원하지 않습니다.")

        stream_names = [
            "/".join(parts)
            for parts in ole.listdir(streams=True, storages=False)
            if len(parts) == 2
            and parts[0] == "BodyText"
            and re.fullmatch(r"Section\d+", parts[1])
        ]
        stream_names.sort(key=_section_sort_key)
        if not stream_names:
            raise Hwp5Error("BodyText/SectionN 스트림이 없습니다.")

        paragraphs: list[HwpParagraph] = []
        for section_index, stream_name in enumerate(stream_names):
            raw = ole.openstream(stream_name).read()
            section_data = _decompress_section(
                raw, bool(flags & HWP5_COMPRESSED_FLAG), stream_name
            )
            for record in iter_hwp_records(section_data):
                if record.tag_id != HWPTAG_PARA_TEXT:
                    continue
                text = decode_paragraph_text(record.payload)
                if text:
                    paragraphs.append(
                        HwpParagraph(
                            text=text, level=record.level, section=section_index
                        )
                    )
        return paragraphs
    finally:
        ole.close()


def list_binary_streams(path: Path | str) -> list[str]:
    """
    @brief HWP 파일의 BinData 스트림 이름을 나열

    @param path HWP 파일 경로
    @return 내장 바이너리 스트림 이름 목록
    """
    olefile = _load_olefile()
    with olefile.OleFileIO(str(Path(path))) as ole:
        return sorted(
            "/".join(parts)
            for parts in ole.listdir(streams=True, storages=False)
            if parts and parts[0] == "BinData"
        )
