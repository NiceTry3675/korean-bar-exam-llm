"""
@brief 제15회 변호사시험 HWP 원문을 로컬 벤치마크 입력으로 변환

공개 저장소에는 정답·배점·출처 메타데이터만 두고, 이 스크립트가 만든
문제 본문과 questions.json은 git-ignored problems/bar-exam-15 아래에 둡니다.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable

from hwp5_reader import Hwp5Error, HwpParagraph, extract_hwp5_paragraphs, list_binary_streams


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METADATA_PATH = REPO_ROOT / "benchmarks" / "bar-exam-15" / "questions.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "problems" / "bar-exam-15"
DEFAULT_SOURCE_DIR = DEFAULT_OUTPUT_DIR / "source"
QUESTION_MARKER = re.compile(r"^\s*문\s*(\d{1,3})\s*\.\s*(.*)$")
CHOICE_MARKERS = ("①", "②", "③", "④", "⑤")
TRAILING_BOILERPLATE = frozenset({"이하부터는 여백입니다"})


class BarExamValidationError(ValueError):
    """@brief 변호사시험 원문 또는 메타데이터 검증 오류"""


@dataclass(frozen=True)
class SourceSpec:
    """@brief 과목별 공식 HWP 원문 사양"""

    subject_id: str
    label: str
    problem_filename: str
    answer_filename: str
    problem_sha256: str
    answer_sha256: str
    question_count: int
    max_points: float


@dataclass(frozen=True)
class PreparedQuestion:
    """@brief 검증을 마친 로컬 문제"""

    number: int
    text: str
    correct_answer: int
    points: float


SOURCE_SPECS = (
    SourceSpec(
        subject_id="public-law",
        label="공법",
        problem_filename="제15회 변호사시험 공법 선택형.hwp",
        answer_filename="변호사시험 정답가안(공법 선택형).hwp",
        problem_sha256="17c8ed9064c75e771646edd1ad35ebf915b3ea8a62251eadbf4ccfffacb71f4c",
        answer_sha256="85e0fa785b4fbb0b46d50063a3012f359fb85d3439023b52e51a624d33ca3217",
        question_count=40,
        max_points=100.0,
    ),
    SourceSpec(
        subject_id="civil-law",
        label="민사법",
        problem_filename="제15회 변호사시험 민사법 선택형.hwp",
        answer_filename="변호사시험 정답가안(민사법 선택형).hwp",
        problem_sha256="97b78c3fbf130cd066e7adbac9783e48ffd75bcf8c9527ded93600ad01f12faf",
        answer_sha256="853e0cb0ce0db6adf88ec081c983c153af12f133f6e000f4bc7c143146d1938a",
        question_count=70,
        max_points=175.0,
    ),
    SourceSpec(
        subject_id="criminal-law",
        label="형사법",
        problem_filename="제15회 변호사시험 형사법 선택형.hwp",
        answer_filename="변호사시험 정답가안(형사법 선택형).hwp",
        problem_sha256="dad5b6e8b3890cbbf952a8183146950c046d2b145e0a2b5e246326df9ed4a13b",
        answer_sha256="83ec2e7f876f626a028fba4b44c184b49a08a129271ba0ef439d3a679b9ed30b",
        question_count=40,
        max_points=100.0,
    ),
)


def _sha256(path: Path) -> str:
    """
    @brief 파일의 SHA-256 계산

    @param path 파일 경로
    @return 64자리 16진수 해시
    """
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_hash(path: Path, expected: str) -> str:
    """
    @brief 공식 원문의 고정 해시를 검증

    @param path 파일 경로
    @param expected 기대 SHA-256
    @return 실제 SHA-256
    @throws BarExamValidationError 해시가 다른 경우
    """
    actual = _sha256(path)
    if actual != expected:
        raise BarExamValidationError(
            f"원본 해시 불일치: {path.name}\n기대: {expected}\n실제: {actual}"
        )
    return actual


def split_questions(
    paragraphs: Iterable[HwpParagraph], expected_count: int
) -> dict[int, str]:
    """
    @brief HWP 문단을 '문 N.' 표지 기준으로 개별 문제로 분리

    표 내부 문단도 입력 순서대로 현재 문제에 포함합니다.

    @param paragraphs 원문 문단 목록
    @param expected_count 기대 문항 수
    @return 문항번호와 전체 텍스트 매핑
    @throws BarExamValidationError 문항 표지가 중복·누락된 경우
    """
    grouped: dict[int, list[tuple[str, int]]] = {}
    current_number: int | None = None

    for paragraph in paragraphs:
        marker = QUESTION_MARKER.match(paragraph.text)
        if marker:
            number = int(marker.group(1))
            if number in grouped:
                raise BarExamValidationError(f"문 {number} 표지가 중복되었습니다.")
            current_number = number
            grouped[number] = [(f"문 {number}.", paragraph.level)]
            remainder = marker.group(2).strip()
            if remainder:
                grouped[number].append((remainder, paragraph.level))
            continue
        if current_number is not None:
            grouped[current_number].append((paragraph.text, paragraph.level))

    expected_numbers = list(range(1, expected_count + 1))
    actual_numbers = sorted(grouped)
    if actual_numbers != expected_numbers:
        missing = sorted(set(expected_numbers) - set(actual_numbers))
        extra = sorted(set(actual_numbers) - set(expected_numbers))
        raise BarExamValidationError(
            f"문항번호 불일치: 누락={missing or '없음'}, 초과={extra or '없음'}"
        )

    for number in expected_numbers:
        while grouped[number] and grouped[number][-1][0].strip() in TRAILING_BOILERPLATE:
            grouped[number].pop()

    questions = {
        number: "\n".join(line for line, _ in grouped[number] if line).strip() + "\n"
        for number in expected_numbers
    }
    for number in expected_numbers:
        # 실제 선택지는 최상위 문단에 있고, 제시 법령·보기의 표 셀은 더 깊은
        # 레벨에 있습니다. 표 안의 조문 번호 ① 등을 선택지로 오인하지 않습니다.
        choice_text = "\n".join(
            line for line, level in grouped[number] if level == 1
        )
        _validate_choice_markers(number, choice_text)
    return questions


def _validate_choice_markers(number: int, text: str) -> None:
    """
    @brief 한 문제에 ①~⑤가 각각 한 번 있는지 검증

    @param number 문항번호
    @param text 전체 문제 텍스트
    @throws BarExamValidationError 선택지가 없거나 중복된 경우
    """
    # 선택지 라벨은 최상위 문단 시작 또는 같은 문단 내 탭 다음에 나타납니다.
    # 선택지 본문의 "위 ④" 같은 참조는 포함하지 않습니다.
    found = re.findall(r"(?:^|[\n\t])([①②③④⑤])", text)
    if found != list(CHOICE_MARKERS):
        raise BarExamValidationError(
            f"문 {number} 선택지 표지 오류: 발견={found}"
        )


def extract_answer_key(
    paragraphs: Iterable[HwpParagraph], expected_count: int
) -> dict[int, int]:
    """
    @brief 공식 정답표의 표 셀을 문항번호·정답 쌍으로 변환

    @param paragraphs 정답 HWP 문단 목록
    @param expected_count 기대 문항 수
    @return 문항번호별 정답
    @throws BarExamValidationError 정답표 구조나 답 범위가 잘못된 경우
    """
    numeric_cells = [
        int(paragraph.text)
        for paragraph in paragraphs
        if re.fullmatch(r"\d+", paragraph.text)
    ]
    if len(numeric_cells) != expected_count * 2:
        raise BarExamValidationError(
            f"정답표 숫자 셀은 {expected_count * 2}개여야 하나 "
            f"{len(numeric_cells)}개입니다."
        )

    answers: dict[int, int] = {}
    for offset in range(0, len(numeric_cells), 2):
        number, answer = numeric_cells[offset : offset + 2]
        if number in answers:
            raise BarExamValidationError(f"정답표에 문 {number}이 중복되었습니다.")
        if not 1 <= answer <= 5:
            raise BarExamValidationError(f"문 {number} 정답 {answer}은 범위 밖입니다.")
        answers[number] = answer

    expected_numbers = list(range(1, expected_count + 1))
    if sorted(answers) != expected_numbers:
        raise BarExamValidationError("정답표 문항번호가 1부터 연속적이지 않습니다.")
    return answers


def _load_public_metadata(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """
    @brief 추적되는 공개 정답 메타데이터를 로드하고 기본 구조 검증

    @param path questions.json 경로
    @return 과목 ID별 메타데이터와 전체 공개 메타데이터
    @throws BarExamValidationError 스키마 또는 벤치 ID가 잘못된 경우
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BarExamValidationError(f"공개 메타데이터를 읽을 수 없습니다: {path}") from exc

    if (
        data.get("version") != 1
        or data.get("benchmark_id") != "bar-exam-15"
        or data.get("answer_status") != "final_confirmed"
    ):
        raise BarExamValidationError(
            "공개 메타데이터 버전, benchmark_id 또는 answer_status가 잘못되었습니다."
        )
    subjects = data.get("subjects")
    if not isinstance(subjects, list):
        raise BarExamValidationError("공개 메타데이터 subjects는 배열이어야 합니다.")
    mapped_subjects: dict[str, dict[str, Any]] = {}
    for subject in subjects:
        if not isinstance(subject, dict) or not isinstance(subject.get("id"), str):
            raise BarExamValidationError("공개 과목에는 문자열 id가 필요합니다.")
        if subject["id"] in mapped_subjects:
            raise BarExamValidationError(f"공개 과목 id 중복: {subject['id']}")
        mapped_subjects[subject["id"]] = subject
    return mapped_subjects, data


