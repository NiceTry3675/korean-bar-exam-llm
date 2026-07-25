"""@brief 변호사시험 분리·정답·공개 메타데이터 테스트"""

import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from hwp5_reader import HwpParagraph  # noqa: E402
from generate_questions_metadata import build_metadata  # noqa: E402
from prepare_bar_exam import (  # noqa: E402
    BarExamValidationError,
    DEFAULT_SOURCE_DIR,
    SOURCE_SPECS,
    extract_answer_key,
    prepare_benchmark,
    split_questions,
)


def _paragraph(text: str, level: int = 1) -> HwpParagraph:
    """
    @brief 합성 HWP 문단 생성

    @param text 문단 텍스트
    @param level 레코드 레벨
    @return HwpParagraph
    """
    return HwpParagraph(text=text, level=level, section=0)


class QuestionSplitTest(unittest.TestCase):
    """@brief 문제 표지와 표 셀 병합 테스트"""

    def test_keeps_table_cells_in_record_order(self):
        """@brief 레벨 3의 ㄱ·ㄴ 표 셀도 현재 문항에 포함"""
        paragraphs = [
            _paragraph("머리말"),
            _paragraph("문  1."),
            _paragraph("옳은 것을 고르시오."),
            _paragraph("ㄱ. 첫 번째 조건", level=3),
            _paragraph("ㄴ. 두 번째 조건", level=3),
            _paragraph("① 하나\t② 둘\t③ 셋\t④ 넷\t⑤ 다섯"),
            _paragraph("문 2. 옳지 않은 것은?"),
            _paragraph("① A"),
            _paragraph("② B"),
            _paragraph("③ C"),
            _paragraph("④ D"),
            _paragraph("⑤ E"),
            _paragraph("이하부터는 여백입니다"),
        ]

        questions = split_questions(paragraphs, expected_count=2)

        self.assertNotIn("머리말", questions[1])
        self.assertLess(questions[1].index("ㄱ."), questions[1].index("ㄴ."))
        self.assertIn("옳지 않은 것은?", questions[2])
        self.assertNotIn("여백입니다", questions[2])

    def test_rejects_missing_choice(self):
        """@brief 선택지 표지가 하나라도 없으면 실패"""
        paragraphs = [
            _paragraph("문 1."),
            _paragraph("① A ② B ③ C ④ D"),
        ]
        with self.assertRaisesRegex(BarExamValidationError, "선택지 표지 오류"):
            split_questions(paragraphs, expected_count=1)


class AnswerKeyTest(unittest.TestCase):
    """@brief 정답표 셀 파싱 테스트"""

    def test_reads_row_major_question_answer_pairs(self):
        """@brief 표 행 순서의 문항·정답 쌍을 번호 기준으로 복원"""
        paragraphs = [
            _paragraph("문번", 3),
            _paragraph("정답", 3),
            _paragraph("1", 3),
            _paragraph("5", 3),
            _paragraph("2", 3),
            _paragraph("3", 3),
        ]
        self.assertEqual(extract_answer_key(paragraphs, 2), {1: 5, 2: 3})

    def test_rejects_out_of_range_answer(self):
        """@brief 1~5 밖의 정답을 거부"""
        with self.assertRaisesRegex(BarExamValidationError, "범위 밖"):
            extract_answer_key([_paragraph("1"), _paragraph("6")], 1)


