#!/usr/bin/env python3
"""
@brief git-ignored 벤치마크 입력(problems/<benchmark>)과 모델 설정을 하나로 패키징

problems/** 와 benchmark_models.json 은 저작권·자격증명 때문에 저장소에 추적되지
않습니다. 다른 장비로 옮기거나 백업할 때 이 스크립트로 무결성 매니페스트가 포함된
아카이브를 만듭니다. 만들어진 아카이브는 공개 배포용이 아닙니다.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any, Iterable
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "benchmarks" / "registry.json"
DEFAULT_BENCHMARK = "bar-exam-15"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist"
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
MANIFEST_VERSION = 1

# 어떤 조합에서도 담지 않는 잡동사니
ALWAYS_EXCLUDED = (
    "**/__pycache__/**",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/*.tmp",
    "**/*.bak",
    "**/~$*",
)
# 기본 제외: 원본 응답 로그는 수백 MB까지 커지므로 --include-raw 로만 담는다
RAW_PATTERN = "problems/*/.runner/raw/**"
# 자격증명이 들어 있을 만한 필드 이름 (정확히 일치하거나 이 접미사로 끝나는 경우만)
SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
SECRET_FIELD_SUFFIXES = ("_api_key", "_key", "_secret", "_token", "_password", "_credential")


class PackagingError(RuntimeError):
    """@brief 패키징 입력 검증 오류"""


def _load_json(path: Path) -> dict[str, Any]:
    """
    @brief JSON 객체를 UTF-8로 읽는다.

    @param path 입력 파일
    @return JSON 객체
    @throws PackagingError 파일이 없거나 최상위 값이 객체가 아닐 때
    """
    if not path.exists():
        raise PackagingError(f"파일이 없습니다: {_display_path(path)}")
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
    except json.JSONDecodeError as exc:
        raise PackagingError(f"JSON을 읽을 수 없습니다: {_display_path(path)} ({exc})") from exc
    if not isinstance(value, dict):
        raise PackagingError(f"JSON 객체가 필요합니다: {_display_path(path)}")
    return value


def _display_path(path: Path) -> str:
    """
    @brief 저장소 안이면 상대 경로, 밖이면 절대 경로로 표기

    @param path 대상 경로
    @return POSIX 형식 경로
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _format_size(size: int) -> str:
    """
    @brief 바이트 크기를 사람이 읽을 단위로 변환

    @param size 바이트 수
    @return "12.3 MB" 형태 문자열
    """
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _benchmark_problem_dir(registry_path: Path, benchmark_id: str) -> Path:
    """
    @brief 레지스트리의 problemDir 들에서 벤치마크 공통 입력 디렉터리를 구한다.

    @param registry_path 레지스트리 경로
    @param benchmark_id 벤치마크 ID
    @return problems/<benchmark> 절대 경로
    @throws PackagingError 벤치마크가 없거나 섹션 경로가 problems/ 밖일 때
    """
    registry = _load_json(registry_path)
    benchmarks = registry.get("benchmarks")
    if not isinstance(benchmarks, list):
        raise PackagingError(f"benchmarks 배열이 필요합니다: {_display_path(registry_path)}")

    for benchmark in benchmarks:
        if not isinstance(benchmark, dict) or benchmark.get("id") != benchmark_id:
            continue
        sections = benchmark.get("sections")
        if not isinstance(sections, list) or not sections:
            raise PackagingError(f"섹션이 없는 벤치마크입니다: {benchmark_id}")

        problems_root = (REPO_ROOT / "problems").resolve()
        parents: set[Path] = set()
        for section in sections:
            raw_dir = section.get("problemDir") if isinstance(section, dict) else None
            if not raw_dir:
                raise PackagingError(f"problemDir이 없는 섹션이 있습니다: {benchmark_id}")
            section_dir = (REPO_ROOT / raw_dir).resolve()
            try:
                section_dir.relative_to(problems_root)
            except ValueError as exc:
                raise PackagingError(
                    f"problemDir은 problems/ 아래여야 합니다: {raw_dir}"
                ) from exc
            parents.add(section_dir.parent)

        if len(parents) != 1:
            raise PackagingError(
                f"섹션들이 서로 다른 상위 디렉터리를 가리킵니다: {sorted(map(str, parents))}"
            )
        return parents.pop()

    known = ", ".join(str(item.get("id")) for item in benchmarks if isinstance(item, dict))
    raise PackagingError(f"벤치마크를 찾을 수 없습니다: {benchmark_id} (등록된 ID: {known})")