def _validate_public_source_hashes(metadata: dict[str, Any]) -> None:
    """
    @brief 공개 출처 레코드의 여섯 SHA-256을 고정 사양과 대조

    @param metadata 전체 공개 메타데이터
    @throws BarExamValidationError 출처 해시가 없거나 불일치한 경우
    """
    source_files = metadata.get("provenance", {}).get("source_files", [])
    actual = {
        (source.get("role"), source.get("subject_id")): source.get("sha256")
        for source in source_files
        if isinstance(source, dict)
    }
    expected = {}
    for spec in SOURCE_SPECS:
        expected[("questions", spec.subject_id)] = spec.problem_sha256
        expected[("answers", spec.subject_id)] = spec.answer_sha256
    if actual != expected:
        raise BarExamValidationError("공개 메타데이터의 원본 SHA-256 목록이 잘못되었습니다.")


def _public_answers(subject: dict[str, Any], spec: SourceSpec) -> dict[int, int]:
    """
    @brief 공개 메타데이터의 정답·배점을 검증하고 답안 매핑 생성

    @param subject 공개 과목 메타데이터
    @param spec 기대 과목 사양
    @return 문항번호별 정답
    @throws BarExamValidationError 개수·번호·배점·정답이 잘못된 경우
    """
    questions = subject.get("questions")
    if not isinstance(questions, list) or len(questions) != spec.question_count:
        raise BarExamValidationError(
            f"{spec.label} 공개 문항 수는 {spec.question_count}개여야 합니다."
        )

    answers: dict[int, int] = {}
    score = 0.0
    for question in questions:
        number = question.get("number")
        answer = question.get("correct_answer")
        points = question.get("points")
        if not isinstance(number, int) or number in answers:
            raise BarExamValidationError(f"{spec.label} 공개 문항번호가 잘못되었습니다.")
        if not isinstance(answer, int) or not 1 <= answer <= 5:
            raise BarExamValidationError(f"{spec.label} 문 {number} 정답이 잘못되었습니다.")
        if points != 2.5:
            raise BarExamValidationError(f"{spec.label} 문 {number} 배점은 2.5여야 합니다.")
        if set(question) != {"number", "correct_answer", "points"}:
            raise BarExamValidationError(
                f"{spec.label} 문 {number} 공개 메타데이터에는 번호·정답·배점만 허용됩니다."
            )
        answers[number] = answer
        score += float(points)

    if sorted(answers) != list(range(1, spec.question_count + 1)):
        raise BarExamValidationError(f"{spec.label} 공개 문항번호가 연속적이지 않습니다.")
    if score != spec.max_points:
        raise BarExamValidationError(
            f"{spec.label} 총점은 {spec.max_points:g}여야 하나 {score:g}입니다."
        )
    return answers


