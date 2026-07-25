"""
@brief Focused, network-free tests for benchmark_runner.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmark_runner import (
    AtomicJsonStore,
    BenchmarkRunner,
    ConfigurationError,
    ContextLimitError,
    ModelConfig,
    GoogleAdapter,
    OpenAICompatibleAdapter,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
    build_requests,
    estimate_input_tokens,
    load_benchmark,
    load_model_configs,
    parse_question_answer,
    parse_subject_answers,
)


class FakeAdapter(ProviderAdapter):
    """@brief Deterministic adapter that never accesses the network."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def send(self, prompt: str) -> ProviderResponse:
        """@brief Return or raise the next configured outcome."""
        self.calls.append(prompt)
        if not self.outcomes:
            raise AssertionError("Unexpected fake provider call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class WorkspaceFixture:
    """@brief Build a minimal registry and ignored-style local problem tree."""

    def __init__(self, question_count: int = 2, context_window: int = 10000):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.problem_dir = self.root / "problems" / "bar-exam-15" / "public-law"
        self.problem_dir.mkdir(parents=True)
        benchmark_dir = self.root / "benchmarks" / "bar-exam-15"
        benchmark_dir.mkdir(parents=True)

        questions = [
            {
                "number": number,
                "correct_answer": number if number <= 5 else 1,
                "points": 2.5,
                "type": "multiple_choice",
                "choices": [1, 2, 3, 4, 5],
            }
            for number in range(1, question_count + 1)
        ]
        public_metadata = {
            "schema_version": 1,
            "benchmark_id": "bar-exam-15",
            "subjects": [
                {
                    "id": "public-law",
                    "label": {"ko": "공법", "en": "Public Law"},
                    "sheet_name": "공법",
                    "question_count": question_count,
                    "max_points": question_count * 2.5,
                    "questions": questions,
                }
            ],
        }
        self._write_json(benchmark_dir / "questions.json", public_metadata)

        local_questions = []
        for question in questions:
            path = self.problem_dir / f"{question['number']}.txt"
            path.write_text(
                f"문제 {question['number']} 본문\n① 선택지 1\n② 선택지 2\n③ 선택지 3\n④ 선택지 4\n⑤ 선택지 5\n",
                encoding="utf-8",
            )
            local_questions.append(
                {
                    **question,
                    "question_path": str(path.relative_to(self.root)),
                    "image_paths": [],
                }
            )
        self._write_json(
            self.problem_dir / "questions.json",
            {"subject": "공법", "section": "공법", "questions": local_questions},
        )

        registry = {
            "version": 1,
            "benchmarks": [
                {
                    "id": "bar-exam-15",
                    "title": {"ko": "제15회 변호사시험", "en": "15th Bar Exam"},
                    "navigation": {"visible": False, "visibleWhenResults": True},
                    "scoring": {
                        "type": "sum",
                        "maxScore": question_count * 2.5,
                        "totalQuestions": question_count,
                        "pointsPerQuestion": 2.5,
                    },
                    "modes": {
                        "default": {"results": "bar_results.json"},
                        "hard": {"results": "bar_hard_results.json"},
                    },
                    "sections": [
                        {
                            "id": "public-law",
                            "sheet": "공법",
                            "subject": "공법",
                            "section": "공법",
                            "questionCount": question_count,
                            "maxScore": question_count * 2.5,
                            "problemDir": str(self.problem_dir.relative_to(self.root)),
                            "metadataPath": "benchmarks/bar-exam-15/questions.json",
                        }
                    ],
                }
            ],
        }
        self.registry_path = self.root / "benchmarks" / "registry.json"
        self._write_json(self.registry_path, registry)

        config = {
            "models": [
                {
                    "name": "Fake Model",
                    "provider": "openai-compatible",
                    "model_id": "fake-model",
                    "api_key_env": "FAKE_API_KEY",
                    "context_window": context_window,
                    "max_output_tokens": 100,
                    "max_retries": 2,
                    "input_cost_per_million": 1.0,
                    "output_cost_per_million": 2.0,
                }
            ]
        }
        self.config_path = self.root / "benchmark_models.json"
        self._write_json(self.config_path, config)

    def close(self):
        """@brief Release the temporary fixture tree."""
        self._temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value) -> None:
        """@brief Write one test fixture JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class AnswerParserTests(unittest.TestCase):
    """@brief Verify strict single-question and whole-subject answer contracts."""

    def test_question_accepts_final_marker_and_circled_digit(self):
        parsed = parse_question_answer("간단한 설명\n정답: ③")
        self.assertEqual("answered", parsed.status)
        self.assertEqual(3, parsed.answers[0])

    def test_question_rejects_ambiguous_markers(self):
        parsed = parse_question_answer("정답: 2\n정답: 4")
        self.assertEqual("parse_failed", parsed.status)

    def test_question_lenient_accepts_repeated_identical_markers(self):
        parsed = parse_question_answer("정답: 2\n정답: 2", strict=False)
        self.assertEqual("answered", parsed.status)
        self.assertEqual(2, parsed.answers[0])

    def test_question_lenient_accepts_prose_final_answer(self):
        parsed = parse_question_answer(
            "따라서 옳은 지문은 ㄱ, ㄴ이며, 정답은 **③**입니다.", strict=False
        )
        self.assertEqual("answered", parsed.status)
        self.assertEqual(3, parsed.answers[0])
        parsed = parse_question_answer("검토를 마친다.\n\n**정답: ①**", strict=False)
        self.assertEqual("answered", parsed.status)
        self.assertEqual(1, parsed.answers[0])
        parsed = parse_question_answer("옳지 않은 것은 ㄷ뿐이므로, 정답은 **①**이다.", strict=False)
        self.assertEqual("answered", parsed.status)
        self.assertEqual(1, parsed.answers[0])

    def test_question_lenient_rejects_disagreeing_prose_markers(self):
        parsed = parse_question_answer(
            "우선 정답은 2로 보이지만, 다시 보면 정답은 4입니다.", strict=False
        )
        self.assertEqual("parse_failed", parsed.status)

    def test_question_default_strict_matches_v1_rules(self):
        parsed = parse_question_answer("따라서 옳은 지문은 ㄱ, ㄴ이며, 정답은 **③**입니다.")
        self.assertEqual("parse_failed", parsed.status)
        parsed = parse_question_answer("정답: 2\n정답: 2")
        self.assertEqual("parse_failed", parsed.status)
        parsed = parse_question_answer("정답: 2")
        self.assertEqual("answered", parsed.status)
        self.assertEqual(2, parsed.answers[0])

    def test_question_lenient_prose_marker_ignores_negated_statement(self):
        parsed = parse_question_answer("정답은 3이 아니라고 단정하기 어렵다.", strict=False)
        self.assertEqual("parse_failed", parsed.status)
        parsed = parse_question_answer(
            "정답은 3번이 아니라 4번이라는 견해도 있다.", strict=False
        )
        self.assertEqual("parse_failed", parsed.status)

    def test_question_distinguishes_empty_refusal_and_parse_failure(self):
        self.assertEqual("no_answer", parse_question_answer("  ").status)
        self.assertEqual("refusal", parse_question_answer("이 요청에는 답변할 수 없습니다.").status)
        self.assertEqual("parse_failed", parse_question_answer("아마 세 번째 같습니다.").status)

    def test_subject_requires_every_number_exactly_once(self):
        parsed = parse_subject_answers("1: ①\n문항 2: 2번", [1, 2])
        self.assertEqual("answered", parsed.status)
        self.assertEqual({1: 1, 2: 2}, parsed.answers)
        self.assertEqual("parse_failed", parse_subject_answers("1: 1", [1, 2]).status)
        self.assertEqual(
            "parse_failed", parse_subject_answers("1: 1\n1: 1\n2: 2", [1, 2]).status
        )


class ConfigurationTests(unittest.TestCase):
    """@brief Verify registry, local-content, secret, and context validation."""

    def test_loads_exact_registry_schema_and_local_questions(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        benchmark = load_benchmark(fixture.registry_path, "bar-exam-15", fixture.root)
        models = load_model_configs(fixture.config_path)
        requests = build_requests(benchmark, models, "question", fixture.root)
        self.assertEqual(2, len(requests))
        self.assertEqual("public-law-q001", requests[0].request_key)
        self.assertNotIn("문제 1 본문", json.dumps(requests[0].model.public_dict()))

    def test_missing_registry_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigurationError, "Create benchmarks/registry.json"):
                load_benchmark(Path(directory) / "missing.json", "bar-exam-15", Path(directory))

    def test_registry_problem_dir_outside_repository_is_rejected(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        registry = json.loads(fixture.registry_path.read_text(encoding="utf-8"))
        registry["benchmarks"][0]["sections"][0]["problemDir"] = "../outside"
        fixture._write_json(fixture.registry_path, registry)
        with self.assertRaisesRegex(ConfigurationError, "ignored problems"):
            load_benchmark(fixture.registry_path, "bar-exam-15", fixture.root)

    def test_literal_api_key_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "literal api_key values are forbidden"):
            ModelConfig.from_dict(
                {
                    "name": "Unsafe",
                    "provider": "google",
                    "model_id": "unsafe",
                    "api_key_env": "GOOGLE_API_KEY",
                    "api_key": "secret",
                    "context_window": 1000,
                }
            )

    def test_env_files_load_without_overriding_real_environment(self):
        from benchmark_runner import _load_env_files

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                '# comment\n'
                'FROM_ENV="base"\n'
                'SHARED_KEY=env-value\n'
                'PRESET_KEY=file-value\n'
                'invalid line\n',
                encoding="utf-8",
            )
            (root / ".env.local").write_text(
                "export SHARED_KEY='local-value'\nLOCAL_ONLY=1\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {"PRESET_KEY": "shell-value"}, clear=True):
                _load_env_files(root)
                self.assertEqual("base", os.environ["FROM_ENV"])
                self.assertEqual("local-value", os.environ["SHARED_KEY"])
                self.assertEqual("1", os.environ["LOCAL_ONLY"])
                self.assertEqual("shell-value", os.environ["PRESET_KEY"])

    def test_vertexai_flag_requires_google_and_keeps_fingerprint(self):
        base = {
            "name": "Gemini Express",
            "provider": "google",
            "model_id": "gemini-3-pro",
            "api_key_env": "VERTEX_EXPRESS_API_KEY",
            "context_window": 1000000,
            "max_output_tokens": 4096,
        }
        express = ModelConfig.from_dict({**base, "vertexai": True})
        self.assertTrue(express.vertexai)
        self.assertTrue(express.public_dict()["vertexai"])

        plain = ModelConfig.from_dict(base)
        self.assertFalse(plain.vertexai)
        # vertexai=False는 public_dict에 나타나지 않아 기존 fingerprint를 보존한다.
        self.assertNotIn("vertexai", plain.public_dict())
        self.assertNotEqual(plain.fingerprint(), express.fingerprint())

        with self.assertRaisesRegex(ConfigurationError, "google provider"):
            ModelConfig.from_dict(
                {
                    "name": "Bad",
                    "provider": "openai-compatible",
                    "model_id": "gpt-x",
                    "api_key_env": "OPENAI_API_KEY",
                    "context_window": 1000,
                    "max_output_tokens": 500,
                    "vertexai": True,
                }
            )
        with self.assertRaisesRegex(ConfigurationError, "boolean"):
            ModelConfig.from_dict({**base, "vertexai": "yes"})

    def test_vertex_adc_mode_allows_missing_api_key_env(self):
        base = {
            "name": "Gemini Vertex ADC",
            "provider": "google",
            "vertexai": True,
            "vertex_project": "my-project",
            "model_id": "gemini-3-pro",
            "context_window": 1000000,
            "max_output_tokens": 4096,
        }
        adc = ModelConfig.from_dict(base)
        self.assertIsNone(adc.api_key_env)
        self.assertEqual("my-project", adc.vertex_project)
        self.assertEqual("my-project", adc.public_dict()["vertex_project"])
        self.assertEqual("global", adc.public_dict()["vertex_location"])

        with self.assertRaisesRegex(ConfigurationError, "not both"):
            ModelConfig.from_dict({**base, "api_key_env": "GOOGLE_API_KEY"})
        with self.assertRaisesRegex(ConfigurationError, "vertexai: true"):
            ModelConfig.from_dict(
                {
                    **{k: v for k, v in base.items() if k != "vertexai"},
                    "api_key_env": "GOOGLE_API_KEY",
                }
            )
        with self.assertRaisesRegex(ConfigurationError, "api_key_env"):
            ModelConfig.from_dict(
                {k: v for k, v in base.items() if k != "vertex_project"}
            )

    def test_google_thinking_level_is_validated_and_sent(self):
        model = ModelConfig.from_dict(
            {
                "name": "Gemini high",
                "provider": "google",
                "model_id": "gemini-3.1-pro-preview",
                "api_key_env": "GOOGLE_API_KEY",
                "context_window": 1_000_000,
                "max_output_tokens": 65_536,
                "thinking_level": "HIGH",
            }
        )
        self.assertEqual("high", model.thinking_level)
        self.assertEqual("high", model.public_dict()["thinking_level"])

        captured = {}
        google_response = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason="STOP")],
            prompt_feedback=None,
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                total_token_count=15,
                candidates_token_count=2,
                thoughts_token_count=3,
            ),
            text="정답: 1",
        )
        google_adapter = GoogleAdapter.__new__(GoogleAdapter)
        google_adapter._config = model
        google_adapter._types = SimpleNamespace(
            ThinkingConfig=lambda **kwargs: {"thinking": kwargs},
            GenerateContentConfig=lambda **kwargs: kwargs,
        )

        def generate_content(**kwargs):
            captured.update(kwargs)
            return google_response

        google_adapter._client = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )
        response = google_adapter.send("prompt")
        self.assertEqual(
            {"thinking": {"thinking_level": "high"}},
            captured["config"]["thinking_config"],
        )
        self.assertEqual(5, response.output_tokens)

        with self.assertRaisesRegex(ConfigurationError, "only by the google"):
            ModelConfig.from_dict(
                {
                    "name": "Bad",
                    "provider": "openai-compatible",
                    "model_id": "gpt-x",
                    "api_key_env": "OPENAI_API_KEY",
                    "context_window": 10_000,
                    "max_output_tokens": 100,
                    "thinking_level": "high",
                }
            )

    def test_subject_context_limit_fails_without_splitting(self):
        fixture = WorkspaceFixture(context_window=101)
        self.addCleanup(fixture.close)
        benchmark = load_benchmark(fixture.registry_path, "bar-exam-15", fixture.root)
        models = load_model_configs(fixture.config_path)
        with self.assertRaisesRegex(ContextLimitError, "Subject requests are never split"):
            build_requests(benchmark, models, "subject", fixture.root)

    def test_token_estimate_uses_conservative_utf8_byte_bound(self):
        self.assertEqual(3, estimate_input_tokens("abc"))
        self.assertEqual(6, estimate_input_tokens("한글"))

    def test_provider_filter_reasons_are_normalized_as_refusals(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        model = load_model_configs(fixture.config_path)[0]

        openai_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="", refusal=None),
                finish_reason="content_filter",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=0),
        )
        openai_adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
        openai_adapter._config = model
        openai_adapter._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: openai_response)
            )
        )
        self.assertTrue(openai_adapter.send("prompt").refusal)

        google_response = SimpleNamespace(
            candidates=[],
            prompt_feedback=SimpleNamespace(block_reason="SPII"),
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                total_token_count=10,
                candidates_token_count=0,
                thoughts_token_count=0,
            ),
            text="",
        )
        google_adapter = GoogleAdapter.__new__(GoogleAdapter)
        google_adapter._config = model
        google_adapter._types = SimpleNamespace(
            GenerateContentConfig=lambda **kwargs: kwargs
        )
        google_adapter._client = SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **_kwargs: google_response)
        )
        self.assertTrue(google_adapter.send("prompt").refusal)

    def test_composite_benchmark_does_not_sum_alternative_section_maxima(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        registry = json.loads(fixture.registry_path.read_text())
        registry["benchmarks"][0]["scoring"]["type"] = "composite"
        registry["benchmarks"][0]["scoring"]["maxScore"] = 450
        WorkspaceFixture._write_json(fixture.registry_path, registry)
        benchmark = load_benchmark(fixture.registry_path, "bar-exam-15", fixture.root)
        self.assertEqual("composite", benchmark.scoring_type)
        self.assertEqual(450, benchmark.max_score)


class RunnerTests(unittest.TestCase):
    """@brief Verify dry-run safety, retry policy, resume, and handoff artifacts."""

    def _runner(self, fixture: WorkspaceFixture, adapter: FakeAdapter, sleeps=None):
        """@brief Construct a runner that can only use the supplied fake adapter."""
        sleeps = sleeps if sleeps is not None else []
        return BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            adapter_factory=lambda _model, _key: adapter,
            sleep_fn=sleeps.append,
            random_fn=lambda: 0.0,
        )

    def test_dry_run_never_reads_key_or_builds_adapter_or_writes_state(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        built = []
        runner = BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            adapter_factory=lambda _model, _key: built.append(True),
        )
        with patch.dict(os.environ, {}, clear=True):
            report = runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
            )
        self.assertTrue(report.dry_run)
        self.assertEqual(2, report.planned_requests)
        self.assertEqual([], built)
        self.assertNotIn("prompt_preview", report.preview[0])
        self.assertNotIn("문제 1 본문", json.dumps(report.preview, ensure_ascii=False))
        self.assertFalse((fixture.root / "problems" / "bar-exam-15" / ".runner").exists())

    def test_prompt_preview_requires_explicit_opt_in(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        report = self._runner(fixture, FakeAdapter([])).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            include_prompt_preview=True,
        )
        self.assertIn("문제 1 본문", report.preview[0]["prompt_preview"])

    def test_execute_requires_explicit_cap_before_adapter_creation(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        built = []
        runner = BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            adapter_factory=lambda _model, _key: built.append(True),
        )
        with self.assertRaisesRegex(ConfigurationError, "requires --max-requests"):
            runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
            )
        self.assertEqual([], built)

    def test_question_execution_writes_raw_checkpoint_and_verified_handoff(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter(
            [
                ProviderResponse("풀이\n정답: 1", 20, 10, "stop", raw={"secret_raw": "first"}),
                ProviderResponse("정답: 2", 21, 11, "stop", raw={"secret_raw": "second"}),
            ]
        )
        runner = self._runner(fixture, adapter)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(2, report.completed)
        self.assertEqual(2, report.provider_attempts)
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        self.assertEqual(5.0, verified["model_scores"]["Fake Model"])
        self.assertEqual(2, verified["correct_count"])
        self.assertEqual(0, verified["manual_review_count"])
        self.assertEqual(
            {"input_per_million": 1.0, "output_per_million": 2.0},
            verified["run_metadata"]["Fake Model"]["pricing"],
        )
        self.assertNotIn("raw_response", json.dumps(verified))
        self.assertNotIn("secret_raw", json.dumps(verified))
        raw_files = list(
            (fixture.root / "problems" / "bar-exam-15" / ".runner" / "raw").rglob("*.json")
        )
        self.assertEqual(2, len(raw_files))
        self.assertIn("secret_raw", raw_files[0].read_text())

    def test_execution_records_lenient_scores_alongside_official(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter(
            [
                ProviderResponse("풀이\n정답: 1", 20, 10, "stop", raw={}),
                ProviderResponse("따라서 정답은 **2**입니다.", 21, 11, "stop", raw={}),
            ]
        )
        runner = self._runner(fixture, adapter)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
            )
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        self.assertEqual(2, verified["schema_version"])
        self.assertEqual(2.5, verified["model_scores"]["Fake Model"])
        self.assertEqual(5.0, verified["model_scores_lenient"]["Fake Model"])
        metrics = verified["model_metrics"]["Fake Model"]
        self.assertEqual(5.0, metrics["lenient_score"])
        self.assertEqual(1, metrics["lenient_rescued_count"])
        rescued = [
            row for row in verified["results"] if row["question_number"] == 2
        ][0]
        self.assertEqual("parse_failed", rescued["answer_status"])
        self.assertEqual("answered", rescued["lenient_status"])
        self.assertEqual(2, rescued["lenient_answer"])
        self.assertTrue(rescued["lenient_is_correct"])

    def test_backfill_lenient_fills_legacy_checkpoint_from_raw(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter(
            [
                ProviderResponse("풀이\n정답: 1", 20, 10, "stop", raw={}),
                ProviderResponse("따라서 정답은 **2**입니다.", 21, 11, "stop", raw={}),
            ]
        )
        runner = self._runner(fixture, adapter)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
            )
        checkpoint_path = next(
            (fixture.root / "problems" / "bar-exam-15" / ".runner" / "checkpoints").rglob(
                "question.json"
            )
        )
        checkpoint = json.loads(checkpoint_path.read_text())
        for entry in checkpoint["entries"].values():
            for row in entry["results"]:
                row.pop("lenient_answer", None)
                row.pop("lenient_status", None)
                row.pop("lenient_is_correct", None)
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False))

        summary = runner.backfill_lenient(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
        )
        self.assertEqual(1, summary["Fake Model"]["lenient_rescued"])
        self.assertEqual(0, summary["Fake Model"]["raw_missing"])
        refilled = json.loads(checkpoint_path.read_text())
        rows = [
            row
            for entry in refilled["entries"].values()
            for row in entry["results"]
        ]
        self.assertTrue(all("lenient_status" in row for row in rows))
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        self.assertEqual(5.0, verified["model_scores_lenient"]["Fake Model"])

    def test_parallel_execution_preserves_every_checkpoint_entry(self):
        fixture = WorkspaceFixture(question_count=4)
        self.addCleanup(fixture.close)
        adapter = FakeAdapter(
            [
                ProviderResponse("정답: 1", 20, 10, "stop")
                for _ in range(4)
            ]
        )
        runner = self._runner(fixture, adapter)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=4,
                workers=3,
            )
        self.assertEqual(4, report.completed)
        self.assertEqual(4, report.provider_attempts)
        checkpoint_files = list(
            (
                fixture.root
                / "problems"
                / "bar-exam-15"
                / ".runner"
                / "checkpoints"
            ).rglob("question.json")
        )
        self.assertEqual(1, len(checkpoint_files))
        checkpoint = json.loads(checkpoint_files[0].read_text())
        self.assertEqual(4, len(checkpoint["entries"]))

    def test_subject_mode_is_one_request_and_uses_hard_handoff(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter([ProviderResponse("1: 1\n2: 2", 40, 10, "end_turn")])
        runner = self._runner(fixture, adapter)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=1,
            )
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, report.completed)
        self.assertTrue((fixture.problem_dir / "hard_results_verified.json").exists())
        self.assertFalse((fixture.problem_dir / "results_verified.json").exists())

    def test_output_dir_inside_repo_must_stay_under_problems(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter([])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            with self.assertRaisesRegex(ConfigurationError, "must be under.*ignored problems"):
                self._runner(fixture, adapter).run(
                    benchmark_id="bar-exam-15",
                    config_path=fixture.config_path,
                    run_mode="subject",
                    execute=True,
                    max_requests=1,
                    output_dir=Path("raw-output"),
                )
        self.assertEqual([], adapter.calls)

        with tempfile.TemporaryDirectory() as external_directory:
            with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
                with self.assertRaisesRegex(ConfigurationError, "this repository's ignored problems"):
                    self._runner(fixture, adapter).run(
                        benchmark_id="bar-exam-15",
                        config_path=fixture.config_path,
                        run_mode="subject",
                        execute=True,
                        max_requests=1,
                        output_dir=Path(external_directory),
                    )
        self.assertEqual([], adapter.calls)

    def test_retries_429_but_not_400(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        sleeps = []
        adapter = FakeAdapter(
            [
                ProviderError("limited", retryable=True, status_code=429),
                ProviderResponse("1: 1\n2: 2", 40, 10, "stop"),
            ]
        )
        runner = self._runner(fixture, adapter, sleeps)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(2, report.provider_attempts)
        self.assertEqual([1.0], sleeps)
        self.assertEqual(1, report.rate_limit_errors)
        self.assertEqual(1, report.retryable_errors)
        self.assertAlmostEqual(0.00006, report.charged_or_reserved_cost_usd)

        fixture_two = WorkspaceFixture()
        self.addCleanup(fixture_two.close)
        not_retryable = FakeAdapter([ProviderError("bad request", retryable=False, status_code=400)])
        runner_two = self._runner(fixture_two, not_retryable, [])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            failed = runner_two.run(
                benchmark_id="bar-exam-15",
                config_path=fixture_two.config_path,
                run_mode="subject",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(1, failed.provider_attempts)
        self.assertEqual(1, failed.failed)

    def test_rate_limit_sleep_releases_shared_lock(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        config = json.loads(fixture.config_path.read_text())
        config["models"][0]["requests_per_minute"] = 4
        WorkspaceFixture._write_json(fixture.config_path, config)
        model = load_model_configs(fixture.config_path)[0]
        runner = self._runner(fixture, FakeAdapter([]))
        lock_was_available = []

        def inspect_sleep(_delay):
            acquired = runner._rate_limit_lock.acquire(blocking=False)
            lock_was_available.append(acquired)
            if acquired:
                runner._rate_limit_lock.release()
            raise RuntimeError("stop after observing the wait")

        runner._sleep = inspect_sleep
        runner._last_request_at[model.name] = 10**18
        with self.assertRaisesRegex(RuntimeError, "stop after observing"):
            runner._respect_rate_limit(model)
        self.assertEqual([True], lock_was_available)

    def test_retries_503(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter([
            ProviderError("unavailable", retryable=True, status_code=503),
            ProviderResponse("1: 1\n2: 2", 10, 5, "stop"),
        ])
        sleeps = []
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = self._runner(fixture, adapter, sleeps).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(2, report.provider_attempts)
        self.assertEqual(1, report.completed)
        self.assertEqual([1.0], sleeps)

    def test_retries_timeout_but_not_generic_connection_error(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        timeout_adapter = FakeAdapter(
            [TimeoutError("timed out"), ProviderResponse("1: 1\n2: 2", 10, 5, "stop")]
        )
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            recovered = self._runner(fixture, timeout_adapter, []).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(2, recovered.provider_attempts)
        self.assertEqual(1, recovered.completed)

        fixture_two = WorkspaceFixture()
        self.addCleanup(fixture_two.close)
        connection_adapter = FakeAdapter([ConnectionError("disconnected")])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            failed = self._runner(fixture_two, connection_adapter, []).run(
                benchmark_id="bar-exam-15",
                config_path=fixture_two.config_path,
                run_mode="subject",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(1, failed.provider_attempts)
        self.assertEqual(1, failed.failed)

    def test_resume_skips_success_and_retry_failed_only_retries_request_errors(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        failing = FakeAdapter([ProviderError("bad request", retryable=False, status_code=400)])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            first = self._runner(fixture, failing).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=1,
            )
        self.assertEqual(1, first.failed)

        unused = FakeAdapter([])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            second = self._runner(fixture, unused).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=1,
            )
        self.assertEqual(1, second.skipped)
        self.assertEqual([], unused.calls)

        succeeding = FakeAdapter([ProviderResponse("1: 1\n2: 2", 10, 5, "stop")])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            third = self._runner(fixture, succeeding).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=1,
                retry_failed=True,
            )
        self.assertEqual(1, third.completed)
        self.assertEqual(1, len(succeeding.calls))

    def test_no_resume_keeps_all_entries_from_the_new_run(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter(
            [ProviderResponse("정답: 1", 5, 2, "stop"), ProviderResponse("정답: 2", 5, 2, "stop")]
        )
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = self._runner(fixture, adapter).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
                resume=False,
            )
        self.assertEqual(2, report.completed)
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        self.assertEqual([1, 2], [item["question_number"] for item in verified["results"]])

    def test_changed_sources_exclude_stale_checkpoint_entries(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            self._runner(
                fixture,
                FakeAdapter([
                    ProviderResponse("정답: 1", 5, 2, "stop"),
                    ProviderResponse("정답: 2", 5, 2, "stop"),
                ]),
            ).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
            )

            for number in (1, 2):
                path = fixture.problem_dir / f"{number}.txt"
                path.write_text(path.read_text(encoding="utf-8") + "\n변경된 본문\n", encoding="utf-8")

            resumed = self._runner(
                fixture,
                FakeAdapter([ProviderResponse("정답: 1", 5, 2, "stop")]),
            ).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
            )

        self.assertIsNotNone(resumed.stopped_reason)
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        self.assertEqual([1], [item["question_number"] for item in verified["results"]])
        self.assertEqual(1, verified["total_verified"])

    def test_answer_key_change_regrades_resumed_checkpoint_without_provider_call(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            self._runner(
                fixture, FakeAdapter([ProviderResponse("정답: 1", 5, 2, "stop")])
            ).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
            )

            public_path = fixture.root / "benchmarks" / "bar-exam-15" / "questions.json"
            public_data = json.loads(public_path.read_text(encoding="utf-8"))
            public_data["subjects"][0]["questions"][0]["correct_answer"] = 2
            fixture._write_json(public_path, public_data)
            local_path = fixture.problem_dir / "questions.json"
            local_data = json.loads(local_path.read_text(encoding="utf-8"))
            local_data["questions"][0]["correct_answer"] = 2
            fixture._write_json(local_path, local_data)

            unused = FakeAdapter([])
            resumed = self._runner(fixture, unused).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
            )

        self.assertEqual(1, resumed.skipped)
        self.assertEqual([], unused.calls)
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        self.assertEqual(0, verified["model_scores"]["Fake Model"])
        self.assertEqual(2, verified["results"][0]["correct_answer"])
        self.assertFalse(verified["results"][0]["is_correct"])

    def test_actual_cost_overrun_stops_after_completed_request(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        benchmark = load_benchmark(fixture.registry_path, "bar-exam-15", fixture.root)
        model = load_model_configs(fixture.config_path)[0]
        first_request = build_requests(benchmark, (model,), "question", fixture.root)[0]
        adapter = FakeAdapter([ProviderResponse("정답: 1", 1000, 100, "stop")])

        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            report = self._runner(fixture, adapter).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_cost_usd=first_request.estimated_cost_usd,
            )

        self.assertEqual(1, report.provider_attempts)
        self.assertEqual(1, report.completed)
        self.assertIn("exceeded max-cost-usd", report.stopped_reason)
        self.assertGreater(report.charged_or_reserved_cost_usd, first_request.estimated_cost_usd)
        self.assertEqual(1, len(adapter.calls))

    def test_failed_replacement_run_clears_stale_verified_model_results(self):
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            self._runner(
                fixture, FakeAdapter([ProviderResponse("1: 1\n2: 2", 10, 5, "stop")])
            ).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=1,
            )
            self._runner(
                fixture,
                FakeAdapter([ProviderError("bad request", retryable=False, status_code=400)]),
            ).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="subject",
                execute=True,
                max_requests=1,
                resume=False,
            )
        verified = json.loads((fixture.problem_dir / "hard_results_verified.json").read_text())
        self.assertEqual([], verified["results"])
        self.assertEqual({}, verified["model_scores"])

    def test_terminal_failure_is_recorded_as_no_answer_when_run_partly_completed(self):
        """@brief 영구 실패 문항도 무응답 행으로 남겨 채점 분모를 유지한다."""
        fixture = WorkspaceFixture()
        self.addCleanup(fixture.close)
        adapter = FakeAdapter(
            [
                ProviderResponse("풀이\n정답: 1", 20, 10, "stop"),
                ProviderError("stream ended prematurely", retryable=False),
            ]
        )
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            self._runner(fixture, adapter).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
            )
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        results = verified["results"]
        self.assertEqual([1, 2], [row["question_number"] for row in results])
        failed_row = results[1]
        self.assertEqual(-1, failed_row["extracted_answer"])
        self.assertEqual("no_answer", failed_row["answer_status"])
        self.assertEqual("no_answer", failed_row["lenient_status"])
        self.assertFalse(failed_row["is_correct"])
        self.assertEqual(2.5, failed_row["points"])
        self.assertIn("stream ended prematurely", failed_row["failure_reason"])
        # 실패 문항은 0점이므로 점수는 성공한 문항만으로 결정된다.
        self.assertEqual(2.5, verified["model_scores"]["Fake Model"])
        self.assertEqual(2, verified["model_metrics"]["Fake Model"]["total_verified"])

    def test_review_statuses_use_sentinels_and_never_retry_on_resume(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        adapter = FakeAdapter([ProviderResponse("이 요청에는 답변할 수 없습니다.", 5, 4, "safety")])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            first = self._runner(fixture, adapter).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
            )
        self.assertEqual(1, first.review_required)
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        result = verified["results"][0]
        self.assertEqual(-2, result["extracted_answer"])
        self.assertEqual("refusal", result["answer_status"])
        self.assertTrue(result["needs_manual_review"])

        unused = FakeAdapter([])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            resumed = self._runner(fixture, unused).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
                retry_failed=True,
            )
        self.assertEqual(1, resumed.skipped)
        self.assertEqual([], unused.calls)

    def test_parse_failed_is_review_only_and_not_automatically_retried(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            first = self._runner(
                fixture, FakeAdapter([ProviderResponse("모호한 설명", 5, 4, "stop")])
            ).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
            )
        self.assertEqual(1, first.review_required)

        unused = FakeAdapter([])
        with patch.dict(os.environ, {"FAKE_API_KEY": "local-test-key"}):
            resumed = self._runner(fixture, unused).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=1,
                retry_failed=True,
            )
        self.assertEqual(1, resumed.skipped)
        self.assertEqual([], unused.calls)

    def test_atomic_store_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = AtomicJsonStore(path)
            store.write({"value": 1})
            self.assertEqual({"value": 1}, store.read({}))
            self.assertEqual([], list(Path(directory).glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