def _matches_any(relative_path: str, patterns: Iterable[str]) -> bool:
    """
    @brief 아카이브 상대 경로가 glob 패턴 중 하나에 걸리는지 판정

    "**/" 접두 패턴은 경로 중간뿐 아니라 최상위 이름에도 적용된다.

    @param relative_path POSIX 상대 경로
    @param patterns glob 패턴들
    @return 하나라도 일치하면 True
    """
    for pattern in patterns:
        if fnmatch.fnmatch(relative_path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(relative_path, pattern[3:]):
            return True
        # 디렉터리 패턴은 그 아래 전체를 제외한다
        if pattern.endswith("/**") and fnmatch.fnmatch(relative_path, pattern[:-3]):
            return True
    return False


def _collect_files(
    source_root: Path, prefix: str, excluded: Iterable[str]
) -> list[tuple[Path, str]]:
    """
    @brief 디렉터리를 재귀 순회하며 담을 파일 목록을 만든다.

    @param source_root 원본 디렉터리
    @param prefix 아카이브 안에서 쓸 상대 경로 접두사
    @param excluded 제외 glob 패턴
    @return (실제 경로, 아카이브 상대 경로) 목록, 경로 오름차순
    """
    patterns = tuple(excluded)
    collected: list[tuple[Path, str]] = []
    for current_dir, dir_names, file_names in os.walk(source_root):
        current = Path(current_dir)
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not _matches_any(
                f"{prefix}/{(current / name).relative_to(source_root).as_posix()}", patterns
            )
        )
        for name in sorted(file_names):
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = f"{prefix}/{path.relative_to(source_root).as_posix()}"
            if _matches_any(relative, patterns):
                continue
            collected.append((path, relative))
    return collected