def _atomic_write_text(path: Path, content: str) -> None:
    """
    @brief 같은 디렉터리의 임시 파일을 이용해 텍스트를 원자적으로 저장

    @param path 대상 경로
    @param content UTF-8 내용
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _repo_relative_or_absolute(path: Path) -> str:
    """
    @brief 표준 출력 경로는 저장소 상대 경로, 그 외는 절대 경로로 표현

    @param path 변환 대상 파일 경로
    @return POSIX 형식 경로
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _validate_output_dir(output_dir: Path) -> None:
    """
    @brief 문제 본문 출력이 현재 저장소의 ignored problems 아래인지 검증

    @param output_dir 요청된 출력 디렉터리
    @throws BarExamValidationError 저장소의 problems 바깥일 때
    """
    problems_root = (REPO_ROOT / "problems").resolve()
    try:
        output_dir.resolve().relative_to(problems_root)
    except ValueError as exc:
        raise BarExamValidationError(
            "문제 본문 출력은 현재 저장소의 ignored problems/ 아래만 허용됩니다."
        ) from exc


def _write_subject(
    output_dir: Path, spec: SourceSpec, questions: list[PreparedQuestion]
) -> None:
    """
    @brief 한 과목의 본문 파일과 로컬 questions.json 저장

    @param output_dir 벤치 출력 루트
    @param spec 과목 사양
    @param questions 검증된 문제 목록
    """
    subject_dir = output_dir / spec.subject_id
    records: list[dict[str, Any]] = []
    for question in questions:
        question_path = subject_dir / f"{question.number}.txt"
        _atomic_write_text(question_path, question.text)
        records.append(
            {
                "number": question.number,
                "type": "multiple_choice",
                "choices": [1, 2, 3, 4, 5],
                "correct_answer": question.correct_answer,
                "points": question.points,
                "question_path": _repo_relative_or_absolute(question_path),
                "image_paths": [],
            }
        )

    local_metadata = {
        "version": 1,
        "benchmark_id": "bar-exam-15",
        "subject_id": spec.subject_id,
        "subject": spec.label,
        "section": spec.label,
        "questions": records,
    }
    _atomic_write_text(
        subject_dir / "questions.json",
        json.dumps(local_metadata, ensure_ascii=False, indent=2) + "\n",
    )


