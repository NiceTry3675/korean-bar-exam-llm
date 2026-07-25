"""@brief 벤치마크 입력 패키징 테스트"""

import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from package_bar_exam import (  # noqa: E402
    MANIFEST_NAME,
    PackagingError,
    _benchmark_problem_dir,
    _is_secret_field,
    create_package,
)


def _build_fixture(root: Path) -> tuple[Path, Path]:
    """
    @brief problems/<benchmark> 구조를 흉내 낸 임시 입력 생성

    @param root 임시 디렉터리
    @return (문제 디렉터리, 모델 설정 경로)
    """
    problem_dir = root / "problems" / "bar-exam-15"
    (problem_dir / "public-law").mkdir(parents=True)
    (problem_dir / "public-law" / "1.txt").write_text("문 1.\n", encoding="utf-8")
    (problem_dir / "public-law" / "results_verified.json").write_text("{}\n", encoding="utf-8")
    (problem_dir / "source").mkdir()
    (problem_dir / "source" / "원문.hwp").write_bytes(b"hwp")
    (problem_dir / ".runner" / "checkpoints" / "model-1234abcd").mkdir(parents=True)
    (problem_dir / ".runner" / "checkpoints" / "model-1234abcd" / "question.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (problem_dir / ".runner" / "raw" / "model-1234abcd" / "question").mkdir(parents=True)
    (problem_dir / ".runner" / "raw" / "model-1234abcd" / "question" / "1.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (problem_dir / "__pycache__").mkdir()
    (problem_dir / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (problem_dir / ".DS_Store").write_bytes(b"\x00")

    config_path = root / "benchmark_models.json"
    config_path.write_text(
        json.dumps({"models": [{"name": "m", "max_output_tokens": 4096}]}), encoding="utf-8"
    )
    return problem_dir, config_path


class PackageContentsTest(unittest.TestCase):
    """@brief 아카이브 구성과 제외 규칙 테스트"""

    def test_default_package_keeps_checkpoints_and_drops_raw(self):
        """@brief 기본 설정은 체크포인트를 담고 raw 응답과 잡파일은 제외"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem_dir, config_path = _build_fixture(root)
            archive_path = root / "package.tar.gz"

            create_package(
                output_path=archive_path,
                problem_dir=problem_dir,
                config_path=config_path,
            )

            with tarfile.open(archive_path) as archive:
                names = archive.getnames()

            self.assertIn("package/benchmark_models.json", names)
            self.assertIn("package/problems/bar-exam-15/public-law/1.txt", names)
            self.assertIn(
                "package/problems/bar-exam-15/.runner/checkpoints/model-1234abcd/question.json",
                names,
            )
            self.assertFalse([name for name in names if "/.runner/raw/" in name])
            self.assertFalse([name for name in names if "__pycache__" in name])
            self.assertFalse([name for name in names if name.endswith(".DS_Store")])

    def test_include_raw_and_extra_excludes(self):
        """@brief --include-raw는 원본 응답을 담고 --exclude는 지정 경로를 뺀다"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem_dir, config_path = _build_fixture(root)
            archive_path = root / "package.zip"

            create_package(
                output_path=archive_path,
                problem_dir=problem_dir,
                config_path=config_path,
                archive_format="zip",
                include_raw=True,
                extra_excludes=["problems/*/source/**"],
            )

            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()

            self.assertTrue([name for name in names if "/.runner/raw/" in name])
            self.assertFalse([name for name in names if "/source/" in name])

    def test_manifest_hashes_match_archive_members(self):
        """@brief 매니페스트의 SHA-256이 실제 파일과 일치"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem_dir, config_path = _build_fixture(root)
            archive_path = root / "package.tar.gz"

            result = create_package(
                output_path=archive_path,
                problem_dir=problem_dir,
                config_path=config_path,
            )

            with tarfile.open(archive_path) as archive:
                manifest = json.loads(
                    archive.extractfile(f"package/{MANIFEST_NAME}").read().decode("utf-8")
                )
                for record in manifest["files"]:
                    member = archive.extractfile(f"package/{record['path']}")
                    payload = member.read()
                    self.assertEqual(record["size"], len(payload))
                    self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())

            self.assertEqual(manifest["file_count"], result["manifest"]["file_count"])
            self.assertFalse(manifest["include_raw"])

    def test_dry_run_writes_nothing(self):
        """@brief dry-run은 목록만 계산하고 파일을 만들지 않음"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem_dir, config_path = _build_fixture(root)
            archive_path = root / "package.tar.gz"

            result = create_package(
                output_path=archive_path,
                problem_dir=problem_dir,
                config_path=config_path,
                dry_run=True,
            )

            self.assertFalse(archive_path.exists())
            self.assertTrue(result["dry_run"])
            self.assertGreater(result["manifest"]["file_count"], 0)

    def test_missing_inputs_raise(self):
        """@brief 문제 디렉터리나 설정 파일이 없으면 오류"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem_dir, config_path = _build_fixture(root)

            with self.assertRaises(PackagingError):
                create_package(
                    output_path=root / "a.tar.gz",
                    problem_dir=root / "없는-디렉터리",
                    config_path=config_path,
                )
            with self.assertRaises(PackagingError):
                create_package(
                    output_path=root / "b.tar.gz",
                    problem_dir=problem_dir,
                    config_path=root / "없는-설정.json",
                )
            with self.assertRaises(PackagingError):
                create_package(
                    output_path=root / "c.7z",
                    problem_dir=problem_dir,
                    config_path=config_path,
                    archive_format="7z",
                )


class RegistryLookupTest(unittest.TestCase):
    """@brief 레지스트리 기반 문제 디렉터리 추론 테스트"""

    def test_resolves_registered_benchmark(self):
        """@brief 등록된 벤치마크는 problems/<benchmark>로 해석"""
        problem_dir = _benchmark_problem_dir(
            REPO_ROOT / "benchmarks" / "registry.json", "bar-exam-15"
        )
        self.assertEqual(problem_dir, (REPO_ROOT / "problems" / "bar-exam-15").resolve())

    def test_unknown_benchmark_raises(self):
        """@brief 없는 벤치마크 ID는 오류"""
        with self.assertRaises(PackagingError):
            _benchmark_problem_dir(REPO_ROOT / "benchmarks" / "registry.json", "없는-벤치마크")


class SecretFieldTest(unittest.TestCase):
    """@brief 자격증명 필드 감지 테스트"""

    def test_flags_credential_names_only(self):
        """@brief 토큰 상한처럼 이름만 겹치는 필드는 걸러낸다"""
        for name in ("api_key", "apiKey", "client_secret", "access_token", "password"):
            self.assertTrue(_is_secret_field(name), name)
        for name in ("max_output_tokens", "oauth_profile", "model_id", "thinking_level"):
            self.assertFalse(_is_secret_field(name), name)


if __name__ == "__main__":
    unittest.main()