def _sha256(path: Path) -> str:
    """
    @brief 파일의 SHA-256 해시 계산

    @param path 대상 파일
    @return 16진수 해시 문자열
    """
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    """
    @brief 현재 커밋 해시를 최선 노력으로 조회 (실패해도 패키징은 계속)

    @return 커밋 해시 또는 None
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    return completed.stdout.strip() or None


def _is_secret_field(key: str) -> bool:
    """
    @brief 필드 이름이 자격증명 이름 규칙에 해당하는지 판정

    max_output_tokens 처럼 우연히 겹치는 이름은 걸러낸다.

    @param key 필드 이름
    @return 자격증명으로 보이면 True
    """
    lowered = key.lower()
    return lowered in SECRET_FIELD_NAMES or lowered.endswith(SECRET_FIELD_SUFFIXES)


def _scan_secret_fields(config_path: Path) -> list[str]:
    """
    @brief 모델 설정에 자격증명처럼 보이는 문자열 필드가 있는지 검사

    @param config_path benchmark_models.json 경로
    @return "models[0].api_key" 형태의 의심 필드 경로 목록
    """
    try:
        config = _load_json(config_path)
    except PackagingError:
        return []

    findings: list[str] = []

    def _walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{trail}.{key}" if trail else str(key)
                if isinstance(value, str) and value and _is_secret_field(str(key)):
                    findings.append(path)
                _walk(value, path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                _walk(value, f"{trail}[{index}]")

    _walk(config, "")
    return findings


def _build_manifest(
    benchmark_id: str,
    entries: list[tuple[Path, str]],
    include_raw: bool,
    excluded: list[str],
) -> dict[str, Any]:
    """
    @brief 아카이브에 함께 넣을 무결성 매니페스트 생성

    @param benchmark_id 벤치마크 ID
    @param entries (실제 경로, 아카이브 상대 경로) 목록
    @param include_raw 원본 응답 로그 포함 여부
    @param excluded 적용된 제외 패턴
    @return 매니페스트 객체
    """
    files = [
        {
            "path": relative,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path, relative in entries
    ]
    return {
        "version": MANIFEST_VERSION,
        "benchmark_id": benchmark_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "include_raw": include_raw,
        "excluded_patterns": excluded,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "files": files,
        "notice": (
            "problems/** 는 저작권 대상 시험 원문을 포함하며 공개 배포 대상이 아닙니다. "
            "benchmark_models.json 은 로컬 실행 설정이므로 공유 전 내용을 확인하세요."
        ),
    }


def _write_tar(
    archive_path: Path, root_name: str, entries: list[tuple[Path, str]], manifest: bytes
) -> None:
    """
    @brief gzip tar 아카이브 작성 (소유자 정보는 재현성을 위해 초기화)

    @param archive_path 출력 파일
    @param root_name 아카이브 최상위 디렉터리 이름
    @param entries (실제 경로, 아카이브 상대 경로) 목록
    @param manifest 매니페스트 JSON 바이트
    """
    with tarfile.open(archive_path, "w:gz") as archive:
        for path, relative in entries:
            info = archive.gettarinfo(str(path), arcname=f"{root_name}/{relative}")
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with open(path, "rb") as file:
                archive.addfile(info, file)
        info = tarfile.TarInfo(name=f"{root_name}/{MANIFEST_NAME}")
        info.size = len(manifest)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(manifest))


def _write_zip(
    archive_path: Path, root_name: str, entries: list[tuple[Path, str]], manifest: bytes
) -> None:
    """
    @brief deflate zip 아카이브 작성

    @param archive_path 출력 파일
    @param root_name 아카이브 최상위 디렉터리 이름
    @param entries (실제 경로, 아카이브 상대 경로) 목록
    @param manifest 매니페스트 JSON 바이트
    """
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in entries:
            archive.write(path, arcname=f"{root_name}/{relative}")
        archive.writestr(f"{root_name}/{MANIFEST_NAME}", manifest)


def create_package(
    output_path: Path,
    problem_dir: Path,
    config_path: Path,
    benchmark_id: str = DEFAULT_BENCHMARK,
    archive_format: str = "tar.gz",
    include_raw: bool = False,
    extra_excludes: Iterable[str] = (),
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    @brief 벤치마크 입력과 모델 설정을 하나의 아카이브로 묶는다.

    @param output_path 출력 아카이브 경로
    @param problem_dir problems/<benchmark> 디렉터리
    @param config_path benchmark_models.json 경로
    @param benchmark_id 벤치마크 ID
    @param archive_format "tar.gz" 또는 "zip"
    @param include_raw .runner/raw 원본 응답 포함 여부
    @param extra_excludes 추가 제외 glob 패턴 (아카이브 상대 경로 기준)
    @param dry_run True이면 아카이브를 쓰지 않고 목록만 계산
    @return 요약 정보와 매니페스트를 담은 객체
    @throws PackagingError 입력이 없거나 담을 파일이 없을 때
    """
    if archive_format not in {"tar.gz", "zip"}:
        raise PackagingError(f"지원하지 않는 형식입니다: {archive_format}")
    if not problem_dir.is_dir():
        raise PackagingError(f"문제 디렉터리가 없습니다: {_display_path(problem_dir)}")
    if not config_path.is_file():
        raise PackagingError(f"모델 설정 파일이 없습니다: {_display_path(config_path)}")

    excluded = list(ALWAYS_EXCLUDED) + list(extra_excludes)
    if not include_raw:
        excluded.append(RAW_PATTERN.replace("problems/*", f"problems/{problem_dir.name}"))

    prefix = f"problems/{problem_dir.name}"
    entries = _collect_files(problem_dir, prefix, excluded)
    entries.append((config_path, config_path.name))
    entries.sort(key=lambda item: item[1])
    if len(entries) <= 1:
        raise PackagingError(f"담을 파일이 없습니다: {_display_path(problem_dir)}")

    manifest = _build_manifest(benchmark_id, entries, include_raw, excluded)
    root_name = output_path.name
    for suffix in (".tar.gz", ".tgz", ".zip"):
        if root_name.endswith(suffix):
            root_name = root_name[: -len(suffix)]
            break

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary_path = output_path.with_name(f".{output_path.name}.partial")
        try:
            if archive_format == "zip":
                _write_zip(temporary_path, root_name, entries, payload)
            else:
                _write_tar(temporary_path, root_name, entries, payload)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    return {
        "output_path": output_path,
        "root_name": root_name,
        "dry_run": dry_run,
        "archive_bytes": output_path.stat().st_size if not dry_run else 0,
        "manifest": manifest,
    }


