"""@brief Runner integration tests for OAuth config, caps, refresh, and resume."""

from __future__ import annotations

import hashlib
import json
import unittest

from benchmark_runner import (
    BenchmarkRunner,
    ConfigurationError,
    ModelConfig,
    ProviderResponse,
    build_requests,
    load_benchmark,
    load_model_configs,
)
from oauth_providers import OAuthProviderRequestError
from tests.test_benchmark_runner import WorkspaceFixture


def _oauth_config(
    fixture,
    provider="openai-codex-oauth",
    question_count=None,
    input_cost_per_million=None,
    output_cost_per_million=None,
):
    """@brief Replace a workspace model config with one OAuth provider."""
    del question_count
    model = {
        "name": "OAuth Model",
        "provider": provider,
        "model_id": "oauth-model-id",
        "oauth_profile": "test-profile",
        "context_window": 10_000,
        "max_output_tokens": 100,
        "max_retries": 2,
        "input_cost_per_million": input_cost_per_million,
        "output_cost_per_million": output_cost_per_million,
    }
    if provider == "openai-codex-oauth":
        model["reasoning_effort"] = "max"
        model["text_verbosity"] = "medium"
    else:
        model["temperature"] = 0
    fixture._write_json(fixture.config_path, {"models": [model]})


class FakeOAuthAdapter:
    """@brief Adapter whose 401 refresh remains visible to runner accounting."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.refreshes = 0

    def send(self, prompt):
        self.calls.append(prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def refresh_auth(self):
        self.refreshes += 1


class OAuthConfigurationIntegrationTests(unittest.TestCase):
    """@brief Verify public model configuration and legacy fingerprint stability."""

    def test_openai_oauth_defaults_to_highest_reasoning_effort(self):
        model = ModelConfig.from_dict(
            {
                "name": "Codex",
                "provider": "openai-codex-oauth",
                "model_id": "gpt-5.6-sol",
                "context_window": 128_000,
                "max_output_tokens": 1_000,
            }
        )
        self.assertEqual("max", model.reasoning_effort)
        self.assertEqual("max", model.public_dict()["reasoning_effort"])

    def test_anthropic_oauth_defaults_to_highest_effort(self):
        model = ModelConfig.from_dict(
            {
                "name": "Claude",
                "provider": "anthropic-oauth",
                "model_id": "claude-fable-5",
                "context_window": 200_000,
                "max_output_tokens": 4_096,
            }
        )
        self.assertEqual("max", model.effort)
        self.assertEqual("max", model.public_dict()["effort"])

    def test_anthropic_oauth_disabled_thinking_skips_effort_default(self):
        model = ModelConfig.from_dict(
            {
                "name": "Claude",
                "provider": "anthropic-oauth",
                "model_id": "claude-sonnet-5",
                "context_window": 200_000,
                "max_output_tokens": 4_096,
                "thinking": "disabled",
            }
        )
        self.assertEqual("disabled", model.thinking)
        self.assertIsNone(model.effort)
        public = model.public_dict()
        self.assertEqual("disabled", public["thinking"])
        self.assertIsNone(public["effort"])

    def test_anthropic_oauth_without_thinking_keeps_fingerprint_key_absent(self):
        model = ModelConfig.from_dict(
            {
                "name": "Claude",
                "provider": "anthropic-oauth",
                "model_id": "claude-fable-5",
                "context_window": 200_000,
                "max_output_tokens": 4_096,
            }
        )
        self.assertNotIn("thinking", model.public_dict())

    def test_thinking_is_anthropic_oauth_only_and_disabled_only(self):
        base = {
            "name": "Model",
            "model_id": "id",
            "context_window": 128_000,
            "max_output_tokens": 1_000,
        }
        with self.assertRaises(ConfigurationError):
            ModelConfig.from_dict(
                {**base, "provider": "openai-codex-oauth", "thinking": "disabled"}
            )
        with self.assertRaises(ConfigurationError):
            ModelConfig.from_dict(
                {**base, "provider": "anthropic-oauth", "thinking": "adaptive"}
            )

    def test_oauth_config_is_secret_free_and_versioned(self):
        model = ModelConfig.from_dict(
            {
                "name": "Codex",
                "provider": "openai-codex-oauth",
                "model_id": "gpt-model-exact",
                "oauth_profile": "default",
                "context_window": 128_000,
                "max_output_tokens": 1_000,
                "reasoning_effort": "xhigh",
                "text_verbosity": "low",
            }
        )
        public = model.public_dict()
        self.assertTrue(model.is_oauth)
        self.assertEqual("gpt-model-exact", public["model_id"])
        self.assertEqual("default", public["oauth_profile"])
        self.assertIn("oauth_protocol_version", public)
        self.assertIn("oauth_transform_version", public)
        self.assertIn("oauth_transform_sha256", public)
        self.assertNotIn("api_key_env", public)
        self.assertNotIn("access_token", json.dumps(public))
        other_profile = ModelConfig.from_dict(
            {
                "name": "Codex",
                "provider": "openai-codex-oauth",
                "model_id": "gpt-model-exact",
                "oauth_profile": "other",
                "context_window": 128_000,
                "max_output_tokens": 1_000,
                "reasoning_effort": "xhigh",
                "text_verbosity": "low",
            }
        )
        self.assertNotEqual(model.fingerprint(), other_profile.fingerprint())
        anthropic_public = ModelConfig.from_dict(
            {
                "name": "Claude",
                "provider": "anthropic-oauth",
                "model_id": "claude-model-exact",
                "context_window": 200_000,
                "max_output_tokens": 1_000,
            }
        ).public_dict()
        self.assertIn("oauth_system_sha256", anthropic_public)

    def test_literal_oauth_secrets_are_rejected(self):
        base = {
            "name": "Unsafe",
            "provider": "anthropic-oauth",
            "model_id": "claude",
            "context_window": 10_000,
        }
        for key in (
            "access_token",
            "refresh_token",
            "id_token",
            "oauth_token",
            "bearer_token",
            "client_secret",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ConfigurationError, "Literal credential"):
                    ModelConfig.from_dict({**base, key: "secret"})

    def test_oauth_rejects_api_key_env_base_url_and_invalid_profile(self):
        base = {
            "name": "Unsafe",
            "provider": "anthropic-oauth",
            "model_id": "claude",
            "context_window": 10_000,
        }
        with self.assertRaisesRegex(ConfigurationError, "does not accept api_key_env"):
            ModelConfig.from_dict({**base, "api_key_env": "ANTHROPIC_API_KEY"})
        with self.assertRaisesRegex(ConfigurationError, "fixed allowlisted"):
            ModelConfig.from_dict({**base, "base_url": "https://proxy.invalid"})
        with self.assertRaisesRegex(ConfigurationError, "TLS verification"):
            ModelConfig.from_dict({**base, "verify_tls": False})
        with self.assertRaisesRegex(ConfigurationError, "oauth_profile"):
            ModelConfig.from_dict({**base, "oauth_profile": "../unsafe"})

    def test_oauth_accepts_benchmark_token_prices(self):
        for provider in ("openai-codex-oauth", "anthropic-oauth"):
            with self.subTest(provider=provider):
                model = ModelConfig.from_dict(
                    {
                        "name": "Priced subscription",
                        "provider": provider,
                        "model_id": "oauth-model",
                        "context_window": 10_000,
                        "input_cost_per_million": 3,
                        "output_cost_per_million": 15,
                    }
                )
                self.assertEqual(3.0, model.input_cost_per_million)
                self.assertEqual(15.0, model.output_cost_per_million)
                self.assertEqual(
                    3.0, model.public_dict()["input_cost_per_million"]
                )
                self.assertEqual(
                    15.0, model.public_dict()["output_cost_per_million"]
                )

    def test_oauth_rejects_negative_benchmark_token_prices(self):
        with self.assertRaisesRegex(ConfigurationError, "cannot be negative"):
            ModelConfig.from_dict(
                {
                    "name": "Invalid price",
                    "provider": "anthropic-oauth",
                    "model_id": "claude",
                    "context_window": 10_000,
                    "input_cost_per_million": -1,
                    "output_cost_per_million": 15,
                }
            )

    def test_legacy_public_dict_and_fingerprint_are_unchanged(self):
        raw = {
            "name": "Legacy",
            "provider": "openai-compatible",
            "model_id": "legacy-model",
            "api_key_env": "LEGACY_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "context_window": 10_000,
            "max_output_tokens": 500,
            "request_timeout_seconds": 123,
            "max_tokens_parameter": "max_completion_tokens",
            "temperature": 0,
            "requests_per_minute": 4,
            "max_retries": 2,
            "input_cost_per_million": 1.5,
            "output_cost_per_million": 2.5,
        }
        model = ModelConfig.from_dict(raw)
        expected = {
            "name": "Legacy",
            "provider": "openai-compatible",
            "model_id": "legacy-model",
            "api_key_env": "LEGACY_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "context_window": 10_000,
            "max_output_tokens": 500,
            "request_timeout_seconds": 123.0,
            "max_tokens_parameter": "max_completion_tokens",
            "temperature": 0.0,
            "requests_per_minute": 4.0,
            "max_retries": 2,
            "input_cost_per_million": 1.5,
            "output_cost_per_million": 2.5,
        }
        serialized = json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(expected, model.public_dict())
        self.assertEqual(
            hashlib.sha256(serialized.encode()).hexdigest(),
            model.fingerprint(),
        )


class OAuthRunnerIntegrationTests(unittest.TestCase):
    """@brief Verify caps and authentication retry behavior at the runner boundary."""

    def _runner(self, fixture, adapter, credentials=None):
        resolved = credentials if credentials is not None else object()
        return BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            adapter_factory=lambda _model, credential: (
                adapter if credential is resolved else self.fail("wrong credential")
            ),
            credential_resolver=lambda _model: resolved,
            sleep_fn=lambda _delay: None,
            random_fn=lambda: 0.0,
        )

    def test_dry_run_never_resolves_oauth_credentials(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        resolved = []
        runner = BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            credential_resolver=lambda _model: resolved.append(True),
            adapter_factory=lambda _model, _credential: self.fail("adapter built"),
        )
        report = runner.run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
        )
        self.assertTrue(report.dry_run)
        self.assertEqual([], resolved)

    def test_oauth_allows_max_cost_as_the_only_execution_cap(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(
            fixture,
            input_cost_per_million=2,
            output_cost_per_million=10,
        )
        resolved = []
        credential = object()
        adapter = FakeOAuthAdapter(
            [ProviderResponse("정답: 1", 1_000, 200, "completed")]
        )

        def resolve_credential(_model):
            resolved.append(True)
            return credential

        runner = BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            credential_resolver=resolve_credential,
            adapter_factory=lambda _model, value: (
                adapter if value is credential else self.fail("wrong credential")
            ),
        )
        report = runner.run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_cost_usd=1,
        )
        self.assertEqual(1, len(resolved))
        self.assertEqual(1, report.completed)
        self.assertEqual(1, report.provider_attempts)

    def test_oauth_max_cost_requires_configured_token_prices_before_credentials(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        resolved = []
        runner = BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            credential_resolver=lambda _model: resolved.append(True),
        )
        with self.assertRaisesRegex(ConfigurationError, "requires input/output prices"):
            runner.run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_cost_usd=1,
            )
        self.assertEqual([], resolved)

    def test_oauth_cost_uses_reported_input_output_tokens_and_ignores_cache(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(
            fixture,
            provider="anthropic-oauth",
            input_cost_per_million=2,
            output_cost_per_million=10,
        )
        adapter = FakeOAuthAdapter(
            [
                ProviderResponse(
                    "정답: 1",
                    1_000,
                    200,
                    "end_turn",
                    raw={
                        "usage": {
                            "cache_creation_input_tokens": 900_000,
                            "cache_read_input_tokens": 800_000,
                        }
                    },
                )
            ]
        )
        report = self._runner(fixture, adapter).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_cost_usd=1,
        )
        expected_cost = (1_000 * 2 + 200 * 10) / 1_000_000
        self.assertAlmostEqual(expected_cost, report.charged_or_reserved_cost_usd)

        verified = json.loads(
            (fixture.problem_dir / "results_verified.json").read_text()
        )
        metadata = verified["run_metadata"]["OAuth Model"]
        self.assertAlmostEqual(expected_cost, metadata["cost_usd"])
        self.assertEqual(
            {"input_per_million": 2.0, "output_per_million": 10.0},
            metadata["pricing"],
        )
        self.assertEqual(
            {"input_tokens": 1_000, "output_tokens": 200, "total_tokens": 1_200},
            metadata["token_usage"],
        )

    def test_oauth_max_cost_stops_after_reported_usage_overrun(self):
        fixture = WorkspaceFixture(question_count=2)
        self.addCleanup(fixture.close)
        _oauth_config(
            fixture,
            input_cost_per_million=2,
            output_cost_per_million=10,
        )
        benchmark = load_benchmark(
            fixture.registry_path, "bar-exam-15", fixture.root
        )
        model = load_model_configs(fixture.config_path)[0]
        first_request = build_requests(
            benchmark, (model,), "question", fixture.root
        )[0]
        adapter = FakeOAuthAdapter(
            [ProviderResponse("정답: 1", 1_000_000, 100_000, "completed")]
        )

        report = self._runner(fixture, adapter).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_cost_usd=first_request.estimated_cost_usd,
        )

        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, report.provider_attempts)
        self.assertEqual(1, report.completed)
        self.assertIn("exceeded max-cost-usd", report.stopped_reason)
        self.assertGreater(
            report.charged_or_reserved_cost_usd,
            first_request.estimated_cost_usd,
        )

    def test_401_refresh_retry_consumes_two_request_slots(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        adapter = FakeOAuthAdapter(
            [
                OAuthProviderRequestError(
                    "openai-codex-oauth",
                    "authentication",
                    status_code=401,
                    auth_refresh_required=True,
                ),
                ProviderResponse("정답: 1", 10, 4, "completed"),
            ]
        )
        report = self._runner(fixture, adapter).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_requests=2,
        )
        self.assertEqual(1, adapter.refreshes)
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(2, report.provider_attempts)
        self.assertEqual(1, report.completed)
        self.assertIsNone(report.charged_or_reserved_cost_usd)
        verified = json.loads((fixture.problem_dir / "results_verified.json").read_text())
        oauth_metadata = verified["run_metadata"]["OAuth Model"]["oauth"]
        self.assertEqual("test-profile", oauth_metadata["profile"])
        self.assertIn("protocol_version", oauth_metadata)
        self.assertIsNone(
            verified["run_metadata"]["OAuth Model"]["cost_usd"]
        )

    def test_request_cap_prevents_refresh_and_second_attempt(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        adapter = FakeOAuthAdapter(
            [
                OAuthProviderRequestError(
                    "openai-codex-oauth",
                    "authentication",
                    status_code=401,
                    auth_refresh_required=True,
                )
            ]
        )
        report = self._runner(fixture, adapter).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_requests=1,
        )
        self.assertEqual(0, adapter.refreshes)
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, report.provider_attempts)
        self.assertIn("before the OAuth retry", report.stopped_reason)

    def test_permanent_authentication_failure_aborts_without_failed_checkpoint(self):
        fixture = WorkspaceFixture(question_count=2)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        adapter = FakeOAuthAdapter(
            [
                OAuthProviderRequestError(
                    "openai-codex-oauth",
                    "authentication",
                    authentication_fatal=True,
                )
            ]
        )
        with self.assertRaisesRegex(ConfigurationError, "OAuth authentication failed"):
            self._runner(fixture, adapter).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=2,
            )
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual([], list(fixture.problem_dir.rglob("*checkpoint*")))

    def test_second_401_after_refresh_aborts_the_whole_run(self):
        fixture = WorkspaceFixture(question_count=2)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        def unauthorized():
            return OAuthProviderRequestError(
                "openai-codex-oauth",
                "authentication",
                status_code=401,
                auth_refresh_required=True,
            )

        adapter = FakeOAuthAdapter([unauthorized(), unauthorized()])
        with self.assertRaisesRegex(ConfigurationError, "remained unauthorized"):
            self._runner(fixture, adapter).run(
                benchmark_id="bar-exam-15",
                config_path=fixture.config_path,
                run_mode="question",
                execute=True,
                max_requests=3,
            )
        self.assertEqual(1, adapter.refreshes)
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual([], list(fixture.problem_dir.rglob("*checkpoint*")))

    def test_subscription_quota_records_one_failure_then_stops(self):
        fixture = WorkspaceFixture(question_count=2)
        self.addCleanup(fixture.close)
        _oauth_config(fixture, provider="anthropic-oauth")
        adapter = FakeOAuthAdapter(
            [
                OAuthProviderRequestError(
                    "anthropic-oauth",
                    "subscription_quota",
                    status_code=400,
                )
            ]
        )
        report = self._runner(fixture, adapter).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_requests=2,
        )
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, report.failed)
        self.assertIn("usage is exhausted", report.stopped_reason)

    def test_oauth_resume_skips_matching_completed_request_without_credentials(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        first_adapter = FakeOAuthAdapter(
            [ProviderResponse("정답: 1", 10, 4, "completed")]
        )
        first = self._runner(fixture, first_adapter).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_requests=1,
        )
        self.assertEqual(1, first.completed)

        resolved = []
        resumed = BenchmarkRunner(
            fixture.root,
            fixture.registry_path,
            credential_resolver=lambda _model: resolved.append(True),
            adapter_factory=lambda _model, _credential: self.fail("adapter built"),
        ).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_requests=1,
        )
        self.assertEqual(1, resumed.skipped)
        self.assertEqual([], resolved)

    def test_credentials_never_enter_run_artifacts_or_report(self):
        fixture = WorkspaceFixture(question_count=1)
        self.addCleanup(fixture.close)
        _oauth_config(fixture)
        credentials = {
            "access_token": "ACCESS_TOKEN_SENTINEL",
            "refresh_token": "REFRESH_TOKEN_SENTINEL",
            "account_id": "ACCOUNT_ID_SENTINEL",
        }
        adapter = FakeOAuthAdapter(
            [ProviderResponse("정답: 1", 10, 4, "completed", raw={"id": "safe"})]
        )
        report = self._runner(fixture, adapter, credentials=credentials).run(
            benchmark_id="bar-exam-15",
            config_path=fixture.config_path,
            run_mode="question",
            execute=True,
            max_requests=1,
        )
        rendered = json.dumps(report.__dict__, ensure_ascii=False)
        for path in fixture.root.rglob("*"):
            if path.is_file():
                rendered += path.read_text(encoding="utf-8")
        for sentinel in credentials.values():
            self.assertNotIn(sentinel, rendered)


if __name__ == "__main__":
    unittest.main()
