"""
@brief 벤치마크 레지스트리를 기준으로 웹용 문항 메타데이터 생성

로컬 questions.json이 있으면 이미지 여부와 배점을 읽고, 문제 본문이 없는
공개 벤치마크는 추적되는 공개 정답 메타데이터에서 구조만 읽습니다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / 'benchmarks' / 'registry.json'
DEFAULT_BENCHMARK = 'bar-exam-15'


def _load_json(path: Path) -> dict[str, Any]:
    """
    @brief JSON 객체를 UTF-8로 읽는다.

    @param path 입력 파일
    @return JSON 객체
    @throws ValueError 최상위 값이 객체가 아닐 때
    """
    with open(path, 'r', encoding='utf-8') as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f'JSON 객체가 필요합니다: {path}')
    return value


def _find_benchmark(registry: dict[str, Any], benchmark_id: str) -> dict[str, Any]:
    """
    @brief ID에 대응하는 벤치마크 설정을 찾는다.

    @param registry 레지스트리 객체
    @param benchmark_id 벤치마크 ID
    @return 벤치마크 설정
    @throws ValueError ID가 없을 때
    """
    benchmark = next(
        (
            item for item in registry.get('benchmarks', [])
            if item.get('id') == benchmark_id
        ),
        None,
    )
    if benchmark is None:
        raise ValueError(f'알 수 없는 벤치마크: {benchmark_id}')
    return benchmark


def _load_section_questions(section: dict[str, Any]) -> list[dict[str, Any]]:
    """
    @brief 한 섹션의 로컬 또는 공개 문항 메타데이터를 읽는다.

    @param section 레지스트리 섹션
    @return 문항 메타데이터 배열
    @throws FileNotFoundError 사용할 메타데이터가 없을 때
    """
    local_path = REPO_ROOT / section['problemDir'] / 'questions.json'
    if local_path.exists():
        return _load_json(local_path).get('questions', [])

    metadata_value = section.get('metadataPath')
    if metadata_value:
        metadata_path = REPO_ROOT / metadata_value
        metadata = _load_json(metadata_path)
        public_subject = next(
            (
                subject for subject in metadata.get('subjects', [])
                if subject.get('id') == section.get('id')
                or subject.get('sheet_name') == section.get('sheet')
            ),
            None,
        )
        if public_subject:
            return public_subject.get('questions', [])

    raise FileNotFoundError(f"문항 메타데이터 없음: {section.get('sheet')}")


def build_metadata(benchmark: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    @brief 벤치마크 전체를 웹 문항 메타데이터 형식으로 변환한다.

    @param benchmark 레지스트리 벤치마크 설정
    @return `과목-섹션 -> 문항번호` 메타데이터
    """
    metadata: dict[str, dict[str, Any]] = {}
    for section in benchmark.get('sections', []):
        try:
            questions = _load_section_questions(section)
        except FileNotFoundError as error:
            print(f'  ⚠ {error}')
            continue

        key = f"{section['subject']}-{section['section']}"
        metadata[key] = {
            str(question['number']): {
                'hasImage': bool(question.get('image_paths', [])),
                'points': question.get('points', 0),
            }
            for question in questions
        }
        print(f"  ✓ {key}: {len(metadata[key])}문항")
    return metadata


def _build_parser() -> argparse.ArgumentParser:
    """@brief 명령행 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description='웹용 문항 메타데이터 생성')
    parser.add_argument('--benchmark', default=DEFAULT_BENCHMARK)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument('--output', type=Path)
    return parser


def main() -> None:
    """@brief 메타데이터 생성 진입점"""
    args = _build_parser().parse_args()
    registry_path = args.registry if args.registry.is_absolute() else REPO_ROOT / args.registry
    registry = _load_json(registry_path)
    benchmark = _find_benchmark(registry, args.benchmark)
    metadata = build_metadata(benchmark)

    output_path = args.output
    if output_path is None:
        default_name = (
            'questions_metadata.json'
            if args.benchmark == registry.get('defaultBenchmark', DEFAULT_BENCHMARK)
            else f'{args.benchmark}_questions_metadata.json'
        )
        output_path = REPO_ROOT / 'web' / 'public' / default_name
    elif not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write('\n')

    question_count = sum(len(section) for section in metadata.values())
    image_count = sum(
        1
        for section in metadata.values()
        for question in section.values()
        if question['hasImage']
    )
    print(f'\n✓ 생성 완료: {output_path}')
    print(f'  총 {question_count}문항 중 {image_count}문항에 이미지 포함')


if __name__ == '__main__':
    main()