def _default_output_path(benchmark_id: str, archive_format: str) -> Path:
    """
    @brief 타임스탬프가 붙은 기본 출력 경로 생성

    @param benchmark_id 벤치마크 ID
    @param archive_format 아카이브 형식
    @return dist/<benchmark>-package-<UTC 타임스탬프>.<확장자>
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"{benchmark_id}-package-{stamp}.{archive_format}"


def _print_summary(result: dict[str, Any], config_path: Path) -> None:
    """
    @brief 패키징 결과 요약을 표준 출력에 쓴다.

    @param result create_package 반환값
    @param config_path 모델 설정 경로
    """
    manifest = result["manifest"]
    label = "[dry-run] 담길 파일" if result["dry_run"] else "생성 완료"
    print(f"{label}: {manifest['file_count']}개 · {_format_size(manifest['total_bytes'])}")
    if not manifest["include_raw"]:
        print("  · .runner/raw 원본 응답은 제외했습니다 (--include-raw로 포함).")
    if result["dry_run"]:
        print(f"  · 예정 경로: {_display_path(result['output_path'])}")
    else:
        print(
            f"  · 아카이브: {_display_path(result['output_path'])} "
            f"({_format_size(result['archive_bytes'])})"
        )
        print(f"  · 매니페스트: {result['root_name']}/{MANIFEST_NAME}")

    sys.stdout.flush()
    secrets = _scan_secret_fields(config_path)
    if secrets:
        print(
            f"경고: {config_path.name}에 자격증명으로 보이는 필드가 있습니다 "
            f"({', '.join(secrets[:5])}). 공유 전 확인하세요.",
            file=sys.stderr,
        )
    print(
        "주의: 이 아카이브에는 저작권 대상 시험 원문이 들어 있으므로 공개 배포하지 마세요.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """
    @brief CLI 진입점

    @param argv 인자 목록 (None이면 sys.argv 사용)
    @return 종료 코드
    """
    parser = argparse.ArgumentParser(
        description="git-ignored 벤치마크 입력과 모델 설정을 아카이브로 패키징합니다."
    )
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK, help="벤치마크 ID")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="레지스트리 경로")
    parser.add_argument(
        "--problem-dir",
        type=Path,
        default=None,
        help="problems/<benchmark> 경로 (기본값은 레지스트리에서 유도)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "benchmark_models.json",
        help="모델 설정 파일 경로",
    )
    parser.add_argument("--output", type=Path, default=None, help="출력 아카이브 경로")
    parser.add_argument(
        "--format", dest="archive_format", choices=("tar.gz", "zip"), default="tar.gz",
        help="아카이브 형식",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help=".runner/raw 원본 응답까지 포함 (용량이 크게 늘어남)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="추가 제외 패턴 (아카이브 상대 경로 기준, 반복 지정 가능)",
    )
    parser.add_argument("--dry-run", action="store_true", help="쓰지 않고 목록만 출력")
    arguments = parser.parse_args(argv)

    try:
        problem_dir = (
            arguments.problem_dir.resolve()
            if arguments.problem_dir is not None
            else _benchmark_problem_dir(arguments.registry, arguments.benchmark)
        )
        output_path = arguments.output or _default_output_path(
            arguments.benchmark, arguments.archive_format
        )
        result = create_package(
            output_path=output_path,
            problem_dir=problem_dir,
            config_path=arguments.config,
            benchmark_id=arguments.benchmark,
            archive_format=arguments.archive_format,
            include_raw=arguments.include_raw,
            extra_excludes=arguments.exclude,
            dry_run=arguments.dry_run,
        )
    except PackagingError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1

    _print_summary(result, arguments.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