class PublicMetadataTest(unittest.TestCase):
    """@brief 추적되는 레지스트리와 정답 메타데이터 불변조건"""

    @classmethod
    def setUpClass(cls):
        """@brief JSON 파일을 한 번 로드"""
        cls.registry = json.loads(
            (REPO_ROOT / "benchmarks" / "registry.json").read_text(encoding="utf-8")
        )
        cls.metadata = json.loads(
            (REPO_ROOT / "benchmarks" / "bar-exam-15" / "questions.json").read_text(
                encoding="utf-8"
            )
        )

    def test_converter_rejects_output_outside_ignored_problems_tree(self):
        """@brief 본문을 저장소 밖이나 추적 가능한 경로에 쓰지 않는다."""
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BarExamValidationError, "ignored problems"):
                prepare_benchmark(
                    source_dir=REPO_ROOT,
                    output_dir=Path(directory),
                    check_only=False,
                )

    def test_registry_exposes_only_bar_exam(self):
        """@brief 변호사시험만 등록된 단일 벤치마크 구성을 검증"""
        benchmarks = {entry["id"]: entry for entry in self.registry["benchmarks"]}
        self.assertEqual(self.registry["defaultBenchmark"], "bar-exam-15")
        self.assertEqual(["bar-exam-15"], list(benchmarks))
        bar_exam = benchmarks["bar-exam-15"]
        self.assertTrue(bar_exam["navigation"]["visible"])
        self.assertEqual(bar_exam["scoring"]["maxScore"], 375)
        self.assertEqual(bar_exam["scoring"]["totalQuestions"], 150)
        self.assertEqual(
            [section["id"] for section in bar_exam["sections"]],
            ["public-law", "civil-law", "criminal-law"],
        )

    def test_public_metadata_has_only_structure_answers_and_provenance(self):
        """@brief 본문 없이 150개 정답과 375점 구조만 공개"""
        self.assertEqual(self.metadata["answer_status"], "final_confirmed")
        questions = [
            question
            for subject in self.metadata["subjects"]
            for question in subject["questions"]
        ]
        self.assertEqual(len(questions), 150)
        self.assertEqual(sum(question["points"] for question in questions), 375.0)
        self.assertTrue(all(1 <= question["correct_answer"] <= 5 for question in questions))
        self.assertTrue(
            all(set(question) == {"number", "correct_answer", "points"} for question in questions)
        )
        forbidden = {"text", "question_text", "question_path", "image_paths", "raw"}
        self.assertTrue(all(forbidden.isdisjoint(question) for question in questions))

    def test_subject_totals_are_exact(self):
        """@brief 과목별 40/70/40문항과 100/175/100점을 검증"""
        actual = [
            (
                subject["id"],
                len(subject["questions"]),
                sum(question["points"] for question in subject["questions"]),
            )
            for subject in self.metadata["subjects"]
        ]
        self.assertEqual(
            actual,
            [
                ("public-law", 40, 100.0),
                ("civil-law", 70, 175.0),
                ("criminal-law", 40, 100.0),
            ],
        )

    def test_official_source_and_answer_key_digests_are_locked(self):
        """@brief CI에서도 6개 원본 해시와 최종정답 배열의 우발 변경을 감지한다."""
        expected_source_hashes = {
            ("questions", spec.subject_id): spec.problem_sha256
            for spec in SOURCE_SPECS
        } | {
            ("answers", spec.subject_id): spec.answer_sha256
            for spec in SOURCE_SPECS
        }
        actual_source_hashes = {
            (entry["role"], entry["subject_id"]): entry["sha256"]
            for entry in self.metadata["provenance"]["source_files"]
        }
        self.assertEqual(actual_source_hashes, expected_source_hashes)

        answer_digests = {}
        for subject in self.metadata["subjects"]:
            serialized = "\n".join(
                f"{question['number']}:{question['correct_answer']}:{question['points']}"
                for question in subject["questions"]
            )
            answer_digests[subject["id"]] = hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest()
        self.assertEqual(answer_digests, {
            "public-law": "03319275d07edf012a5873f9b2497a792d82606aa3ca48737a4d9f3b630dde5a",
            "civil-law": "9c0c783793eeb3456f3060c8aeeeec7f0ef5033e225d78dad30f8caa5342b87d",
            "criminal-law": "b9bad7b7667121da158ad3402d97bf67f33c25fb8d1fa53c39cd40b0ad281f36",
        })

    def test_web_question_metadata_uses_registry_sections(self):
        """@brief 공개 정답 구조에서 본문 없이 웹 배점 메타데이터를 만든다."""
        benchmark = next(
            item for item in self.registry['benchmarks']
            if item['id'] == 'bar-exam-15'
        )
        metadata = build_metadata(benchmark)
        self.assertEqual(sum(len(items) for items in metadata.values()), 150)
        self.assertTrue(
            all(
                not question['hasImage'] and question['points'] == 2.5
                for items in metadata.values()
                for question in items.values()
            )
        )


@unittest.skipUnless(
    os.environ.get("BAR_EXAM_LOCAL_INTEGRATION") == "1",
    "BAR_EXAM_LOCAL_INTEGRATION=1일 때만 공식 로컬 HWP 6개를 검증합니다.",
)
class LocalHwpIntegrationTest(unittest.TestCase):
    """@brief 저장소 루트에 있는 공식 HWP 6개 통합 검증"""

    def test_official_sources_match_public_metadata(self):
        """@brief 해시·문항·표 선택지·정답·배점을 모두 대조"""
        manifest = prepare_benchmark(
            source_dir=DEFAULT_SOURCE_DIR,
            output_dir=REPO_ROOT / "problems" / "bar-exam-15",
            check_only=True,
        )
        self.assertEqual(manifest["question_count"], 150)
        self.assertEqual(manifest["max_points"], 375.0)
        self.assertEqual(len(manifest["sources"]), 6)


if __name__ == "__main__":
    unittest.main()