def prepare_benchmark(
    source_dir: Path,
    output_dir: Path,
    metadata_path: Path = DEFAULT_METADATA_PATH,
    check_only: bool = False,
) -> dict[str, Any]:
    """
    @brief 여섯 HWP를 검증하고 로컬 문제 데이터셋 준비

    @param source_dir 공식 HWP가 있는 디렉터리
    @param output_dir ignored 문제 출력 디렉터리
    @param metadata_path 공개 정답 메타데이터
    @param check_only True이면 파일을 쓰지 않고 검증만 수행
    @return 검증 결과 매니페스트
    @throws BarExamValidationError 원문·정답·선택지 검증 실패
    """
    if not check_only:
        _validate_output_dir(output_dir)

    public_subjects, public_metadata = _load_public_metadata(metadata_path)
    _validate_public_source_hashes(public_metadata)
    prepared_subjects: list[tuple[SourceSpec, list[PreparedQuestion]]] = []
    source_records: list[dict[str, Any]] = []

    for spec in SOURCE_SPECS:
        if spec.subject_id not in public_subjects:
            raise BarExamValidationError(f"공개 메타데이터에 {spec.subject_id}가 없습니다.")

        problem_path = source_dir / spec.problem_filename
        answer_path = source_dir / spec.answer_filename
        for path in (problem_path, answer_path):
            if not path.exists():
                raise BarExamValidationError(f"원본 파일이 없습니다: {path}")

        problem_hash = _verify_hash(problem_path, spec.problem_sha256)
        answer_hash = _verify_hash(answer_path, spec.answer_sha256)
        binary_streams = list_binary_streams(problem_path)
        if binary_streams:
            raise BarExamValidationError(
                f"{problem_path.name}에 지원하지 않는 BinData가 있습니다: {binary_streams}"
            )

        problem_paragraphs = extract_hwp5_paragraphs(problem_path)
        answer_paragraphs = extract_hwp5_paragraphs(answer_path)
        problem_texts = split_questions(problem_paragraphs, spec.question_count)
        hwp_answers = extract_answer_key(answer_paragraphs, spec.question_count)
        published_answers = _public_answers(public_subjects[spec.subject_id], spec)
        if hwp_answers != published_answers:
            mismatches = [
                number
                for number in range(1, spec.question_count + 1)
                if hwp_answers[number] != published_answers[number]
            ]
            raise BarExamValidationError(
                f"{spec.label} 공개 정답과 HWP 정답 불일치: {mismatches}"
            )

        prepared = [
            PreparedQuestion(
                number=number,
                text=problem_texts[number],
                correct_answer=hwp_answers[number],
                points=2.5,
            )
            for number in range(1, spec.question_count + 1)
        ]
        prepared_subjects.append((spec, prepared))
        source_records.extend(
            [
                {
                    "role": "questions",
                    "subject_id": spec.subject_id,
                    "filename": spec.problem_filename,
                    "sha256": problem_hash,
                },
                {
                    "role": "answers",
                    "subject_id": spec.subject_id,
                    "filename": spec.answer_filename,
                    "sha256": answer_hash,
                },
            ]
        )

    total_questions = sum(len(questions) for _, questions in prepared_subjects)
    total_points = sum(
        sum(question.points for question in questions)
        for _, questions in prepared_subjects
    )
    if total_questions != 150 or total_points != 375.0:
        raise BarExamValidationError(
            f"전체 합계 오류: {total_questions}문항, {total_points:g}점"
        )

    manifest = {
        "version": 1,
        "benchmark_id": "bar-exam-15",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_count": total_questions,
        "max_points": total_points,
        "subjects": [
            {
                "id": spec.subject_id,
                "label": spec.label,
                "question_count": len(questions),
                "max_points": sum(question.points for question in questions),
            }
            for spec, questions in prepared_subjects
        ],
        "sources": source_records,
    }

    if not check_only:
        for spec, questions in prepared_subjects:
            _write_subject(output_dir, spec, questions)
        _atomic_write_text(
            output_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    """
    @brief 명령행 파서 구성

    @return ArgumentParser
    """
    parser = argparse.ArgumentParser(
        description="제15회 변호사시험 HWP를 로컬 벤치마크 입력으로 변환합니다."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="6개 공식 HWP가 있는 ignored 디렉터리",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="로컬 문제 출력 디렉터리",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_METADATA_PATH,
        help="추적되는 공개 정답 메타데이터",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="문제 파일을 쓰지 않고 원문·정답·구조만 검증",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """
    @brief 변호사시험 준비 CLI 진입점

    @param argv 테스트용 명령행 인자
    @return 프로세스 종료 코드
    """
    args = _build_parser().parse_args(argv)
    try:
        manifest = prepare_benchmark(
            source_dir=args.source_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            metadata_path=args.metadata.resolve(),
            check_only=args.check_only,
        )
    except (BarExamValidationError, Hwp5Error, RuntimeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    action = "검증" if args.check_only else "변환"
    print(
        f"{action} 완료: {manifest['question_count']}문항, "
        f"{manifest['max_points']:g}점"
    )
    for subject in manifest["subjects"]:
        print(
            f"- {subject['label']}: {subject['question_count']}문항, "
            f"{subject['max_points']:g}점"
        )
    if not args.check_only:
        print(f"로컬 출력: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
