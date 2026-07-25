#!/usr/bin/env python3
"""
@brief Registry-driven benchmark runner with safe dry-run defaults.

The runner intentionally keeps prompts, checkpoints, and raw provider responses
inside the ignored problems tree. Public dashboard data is produced separately by
sync_data.py after the generated per-section verified files are reviewed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


NO_ANSWER = -1
REFUSAL_ANSWER = -2
PROMPT_VERSION = "bar-exam-v1"
OAUTH_PROVIDERS = {"openai-codex-oauth", "anthropic-oauth"}
SUPPORTED_PROVIDERS = {"openai-compatible", "anthropic", "google", *OAUTH_PROVIDERS}
DEFAULT_OPENAI_CODEX_REASONING_EFFORT = "max"
_TERMINAL_ENTRY_STATUSES = {"completed", "no_answer", "refusal", "parse_failed"}
_REFUSAL_PATTERNS = (
    r"답변(?:할|을 제공할) 수 없",
    r"응답(?:할|을 제공할) 수 없",
    r"도와드릴 수 없",
    r"답변을 거부",
    r"(?:cannot|can't|won't) (?:answer|assist|comply|provide)",
    r"(?:i must|i have to) refuse",
)
_GOOGLE_BLOCK_REASONS = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
    "IMAGE_RECITATION",
    "MODEL_ARMOR",
}


class RunnerError(RuntimeError):
    """@brief Base error for benchmark preparation and execution failures."""


class ConfigurationError(RunnerError):
    """@brief Raised when registry or model configuration is invalid."""


class ContextLimitError(RunnerError):
    """@brief Raised before a request that cannot fit the configured context."""


class BudgetExceeded(RunnerError):
    """@brief Raised before a provider call would exceed an execution cap."""


class ProviderError(RunnerError):
    """@brief Normalized provider error carrying retry policy information."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: Optional[int] = None,
        auth_refresh_required: bool = False,
        authentication_fatal: bool = False,
        quota_exhausted: bool = False,
        retry_after_seconds: Optional[float] = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.auth_refresh_required = auth_refresh_required
        self.authentication_fatal = authentication_fatal
        self.quota_exhausted = quota_exhausted
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class SectionDefinition:
    """@brief One independently scored section from the benchmark registry."""

    id: str
    sheet: str
    subject: str
    section: str
    question_count: int
    max_score: float
    problem_dir: Path
    metadata_path: Optional[Path] = None


@dataclass(frozen=True)
class BenchmarkDefinition:
    """@brief Validated benchmark definition consumed by the runner."""

    id: str
    title: Mapping[str, str]
    scoring_type: str
    total_questions: int
    max_score: float
    points_per_question: Optional[float]
    sections: tuple[SectionDefinition, ...]


@dataclass(frozen=True)
class Question:
    """@brief Local question text combined with public answer metadata."""

    benchmark_id: str
    benchmark_title: str
    section_id: str
    subject: str
    section: str
    number: int
    correct_answer: int
    points: float
    choices: tuple[int, ...]
    text: str
    source_path: Optional[Path]
    source_hash: str


@dataclass(frozen=True)
class ModelConfig:
    """@brief Secret-free provider and generation configuration for one model."""

    name: str
    provider: str
    model_id: str
    api_key_env: Optional[str]
    base_url: Optional[str]
    context_window: int
    max_output_tokens: int
    request_timeout_seconds: float
    max_tokens_parameter: str
    temperature: Optional[float]
    requests_per_minute: Optional[float]
    max_retries: int
    input_cost_per_million: Optional[float]
    output_cost_per_million: Optional[float]
    oauth_profile: Optional[str] = None
    reasoning_effort: Optional[str] = None
    text_verbosity: Optional[str] = None
    effort: Optional[str] = None
    thinking: Optional[str] = None
    vertexai: bool = False
    vertex_project: Optional[str] = None
    vertex_location: Optional[str] = None
    thinking_level: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelConfig":
        """
        @brief Validate and construct a model configuration.

        @param value Raw JSON model mapping.
        @return Validated configuration without any credential value.
        @throws ConfigurationError If required fields or limits are invalid.
        """
        forbidden_secret_fields = (
            "api_key",
            "access_token",
            "refresh_token",
            "id_token",
            "oauth_token",
            "bearer_token",
            "client_secret",
        )
        present_secrets = [key for key in forbidden_secret_fields if key in value]
        if present_secrets:
            if present_secrets == ["api_key"]:
                raise ConfigurationError(
                    "Model configuration must use api_key_env; "
                    "literal api_key values are forbidden."
                )
            raise ConfigurationError(
                "Literal credential fields are forbidden in model configuration: "
                f"{', '.join(present_secrets)}."
            )

        provider_aliases = {
            "openai": "openai-compatible",
            "openai_compatible": "openai-compatible",
            "gemini": "google",
        }
        provider = provider_aliases.get(str(value.get("provider", "")), str(value.get("provider", "")))
        if provider not in SUPPORTED_PROVIDERS:
            raise ConfigurationError(
                f"Unsupported provider '{provider}'. Expected one of: "
                f"{', '.join(sorted(SUPPORTED_PROVIDERS))}."
            )

        required = ("name", "model_id", "context_window")
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise ConfigurationError(f"Model configuration is missing: {', '.join(missing)}")

        is_oauth = provider in OAUTH_PROVIDERS
        api_key_env: Optional[str] = None
        oauth_profile: Optional[str] = None
        if is_oauth:
            if value.get("api_key_env"):
                raise ConfigurationError(
                    f"{provider} uses oauth_profile and does not accept api_key_env."
                )
            oauth_profile = str(value.get("oauth_profile", "default"))
            if not re.fullmatch(r"[A-Za-z0-9._-]+", oauth_profile):
                raise ConfigurationError(
                    "oauth_profile must match [A-Za-z0-9._-]+."
                )
            if value.get("base_url"):
                raise ConfigurationError(
                    f"{provider} uses a fixed allowlisted endpoint and does not accept base_url."
                )
            tls_bypass = any(
                field in value and value.get(field) is False
                for field in ("verify", "verify_ssl", "verify_tls", "tls_verify")
            ) or any(
                bool(value.get(field))
                for field in ("insecure", "disable_tls_verification")
            )
            if tls_bypass:
                raise ConfigurationError(
                    f"{provider} requires normal TLS verification and rejects bypass settings."
                )
        else:
            # google + vertexai + vertex_project는 ADC(서비스 계정)로 인증하므로
            # api_key_env 없이도 허용한다.
            uses_vertex_adc = (
                provider == "google"
                and bool(value.get("vertexai"))
                and bool(value.get("vertex_project"))
            )
            if not value.get("api_key_env"):
                if not uses_vertex_adc:
                    raise ConfigurationError("Model configuration is missing: api_key_env")
            else:
                api_key_env = str(value["api_key_env"])
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", api_key_env):
                    raise ConfigurationError(f"Invalid api_key_env name: {api_key_env}")
            if value.get("oauth_profile"):
                raise ConfigurationError(
                    f"{provider} uses api_key_env and does not accept oauth_profile."
                )

        context_window = int(value["context_window"])
        max_output_tokens = int(value.get("max_output_tokens", 2048))
        max_retries = int(value.get("max_retries", 3))
        if context_window <= 0 or max_output_tokens <= 0:
            raise ConfigurationError("context_window and max_output_tokens must be positive.")
        if max_output_tokens >= context_window:
            raise ConfigurationError("max_output_tokens must be smaller than context_window.")
        if max_retries < 0:
            raise ConfigurationError("max_retries cannot be negative.")

        requests_per_minute = value.get("requests_per_minute")
        if requests_per_minute is not None and float(requests_per_minute) <= 0:
            raise ConfigurationError("requests_per_minute must be positive when set.")

        input_cost = value.get("input_cost_per_million")
        output_cost = value.get("output_cost_per_million")
        for label, cost in (("input_cost_per_million", input_cost), ("output_cost_per_million", output_cost)):
            if cost is not None and float(cost) < 0:
                raise ConfigurationError(f"{label} cannot be negative.")

        temperature = value.get("temperature")
        request_timeout_seconds = float(value.get("request_timeout_seconds", 300))
        if request_timeout_seconds <= 0:
            raise ConfigurationError("request_timeout_seconds must be positive.")
        max_tokens_parameter = str(value.get("max_tokens_parameter", "max_tokens"))
        if max_tokens_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ConfigurationError(
                "max_tokens_parameter must be max_tokens or max_completion_tokens."
            )
        reasoning_effort = value.get("reasoning_effort")
        text_verbosity = value.get("text_verbosity")
        effort = value.get("effort")
        thinking = value.get("thinking")
        if thinking is not None:
            if provider != "anthropic-oauth":
                raise ConfigurationError(
                    "thinking is supported only by anthropic-oauth."
                )
            if str(thinking) != "disabled":
                raise ConfigurationError('thinking supports only "disabled".')
        if provider == "openai-codex-oauth":
            if effort is not None:
                raise ConfigurationError(
                    "effort is supported only by anthropic-oauth."
                )
            if reasoning_effort is None:
                reasoning_effort = DEFAULT_OPENAI_CODEX_REASONING_EFFORT
            allowed_efforts = {
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
                "ultra",
            }
            if reasoning_effort is not None and str(reasoning_effort) not in allowed_efforts:
                raise ConfigurationError(
                    "reasoning_effort must be one of: "
                    f"{', '.join(sorted(allowed_efforts))}."
                )
            if text_verbosity is not None and str(text_verbosity) not in {
                "low",
                "medium",
                "high",
            }:
                raise ConfigurationError(
                    "text_verbosity must be low, medium, or high."
                )
            if temperature is not None:
                raise ConfigurationError(
                    "openai-codex-oauth does not send temperature; omit it for a neutral request."
                )
        elif provider == "anthropic-oauth":
            if reasoning_effort is not None or text_verbosity is not None:
                raise ConfigurationError(
                    "reasoning_effort and text_verbosity are supported only by "
                    "openai-codex-oauth."
                )
            if effort is None and thinking is None:
                # thinking을 끈 실행은 effort 기본값을 강제하지 않는다.
                effort = "max"
            if effort is not None and str(effort) not in {
                "low", "medium", "high", "xhigh", "max"
            }:
                raise ConfigurationError(
                    "effort must be one of: high, low, max, medium, xhigh."
                )
        elif (
            reasoning_effort is not None
            or text_verbosity is not None
            or effort is not None
        ):
            raise ConfigurationError(
                "OAuth effort settings are supported only by OAuth providers."
            )
        vertexai = value.get("vertexai", False)
        if vertexai not in (True, False):
            raise ConfigurationError("vertexai must be a boolean.")
        if vertexai and provider != "google":
            raise ConfigurationError(
                "vertexai (Vertex AI Express Mode) is supported only by the google provider."
            )
        vertex_project = value.get("vertex_project")
        vertex_location = value.get("vertex_location")
        thinking_level = value.get("thinking_level")
        if (vertex_project or vertex_location) and not vertexai:
            raise ConfigurationError(
                "vertex_project/vertex_location require vertexai: true."
            )
        if vertexai and vertex_project and value.get("api_key_env"):
            raise ConfigurationError(
                "Use either api_key_env (Express Mode) or vertex_project (ADC), not both."
            )
        if vertex_location and not vertex_project:
            raise ConfigurationError("vertex_location requires vertex_project.")
        if thinking_level is not None:
            if provider != "google":
                raise ConfigurationError(
                    "thinking_level is supported only by the google provider."
                )
            if str(thinking_level).lower() not in {"minimal", "low", "medium", "high"}:
                raise ConfigurationError(
                    "thinking_level must be one of: minimal, low, medium, high."
                )
        return cls(
            name=str(value["name"]),
            provider=provider,
            model_id=str(value["model_id"]),
            api_key_env=api_key_env,
            base_url=str(value["base_url"]) if value.get("base_url") else None,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            request_timeout_seconds=request_timeout_seconds,
            max_tokens_parameter=max_tokens_parameter,
            temperature=float(temperature) if temperature is not None else None,
            requests_per_minute=(
                float(requests_per_minute) if requests_per_minute is not None else None
            ),
            max_retries=max_retries,
            input_cost_per_million=float(input_cost) if input_cost is not None else None,
            output_cost_per_million=float(output_cost) if output_cost is not None else None,
            oauth_profile=oauth_profile,
            reasoning_effort=(
                str(reasoning_effort) if reasoning_effort is not None else None
            ),
            text_verbosity=(
                str(text_verbosity) if text_verbosity is not None else None
            ),
            effort=str(effort) if effort is not None else None,
            thinking=str(thinking) if thinking is not None else None,
            vertexai=bool(vertexai),
            vertex_project=str(vertex_project) if vertex_project else None,
            vertex_location=str(vertex_location) if vertex_location else None,
            thinking_level=(
                str(thinking_level).lower() if thinking_level is not None else None
            ),
        )

    def public_dict(self) -> dict[str, Any]:
        """@brief Return a serializable configuration that never contains a secret."""
        legacy = {
            "name": self.name,
            "provider": self.provider,
            "model_id": self.model_id,
            "api_key_env": self.api_key_env,
            "base_url": self.base_url,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_tokens_parameter": self.max_tokens_parameter,
            "temperature": self.temperature,
            "requests_per_minute": self.requests_per_minute,
            "max_retries": self.max_retries,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
        }
        if self.vertexai:
            # False일 때는 넣지 않아 기존 모델 fingerprint를 보존한다.
            legacy["vertexai"] = True
            if self.vertex_project:
                legacy["vertex_project"] = self.vertex_project
                legacy["vertex_location"] = self.vertex_location or "global"
        if self.thinking_level is not None:
            legacy["thinking_level"] = self.thinking_level
        if not self.is_oauth:
            return legacy
        from oauth_providers import (
            ANTHROPIC_PROTOCOL_VERSION,
            ANTHROPIC_SYSTEM_SPEC_SHA256,
            ANTHROPIC_TRANSFORM_VERSION,
            OPENAI_CODEX_PROTOCOL_VERSION,
            OPENAI_TRANSFORM_SPEC_SHA256,
            OPENAI_TRANSFORM_VERSION,
        )

        common = {
            key: value
            for key, value in legacy.items()
            if key not in {"api_key_env", "base_url", "max_tokens_parameter"}
        }
        common["oauth_profile"] = self.oauth_profile
        if self.provider == "openai-codex-oauth":
            common.update(
                {
                    "reasoning_effort": self.reasoning_effort,
                    "text_verbosity": self.text_verbosity,
                    "oauth_protocol_version": OPENAI_CODEX_PROTOCOL_VERSION,
                    "oauth_transform_version": OPENAI_TRANSFORM_VERSION,
                    "oauth_transform_sha256": OPENAI_TRANSFORM_SPEC_SHA256,
                }
            )
        else:
            common.update(
                {
                    "effort": self.effort,
                    "oauth_protocol_version": ANTHROPIC_PROTOCOL_VERSION,
                }
            )
            if self.thinking is not None:
                # 미설정 시 키를 넣지 않아 기존 checkpoint fingerprint를 보존한다.
                common["thinking"] = self.thinking
            common.update(
                {
                    "oauth_transform_version": ANTHROPIC_TRANSFORM_VERSION,
                    "oauth_transform_sha256": ANTHROPIC_SYSTEM_SPEC_SHA256,
                    "oauth_system_sha256": ANTHROPIC_SYSTEM_SPEC_SHA256,
                }
            )
        return common

    @property
    def is_oauth(self) -> bool:
        """@brief Return whether this model uses subscription OAuth credentials."""
        return self.provider in OAUTH_PROVIDERS

    def fingerprint(self) -> str:
        """@brief Return a stable identifier for resume compatibility checks."""
        return _sha256_json(self.public_dict())


@dataclass(frozen=True)
class ProviderResponse:
    """@brief Normalized response returned by every provider adapter."""

    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    stop_reason: Optional[str] = None
    refusal: bool = False
    raw: Any = None


@dataclass(frozen=True)
class ParseResult:
    """@brief Strictly parsed answers and a shared response-level status."""

    status: str
    answers: Mapping[int, Optional[int]]
    reason: Optional[str] = None


@dataclass(frozen=True)
class PlannedRequest:
    """@brief Immutable provider request prepared before any network activity."""

    model: ModelConfig
    section: SectionDefinition
    questions: tuple[Question, ...]
    request_key: str
    prompt: str
    prompt_hash: str
    source_hash: str
    estimated_input_tokens: int
    estimated_cost_usd: Optional[float]


@dataclass
class RunReport:
    """@brief User-facing summary of a dry run or execution."""

    benchmark_id: str
    run_mode: str
    dry_run: bool
    planned_requests: int
    provider_attempts: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    review_required: int = 0
    estimated_max_cost_usd: Optional[float] = None
    charged_or_reserved_cost_usd: Optional[float] = 0.0
    stopped_reason: Optional[str] = None
    rate_limit_errors: int = 0
    retryable_errors: int = 0
    attempt_latency_p50_seconds: Optional[float] = None
    attempt_latency_p95_seconds: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    preview: list[dict[str, Any]] = field(default_factory=list)


class ProviderAdapter:
    """@brief Minimal provider adapter interface used by BenchmarkRunner."""

    def send(self, prompt: str) -> ProviderResponse:
        """@brief Send one user-only prompt and return normalized response metadata."""
        raise NotImplementedError


class OpenAICompatibleAdapter(ProviderAdapter):
    """@brief Lazy OpenAI SDK adapter for OpenAI-compatible chat endpoints."""

    def __init__(self, config: ModelConfig, api_key: str):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigurationError(
                "The openai package is required only for --execute with an "
                "openai-compatible model. Install it before the real run."
            ) from exc

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "max_retries": 0,
            "timeout": config.request_timeout_seconds,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self._client = OpenAI(**kwargs)
        self._config = config

    def send(self, prompt: str) -> ProviderResponse:
        """@brief Send a Chat Completions request containing only a user message."""
        kwargs: dict[str, Any] = {
            "model": self._config.model_id,
            "messages": [{"role": "user", "content": prompt}],
        }
        kwargs[self._config.max_tokens_parameter] = self._config.max_output_tokens
        if self._config.temperature is not None:
            kwargs["temperature"] = self._config.temperature
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise _provider_error_from_exception(exc) from exc

        choice = response.choices[0]
        message = choice.message
        text = _coerce_text(getattr(message, "content", ""))
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            text=text,
            input_tokens=_optional_int(getattr(usage, "prompt_tokens", None)),
            output_tokens=_optional_int(getattr(usage, "completion_tokens", None)),
            stop_reason=_optional_string(getattr(choice, "finish_reason", None)),
            refusal=(
                bool(getattr(message, "refusal", None))
                or str(getattr(choice, "finish_reason", "")).lower() == "content_filter"
            ),
            raw=_as_jsonable(response),
        )


class AnthropicAdapter(ProviderAdapter):
    """@brief Lazy Anthropic Messages API adapter."""

    def __init__(self, config: ModelConfig, api_key: str):
        try:
            import anthropic
        except ImportError as exc:
            raise ConfigurationError(
                "The anthropic package is required only for --execute with an "
                "Anthropic model. Install it before the real run."
            ) from exc

        self._client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=0,
            timeout=config.request_timeout_seconds,
        )
        self._config = config

    def send(self, prompt: str) -> ProviderResponse:
        """@brief Send one Anthropic message without a system prompt or tools."""
        kwargs: dict[str, Any] = {
            "model": self._config.model_id,
            "max_tokens": self._config.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._config.temperature is not None:
            kwargs["temperature"] = self._config.temperature
        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise _provider_error_from_exception(exc) from exc

        text = "".join(
            str(getattr(block, "text", ""))
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)
        stop_reason = _optional_string(getattr(response, "stop_reason", None))
        return ProviderResponse(
            text=text,
            input_tokens=_optional_int(getattr(usage, "input_tokens", None)),
            output_tokens=_optional_int(getattr(usage, "output_tokens", None)),
            stop_reason=stop_reason,
            refusal=stop_reason in {"refusal", "content_filter"},
            raw=_as_jsonable(response),
        )


class GoogleAdapter(ProviderAdapter):
    """@brief Lazy Google Gen AI SDK adapter."""

    def __init__(self, config: ModelConfig, api_key: str):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ConfigurationError(
                "The google-genai package is required only for --execute with a "
                "Google model. Install it before the real run."
            ) from exc

        client_kwargs: dict[str, Any] = {
            "http_options": types.HttpOptions(
                timeout=int(config.request_timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        }
        if config.vertexai and config.vertex_project:
            # Vertex AI + 서비스 계정(ADC): GOOGLE_APPLICATION_CREDENTIALS로 인증
            client_kwargs.update(
                vertexai=True,
                project=config.vertex_project,
                location=config.vertex_location or "global",
            )
        elif config.vertexai:
            # Vertex AI Express Mode: API 키("AQ." 접두)만으로 Vertex 엔드포인트 사용
            client_kwargs.update(vertexai=True, api_key=api_key)
        else:
            client_kwargs["api_key"] = api_key
        self._client = genai.Client(**client_kwargs)
        self._types = types
        self._config = config

    def send(self, prompt: str) -> ProviderResponse:
        """@brief Generate Google content from one user prompt without tools."""
        config_kwargs: dict[str, Any] = {"max_output_tokens": self._config.max_output_tokens}
        if self._config.temperature is not None:
            config_kwargs["temperature"] = self._config.temperature
        if self._config.thinking_level is not None:
            config_kwargs["thinking_config"] = self._types.ThinkingConfig(
                thinking_level=self._config.thinking_level
            )
        try:
            response = self._client.models.generate_content(
                model=self._config.model_id,
                contents=prompt,
                config=self._types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            raise _provider_error_from_exception(exc) from exc

        usage = getattr(response, "usage_metadata", None)
        candidates = getattr(response, "candidates", None) or []
        finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        stop_reason = _enum_value(finish_reason)
        prompt_feedback = getattr(response, "prompt_feedback", None)
        prompt_block_reason = getattr(prompt_feedback, "block_reason", None)
        normalized_finish_reason = _normalized_reason(finish_reason)
        normalized_prompt_reason = _normalized_reason(prompt_block_reason)
        input_tokens = _optional_int(getattr(usage, "prompt_token_count", None))
        total_tokens = _optional_int(getattr(usage, "total_token_count", None))
        candidate_tokens = _optional_int(getattr(usage, "candidates_token_count", None))
        thought_tokens = _optional_int(getattr(usage, "thoughts_token_count", None)) or 0
        output_tokens = (
            total_tokens - input_tokens
            if total_tokens is not None and input_tokens is not None
            else (candidate_tokens + thought_tokens if candidate_tokens is not None else None)
        )
        return ProviderResponse(
            text=_safe_response_text(response),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason or _enum_value(prompt_block_reason),
            refusal=(
                normalized_finish_reason in _GOOGLE_BLOCK_REASONS
                or (
                    normalized_prompt_reason is not None
                    and normalized_prompt_reason not in {
                        "BLOCK_REASON_UNSPECIFIED",
                        "UNSPECIFIED",
                    }
                )
            ),
            raw=_as_jsonable(response),
        )


class ExecutionBudget:
    """@brief Enforce provider-attempt and estimated-cost caps before calls."""

    def __init__(self, max_requests: Optional[int], max_cost_usd: Optional[float]):
        self.max_requests = max_requests
        self.max_cost_usd = max_cost_usd
        self.request_count = 0
        self.cost_usd = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimated_cost_usd: Optional[float]) -> float:
        """
        @brief Reserve capacity immediately before a provider attempt.

        @param estimated_cost_usd Conservative configured request estimate.
        @return Reserved cost used when settling actual token usage.
        @throws BudgetExceeded If either cap would be exceeded.
        """
        with self._lock:
            if self.max_requests is not None and self.request_count >= self.max_requests:
                raise BudgetExceeded(f"max-requests cap reached ({self.max_requests}).")
            if self.max_cost_usd is not None:
                if estimated_cost_usd is None:
                    raise ConfigurationError(
                        "max-cost-usd requires input_cost_per_million and "
                        "output_cost_per_million for every selected model."
                    )
                if self.cost_usd + estimated_cost_usd > self.max_cost_usd + 1e-12:
                    raise BudgetExceeded(
                        f"max-cost-usd cap reached ({self.max_cost_usd:.6f})."
                    )

            reserved = estimated_cost_usd or 0.0
            self.request_count += 1
            self.cost_usd += reserved
            return reserved

    def settle(self, reserved: float, actual_cost_usd: Optional[float]) -> bool:
        """@brief Settle actual cost and report whether it crossed the configured cap."""
        with self._lock:
            if actual_cost_usd is not None:
                self.cost_usd += actual_cost_usd - reserved
            return bool(
                self.max_cost_usd is not None
                and self.cost_usd > self.max_cost_usd + 1e-12
            )

    def release(self, reserved: float) -> None:
        """@brief Release estimated cost for a provider attempt that returned no 200."""
        with self._lock:
            self.cost_usd = max(0.0, self.cost_usd - reserved)


class AtomicJsonStore:
    """@brief Small JSON store that publishes complete files with os.replace."""

    def __init__(self, path: Path):
        self.path = path

    def read(self, default: Any) -> Any:
        """@brief Read JSON or return the supplied default when absent."""
        if not self.path.exists():
            return default
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise RunnerError(f"Cannot read JSON state {self.path}: {exc}") from exc

    def write(self, value: Any) -> None:
        """@brief Write UTF-8 JSON atomically beside the target file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


def load_benchmark(registry_path: Path, benchmark_id: str, repo_root: Path) -> BenchmarkDefinition:
    """
    @brief Load one entry from benchmarks/registry.json schema version 1.

    @param registry_path Registry location, relative to repo_root when needed.
    @param benchmark_id Registry benchmark id.
    @param repo_root Repository root used for relative paths.
    @return Validated benchmark definition.
    @throws ConfigurationError If the registry is absent, unsupported, or malformed.
    """
    registry_path = _resolve_repo_path(repo_root, registry_path)
    if not registry_path.exists():
        raise ConfigurationError(
            f"Benchmark registry not found: {registry_path}. "
            "Create benchmarks/registry.json before running the benchmark."
        )
    data = _read_json(registry_path)
    if data.get("version") != 1 or not isinstance(data.get("benchmarks"), list):
        raise ConfigurationError(
            f"Unsupported registry shape in {registry_path}; expected version 1 and benchmarks[]."
        )
    raw = next((item for item in data["benchmarks"] if item.get("id") == benchmark_id), None)
    if raw is None:
        available = ", ".join(str(item.get("id")) for item in data["benchmarks"])
        raise ConfigurationError(
            f"Benchmark '{benchmark_id}' is not in {registry_path}. Available: {available or '(none)'}"
        )

    scoring = raw.get("scoring", {})
    raw_sections = raw.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ConfigurationError(f"Benchmark '{benchmark_id}' has no sections[].")

    sections: list[SectionDefinition] = []
    for index, item in enumerate(raw_sections):
        try:
            problem_value = item.get("problemDir") or item.get("problem_path")
            if not problem_value:
                raise KeyError("problemDir")
            section_id = str(item.get("id") or _slugify(str(item["sheet"])))
            metadata_value = item.get("metadataPath") or item.get("metadata_path")
            problem_dir = _resolve_repo_path(repo_root, Path(str(problem_value)))
            if not _is_relative_to(problem_dir, (repo_root / "problems").resolve()):
                raise ConfigurationError(
                    f"Section '{item.get('sheet', index + 1)}' problemDir must be under "
                    "this repository's ignored problems/."
                )
            sections.append(
                SectionDefinition(
                    id=section_id,
                    sheet=str(item["sheet"]),
                    subject=str(item["subject"]),
                    section=str(item["section"]),
                    question_count=int(item["questionCount"]),
                    max_score=float(item["maxScore"]),
                    problem_dir=problem_dir,
                    metadata_path=(
                        _resolve_repo_path(repo_root, Path(str(metadata_value)))
                        if metadata_value
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"Invalid section #{index + 1} for benchmark '{benchmark_id}': {exc}"
            ) from exc

    try:
        definition = BenchmarkDefinition(
            id=benchmark_id,
            title=raw.get("title", {}),
            scoring_type=str(scoring.get("type", "sum")),
            total_questions=int(scoring["totalQuestions"]),
            max_score=float(scoring["maxScore"]),
            points_per_question=(
                float(scoring["pointsPerQuestion"])
                if scoring.get("pointsPerQuestion") is not None
                else None
            ),
            sections=tuple(sections),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid scoring for benchmark '{benchmark_id}': {exc}") from exc

    if sum(section.question_count for section in sections) != definition.total_questions:
        raise ConfigurationError(f"Section question counts do not total {definition.total_questions}.")
    if definition.scoring_type == "sum" and not math.isclose(
        sum(section.max_score for section in sections), definition.max_score, abs_tol=1e-9
    ):
        raise ConfigurationError(f"Section scores do not total {definition.max_score}.")
    return definition


def load_model_configs(config_path: Path) -> tuple[ModelConfig, ...]:
    """
    @brief Load secret-free model configurations from JSON.

    @param config_path Local model configuration path.
    @return Validated model configurations.
    @throws ConfigurationError If the file or models list is invalid.
    """
    if not config_path.exists():
        raise ConfigurationError(
            f"Model configuration not found: {config_path}. "
            "Copy benchmark_models.example.json to benchmark_models.json first."
        )
    data = _read_json(config_path)
    raw_models = data.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ConfigurationError(f"{config_path} must contain a non-empty models[] list.")
    models = tuple(ModelConfig.from_dict(item) for item in raw_models)
    names = [model.name for model in models]
    if len(set(names)) != len(names):
        raise ConfigurationError("Model names must be unique.")
    return models


def load_section_questions(
    benchmark: BenchmarkDefinition,
    section: SectionDefinition,
    repo_root: Path,
) -> tuple[Question, ...]:
    """
    @brief Load local question text and cross-check public metadata.

    @param benchmark Owning benchmark definition.
    @param section Section whose ignored local content should be loaded.
    @param repo_root Repository root for resolving root-relative question paths.
    @return Ordered validated questions.
    @throws ConfigurationError If local preparation is missing or inconsistent.
    """
    local_metadata_path = section.problem_dir / "questions.json"
    if not local_metadata_path.exists():
        raise ConfigurationError(
            f"Local questions are not prepared for '{section.id}': {local_metadata_path}. "
            "Run the HWP preparation command before previewing or executing models."
        )
    local_data = _read_json(local_metadata_path)
    local_questions = local_data.get("questions")
    if not isinstance(local_questions, list):
        raise ConfigurationError(f"{local_metadata_path} must contain questions[].")

    public_by_number: dict[int, Mapping[str, Any]] = {}
    if section.metadata_path:
        metadata = _read_json(section.metadata_path)
        public_subjects = metadata.get("subjects", [])
        public_section = next(
            (
                item
                for item in public_subjects
                if item.get("id") == section.id or item.get("sheet_name") == section.sheet
            ),
            None,
        )
        if public_section is None:
            raise ConfigurationError(
                f"Section '{section.id}' is missing from public metadata {section.metadata_path}."
            )
        public_by_number = {
            int(item["number"]): item for item in public_section.get("questions", [])
        }

    questions: list[Question] = []
    for raw in local_questions:
        try:
            number = int(raw["number"])
            public = public_by_number.get(number, {})
            correct_answer = int(raw.get("correct_answer", public.get("correct_answer")))
            points = float(raw.get("points", public.get("points")))
            choices = tuple(int(item) for item in raw.get("choices", public.get("choices", [])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"Invalid question metadata in {local_metadata_path}: {raw!r}"
            ) from exc

        if public:
            if correct_answer != int(public["correct_answer"]):
                raise ConfigurationError(
                    f"Question {section.id}/{number} answer differs from public metadata."
                )
            if not math.isclose(points, float(public["points"]), abs_tol=1e-9):
                raise ConfigurationError(
                    f"Question {section.id}/{number} points differ from public metadata."
                )
        question_type = str(raw.get("type", public.get("type", "")))
        if question_type != "multiple_choice":
            raise ConfigurationError(
                f"Question {section.id}/{number} must have type=multiple_choice."
            )
        if correct_answer not in range(1, 6) or choices != (1, 2, 3, 4, 5):
            raise ConfigurationError(
                f"Question {section.id}/{number} must have answer 1-5 and choices [1,2,3,4,5]."
            )
        if raw.get("image_paths"):
            raise ConfigurationError(
                f"Question {section.id}/{number} has images, but this text-only runner "
                "does not silently omit them."
            )

        text, source_path, source_hash = _load_question_text(raw, local_metadata_path, repo_root)
        questions.append(
            Question(
                benchmark_id=benchmark.id,
                benchmark_title=str(benchmark.title.get("ko") or benchmark.id),
                section_id=section.id,
                subject=section.subject,
                section=section.section,
                number=number,
                correct_answer=correct_answer,
                points=points,
                choices=choices,
                text=text,
                source_path=source_path,
                source_hash=source_hash,
            )
        )

    questions.sort(key=lambda item: item.number)
    expected_numbers = list(range(1, section.question_count + 1))
    actual_numbers = [question.number for question in questions]
    if actual_numbers != expected_numbers:
        raise ConfigurationError(
            f"Section '{section.id}' must contain consecutive questions 1-"
            f"{section.question_count}; found {actual_numbers}."
        )
    if public_by_number and set(public_by_number) != set(expected_numbers):
        raise ConfigurationError(
            f"Public metadata for '{section.id}' does not contain exactly the expected questions."
        )
    if not math.isclose(sum(item.points for item in questions), section.max_score, abs_tol=1e-9):
        raise ConfigurationError(
            f"Question points for '{section.id}' do not total {section.max_score}."
        )
    return tuple(questions)


def parse_question_answer(
    text: str,
    *,
    provider_refusal: bool = False,
    strict: bool = True,
) -> ParseResult:
    """
    @brief Strictly parse one multiple-choice answer.

    마커 전용 줄, 마지막 줄 단독 숫자, 본문 문장형 정답 선언 순서로 시도하며
    각 단계에서 발견된 값이 하나로 일치할 때만 answered로 확정합니다.

    기본값(strict=True)은 파서 v1 동작(공식·형식 준수 점수용): 마커 전용
    줄은 정확히 한 줄만 허용하고 본문 문장형 폴백을 사용하지 않습니다.
    strict=False는 v2(병행 표기용): 동일 값 반복 마커를 허용하고 문장형
    정답 선언까지 인정합니다.

    @param text Provider response text.
    @param provider_refusal Explicit refusal signal supplied by the provider.
    @param strict True면 v1(형식 준수) 규칙, False면 v2(산문 폴백 포함) 규칙.
    @return One answer or a no_answer/refusal/parse_failed status.
    """
    normalized = _normalize_answer_symbols(text).strip()
    if provider_refusal or _looks_like_refusal(normalized):
        return ParseResult("refusal", {}, "Provider or response indicated refusal.")
    if not normalized:
        return ParseResult("no_answer", {}, "Response was empty.")

    marker_pattern = re.compile(
        r"^\s*(?:최종\s*)?(?:정답|답)\s*(?::|：|은|는)?\s*"
        r"([0-9]+)\s*(?:번)?\s*(?:입니다|이다)?[.!。]?\s*$",
        re.IGNORECASE,
    )
    marker_values = [
        int(match.group(1))
        for line in normalized.splitlines()
        if (match := marker_pattern.match(line))
    ]
    if marker_values:
        distinct = marker_values if strict else set(marker_values)
        if len(distinct) == 1 and marker_values[0] in range(1, 6):
            return ParseResult("answered", {0: marker_values[0]})
        return ParseResult("parse_failed", {}, "Answer markers were ambiguous or out of range.")

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines and re.fullmatch(r"[1-5](?:\s*번)?[.!。]?", lines[-1]):
        return ParseResult("answered", {0: int(lines[-1][0])})

    if not strict:
        prose_pattern = re.compile(
            r"(?:최종\s*)?(?:정답|답)\s*(?::|：|은|는)\s*\*{0,2}\s*([1-5])"
            r"(?!\s*(?:번)?\s*(?:이|가)?\s*아니)\s*(?:번)?\s*\*{0,2}\s*(?:입니다|이다)?",
            re.IGNORECASE,
        )
        prose_values = [int(value) for value in prose_pattern.findall(normalized)]
        if prose_values:
            if len(set(prose_values)) == 1:
                return ParseResult("answered", {0: prose_values[0]})
            return ParseResult("parse_failed", {}, "Prose answer markers disagreed.")

    if re.search(r"(?:정답|답)\s*(?::|：|은|는)?\s*[0-9]+", normalized, re.IGNORECASE):
        return ParseResult("parse_failed", {}, "Answer marker was not a valid final answer line.")
    return ParseResult("parse_failed", {}, "No unambiguous final answer was found.")


def parse_subject_answers(
    text: str,
    expected_numbers: Iterable[int],
    *,
    provider_refusal: bool = False,
) -> ParseResult:
    """
    @brief Strictly parse a complete numbered answer list for one subject.

    @param text Provider response text.
    @param expected_numbers Exact question numbers required in the response.
    @param provider_refusal Explicit refusal signal supplied by the provider.
    @return Complete answer mapping or one response-level failure status.
    """
    expected = tuple(expected_numbers)
    normalized = _normalize_answer_symbols(text).strip()
    if provider_refusal or _looks_like_refusal(normalized):
        return ParseResult("refusal", {}, "Provider or response indicated refusal.")
    if not normalized:
        return ParseResult("no_answer", {}, "Response was empty.")

    line_pattern = re.compile(
        r"^\s*(?:문항\s*)?([0-9]+)\s*(?:번)?\s*[:：.)\-]\s*"
        r"([0-9]+)\s*(?:번)?\s*[.!。]?\s*$"
    )
    answers: dict[int, int] = {}
    duplicates: set[int] = set()
    for line in normalized.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue
        number, answer = int(match.group(1)), int(match.group(2))
        if number in answers:
            duplicates.add(number)
        answers[number] = answer

    expected_set = set(expected)
    if duplicates:
        return ParseResult("parse_failed", {}, f"Duplicate question numbers: {sorted(duplicates)}")
    if set(answers) != expected_set:
        missing = sorted(expected_set - set(answers))
        extra = sorted(set(answers) - expected_set)
        return ParseResult(
            "parse_failed", {}, f"Incomplete answer map; missing={missing}, extra={extra}."
        )
    invalid = {number: answer for number, answer in answers.items() if answer not in range(1, 6)}
    if invalid:
        return ParseResult("parse_failed", {}, f"Answers outside 1-5: {invalid}")
    return ParseResult("answered", answers)


def build_question_prompt(question: Question) -> str:
    """@brief Build the stable user-only prompt for one independent question."""
    return (
        f"{question.benchmark_title} {question.subject} 문제를 외부 도구나 검색 없이 푸세요.\n"
        "선택지는 1번부터 5번까지입니다. 사고 과정은 자유롭게 작성할 수 있지만, "
        "마지막 줄에는 반드시 '정답: N' 형식으로 하나의 답만 쓰세요.\n\n"
        f"[문항 {question.number}]\n{question.text.rstrip()}\n"
    )


def build_subject_prompt(section: SectionDefinition, questions: Sequence[Question]) -> str:
    """@brief Build the stable, unsplit user-only prompt for an entire subject."""
    rendered = "\n\n".join(
        f"[문항 {question.number}]\n{question.text.rstrip()}" for question in questions
    )
    return (
        f"{questions[0].benchmark_title} {section.subject} 전체 문항을 "
        "외부 도구나 검색 없이 푸세요.\n"
        "모든 문항의 답을 빠짐없이 작성하세요. 최종 답안은 문항별로 한 줄씩 "
        "'문항번호: N' 형식만 사용하고, N은 1부터 5까지여야 합니다.\n\n"
        f"{rendered}\n"
    )


def build_requests(
    benchmark: BenchmarkDefinition,
    models: Sequence[ModelConfig],
    run_mode: str,
    repo_root: Path,
    selected_subjects: Optional[set[str]] = None,
) -> tuple[PlannedRequest, ...]:
    """
    @brief Prepare and context-check every provider request without network access.

    @param benchmark Benchmark definition.
    @param models Selected model configurations.
    @param run_mode question or subject.
    @param repo_root Repository root.
    @param selected_subjects Optional ids, sheet names, or subject labels.
    @return Fully prepared requests in model/section/question order.
    @throws ContextLimitError If any request would overflow a model context.
    """
    if run_mode not in {"question", "subject"}:
        raise ConfigurationError("run_mode must be 'question' or 'subject'.")
    sections = [
        section
        for section in benchmark.sections
        if not selected_subjects
        or bool({section.id, section.sheet, section.subject} & selected_subjects)
    ]
    if selected_subjects and len(sections) == 0:
        raise ConfigurationError(
            f"No sections match --subjects: {', '.join(sorted(selected_subjects))}"
        )

    loaded = {
        section.id: load_section_questions(benchmark, section, repo_root) for section in sections
    }
    requests: list[PlannedRequest] = []
    for model in models:
        for section in sections:
            questions = loaded[section.id]
            units: Iterable[tuple[str, tuple[Question, ...], str]]
            if run_mode == "question":
                units = (
                    (f"{section.id}-q{question.number:03d}", (question,), build_question_prompt(question))
                    for question in questions
                )
            else:
                units = ((section.id, questions, build_subject_prompt(section, questions)),)

            for request_key, unit_questions, prompt in units:
                estimated_tokens = estimate_input_tokens(prompt)
                if estimated_tokens + model.max_output_tokens > model.context_window:
                    raise ContextLimitError(
                        f"{model.name}/{request_key} needs about {estimated_tokens} input + "
                        f"{model.max_output_tokens} output tokens, exceeding context_window="
                        f"{model.context_window}. Subject requests are never split."
                    )
                source_hash = _combined_source_hash(unit_questions)
                requests.append(
                    PlannedRequest(
                        model=model,
                        section=section,
                        questions=unit_questions,
                        request_key=request_key,
                        prompt=prompt,
                        prompt_hash=_sha256_text(prompt),
                        source_hash=source_hash,
                        estimated_input_tokens=estimated_tokens,
                        estimated_cost_usd=_estimate_cost(
                            model, estimated_tokens, model.max_output_tokens
                        ),
                    )
                )
    return tuple(requests)


def estimate_input_tokens(prompt: str) -> int:
    """@brief Bound input tokens conservatively by the UTF-8 byte length."""
    return max(1, len(prompt.encode("utf-8")))


class BenchmarkRunner:
    """@brief Orchestrate safe previews, resumable execution, and local handoff files."""

    def __init__(
        self,
        repo_root: Path,
        registry_path: Path = Path("benchmarks/registry.json"),
        adapter_factory: Optional[Callable[[ModelConfig, Any], ProviderAdapter]] = None,
        credential_resolver: Optional[Callable[[ModelConfig], Any]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
    ):
        self.repo_root = repo_root.resolve()
        self.registry_path = registry_path
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._credential_resolver = credential_resolver or _read_credential
        self._sleep = sleep_fn
        self._random = random_fn
        self._last_request_at: dict[str, float] = {}
        self._rate_multiplier: dict[str, float] = {}
        self._rate_limit_not_before: dict[str, float] = {}
        self._rate_limit_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._attempt_latencies: list[float] = []
        self._rate_limit_errors = 0
        self._retryable_errors = 0
        self._run_started_at: Optional[float] = None
        self._ignore_rate_limit = False

    def run(
        self,
        *,
        benchmark_id: str,
        config_path: Path,
        run_mode: str,
        model_names: Optional[set[str]] = None,
        subjects: Optional[set[str]] = None,
        execute: bool = False,
        max_requests: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        retry_failed: bool = False,
        resume: bool = True,
        output_dir: Optional[Path] = None,
        preview_limit: int = 5,
        include_prompt_preview: bool = False,
        workers: int = 1,
        ignore_rate_limit: bool = False,
    ) -> RunReport:
        """
        @brief Preview or execute a complete selected benchmark workload.

        @return Run report containing preview, counters, and budget state.
        @throws ConfigurationError If execution safety requirements are not met.
        """
        benchmark = load_benchmark(self.registry_path, benchmark_id, self.repo_root)
        models = load_model_configs(_resolve_repo_path(self.repo_root, config_path))
        if model_names:
            models = tuple(model for model in models if model.name in model_names)
            missing = model_names - {model.name for model in models}
            if missing:
                raise ConfigurationError(f"Unknown selected models: {', '.join(sorted(missing))}")
        requests = build_requests(benchmark, models, run_mode, self.repo_root, subjects)
        unpriced_oauth_selected = any(
            item.model.is_oauth and item.estimated_cost_usd is None
            for item in requests
        )
        estimated_costs = [item.estimated_cost_usd for item in requests]
        total_estimated_cost = (
            sum(float(value) for value in estimated_costs)
            if all(value is not None for value in estimated_costs)
            else None
        )
        report = RunReport(
            benchmark_id=benchmark.id,
            run_mode=run_mode,
            dry_run=not execute,
            planned_requests=len(requests),
            estimated_max_cost_usd=total_estimated_cost,
            preview=[
                _preview_item(item, include_prompt=include_prompt_preview)
                for item in requests[: max(0, preview_limit)]
            ],
        )
        if not execute:
            return report

        if workers < 1:
            raise ConfigurationError("--workers must be at least 1.")
        self._reset_runtime_stats()
        self._ignore_rate_limit = ignore_rate_limit
        _validate_execution_caps(max_requests, max_cost_usd)
        unpriced_requests = [
            item
            for item in requests
            if item.estimated_cost_usd is None
        ]
        if max_cost_usd is not None and unpriced_requests:
            raise ConfigurationError(
                "--max-cost-usd requires input/output prices for every selected model."
            )
        if not requests:
            return report

        run_root = self._run_root(benchmark, output_dir)
        budget = ExecutionBudget(max_requests, max_cost_usd)
        adapters: dict[str, ProviderAdapter] = {}
        checkpoints: dict[Path, dict[str, Any]] = {}
        expected_entries_by_section: dict[
            tuple[str, str], dict[str, tuple[str, str, tuple[Question, ...]]]
        ] = {}
        for planned in requests:
            expected_entries_by_section.setdefault(
                (planned.model.name, planned.section.id), {}
            )[planned.request_key] = (
                planned.prompt_hash,
                planned.source_hash,
                planned.questions,
            )
        if workers > 1:
            return self._run_parallel(
                benchmark=benchmark,
                requests=requests,
                run_mode=run_mode,
                run_root=run_root,
                report=report,
                budget=budget,
                retry_failed=retry_failed,
                resume=resume,
                expected_entries_by_section=expected_entries_by_section,
                max_cost_usd=max_cost_usd,
                unpriced_oauth_selected=unpriced_oauth_selected,
                workers=workers,
            )
        stop = False
        for request in requests:
            expected_entries = expected_entries_by_section[
                (request.model.name, request.section.id)
            ]
            checkpoint_path = (
                run_root / "checkpoints" / _model_directory(request.model) / f"{run_mode}.json"
            )
            checkpoint_store = AtomicJsonStore(checkpoint_path)
            if checkpoint_path not in checkpoints:
                checkpoints[checkpoint_path] = self._load_checkpoint(
                    checkpoint_store, benchmark, request.model, run_mode, resume
                )
            checkpoint = checkpoints[checkpoint_path]
            existing = checkpoint["entries"].get(request.request_key)
            if self._should_skip(existing, request, retry_failed):
                report.skipped += 1
                self._write_verified_from_checkpoint(
                    benchmark,
                    request.section,
                    request.model,
                    run_mode,
                    checkpoint,
                    expected_entries,
                )
                continue

            try:
                if request.model.name not in adapters:
                    credential = self._credential_resolver(request.model)
                    adapters[request.model.name] = self._adapter_factory(
                        request.model, credential
                    )
                response, attempts, actual_cost, actual_cost_exceeded = self._send_with_retries(
                    adapters[request.model.name], request, budget
                )
                report.provider_attempts += attempts
                parse_result = self._parse_response(run_mode, request, response)
                lenient_result = self._parse_response_lenient(run_mode, request, response)
                entry = self._completed_entry(
                    request, response, parse_result, attempts, actual_cost,
                    parsed_lenient=lenient_result,
                )
                checkpoint["entries"][request.request_key] = entry
                checkpoint["updated_at"] = _utc_now()
                checkpoint_store.write(checkpoint)
                self._write_raw(run_root, request, run_mode, response=response, entry=entry)
                self._write_verified_from_checkpoint(
                    benchmark,
                    request.section,
                    request.model,
                    run_mode,
                    checkpoint,
                    expected_entries,
                )
                if parse_result.status == "answered":
                    report.completed += 1
                else:
                    report.review_required += 1
                if actual_cost_exceeded:
                    report.stopped_reason = (
                        "provider-reported usage exceeded max-cost-usd "
                        f"({budget.cost_usd:.6f} > {max_cost_usd:.6f}); no further calls made."
                    )
                    stop = True
            except BudgetExceeded as exc:
                report.stopped_reason = str(exc)
                self._write_verified_from_checkpoint(
                    benchmark,
                    request.section,
                    request.model,
                    run_mode,
                    checkpoint,
                    expected_entries,
                )
                stop = True
            except ConfigurationError:
                raise
            except Exception as exc:
                provider_error = (
                    exc if isinstance(exc, ProviderError) else ProviderError(str(exc), retryable=False)
                )
                attempts = int(getattr(provider_error, "attempts", 0))
                report.provider_attempts += attempts
                entry = self._failed_entry(request, provider_error, attempts)
                checkpoint["entries"][request.request_key] = entry
                checkpoint["updated_at"] = _utc_now()
                checkpoint_store.write(checkpoint)
                self._write_raw(run_root, request, run_mode, error=provider_error, entry=entry)
                self._write_verified_from_checkpoint(
                    benchmark,
                    request.section,
                    request.model,
                    run_mode,
                    checkpoint,
                    expected_entries,
                )
                report.failed += 1
                if provider_error.quota_exhausted:
                    report.stopped_reason = (
                        f"{request.model.name} subscription usage is exhausted; "
                        "no further calls were made."
                    )
                    stop = True
            if stop:
                break

        report.charged_or_reserved_cost_usd = (
            None if unpriced_oauth_selected else budget.cost_usd
        )
        report.provider_attempts = budget.request_count
        return self._finalize_runtime_report(report)

    def _run_parallel(
        self,
        *,
        benchmark: BenchmarkDefinition,
        requests: Sequence[PlannedRequest],
        run_mode: str,
        run_root: Path,
        report: RunReport,
        budget: ExecutionBudget,
        retry_failed: bool,
        resume: bool,
        expected_entries_by_section: Mapping[
            tuple[str, str], Mapping[str, tuple[str, str, tuple[Question, ...]]]
        ],
        max_cost_usd: Optional[float],
        unpriced_oauth_selected: bool,
        workers: int,
    ) -> RunReport:
        """@brief Execute independent requests concurrently and serialize local writes."""
        checkpoints: dict[Path, dict[str, Any]] = {}
        checkpoint_stores: dict[Path, AtomicJsonStore] = {}
        pending: list[PlannedRequest] = []

        for request in requests:
            checkpoint_path = (
                run_root
                / "checkpoints"
                / _model_directory(request.model)
                / f"{run_mode}.json"
            )
            store = checkpoint_stores.setdefault(
                checkpoint_path, AtomicJsonStore(checkpoint_path)
            )
            if checkpoint_path not in checkpoints:
                checkpoints[checkpoint_path] = self._load_checkpoint(
                    store, benchmark, request.model, run_mode, resume
                )
            checkpoint = checkpoints[checkpoint_path]
            existing = checkpoint["entries"].get(request.request_key)
            if self._should_skip(existing, request, retry_failed):
                report.skipped += 1
                self._write_verified_from_checkpoint(
                    benchmark,
                    request.section,
                    request.model,
                    run_mode,
                    checkpoint,
                    expected_entries_by_section[
                        (request.model.name, request.section.id)
                    ],
                )
            else:
                pending.append(request)

        if not pending:
            report.charged_or_reserved_cost_usd = (
                None if unpriced_oauth_selected else budget.cost_usd
            )
            return self._finalize_runtime_report(report)

        models = {request.model.name: request.model for request in pending}
        credentials = {
            name: self._credential_resolver(model) for name, model in models.items()
        }
        thread_state = threading.local()

        def adapter_for(model: ModelConfig) -> ProviderAdapter:
            adapters = getattr(thread_state, "adapters", None)
            if adapters is None:
                adapters = {}
                thread_state.adapters = adapters
            if model.name not in adapters:
                adapters[model.name] = self._adapter_factory(
                    model, credentials[model.name]
                )
            return adapters[model.name]

        def send_one(
            request: PlannedRequest,
        ) -> tuple[str, PlannedRequest, Any]:
            try:
                result = self._send_with_retries(
                    adapter_for(request.model), request, budget
                )
                return "completed", request, result
            except Exception as exc:
                return "error", request, exc

        stop = False
        fatal_error: Optional[ConfigurationError] = None
        max_workers = min(workers, len(pending))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        future_map = {
            executor.submit(send_one, request): request for request in pending
        }
        try:
            for future in concurrent.futures.as_completed(future_map):
                if future.cancelled():
                    continue
                outcome, request, payload = future.result()
                checkpoint_path = (
                    run_root
                    / "checkpoints"
                    / _model_directory(request.model)
                    / f"{run_mode}.json"
                )
                checkpoint = checkpoints[checkpoint_path]
                store = checkpoint_stores[checkpoint_path]
                expected_entries = expected_entries_by_section[
                    (request.model.name, request.section.id)
                ]

                if outcome == "completed":
                    response, attempts, actual_cost, actual_cost_exceeded = payload
                    parse_result = self._parse_response(run_mode, request, response)
                    lenient_result = self._parse_response_lenient(run_mode, request, response)
                    entry = self._completed_entry(
                        request,
                        response,
                        parse_result,
                        attempts,
                        actual_cost,
                        parsed_lenient=lenient_result,
                    )
                    checkpoint["entries"][request.request_key] = entry
                    checkpoint["updated_at"] = _utc_now()
                    store.write(checkpoint)
                    self._write_raw(
                        run_root,
                        request,
                        run_mode,
                        response=response,
                        entry=entry,
                    )
                    self._write_verified_from_checkpoint(
                        benchmark,
                        request.section,
                        request.model,
                        run_mode,
                        checkpoint,
                        expected_entries,
                    )
                    if parse_result.status == "answered":
                        report.completed += 1
                    else:
                        report.review_required += 1
                    if actual_cost_exceeded:
                        report.stopped_reason = (
                            "provider-reported usage exceeded max-cost-usd "
                            f"({budget.cost_usd:.6f} > {max_cost_usd:.6f}); "
                            "no further calls were scheduled."
                        )
                        stop = True
                else:
                    exc = payload
                    if isinstance(exc, BudgetExceeded):
                        if report.stopped_reason is None:
                            report.stopped_reason = str(exc)
                        stop = True
                    elif isinstance(exc, ConfigurationError):
                        fatal_error = exc
                        stop = True
                    else:
                        provider_error = (
                            exc
                            if isinstance(exc, ProviderError)
                            else ProviderError(str(exc), retryable=False)
                        )
                        attempts = int(getattr(provider_error, "attempts", 0))
                        entry = self._failed_entry(
                            request, provider_error, attempts
                        )
                        checkpoint["entries"][request.request_key] = entry
                        checkpoint["updated_at"] = _utc_now()
                        store.write(checkpoint)
                        self._write_raw(
                            run_root,
                            request,
                            run_mode,
                            error=provider_error,
                            entry=entry,
                        )
                        self._write_verified_from_checkpoint(
                            benchmark,
                            request.section,
                            request.model,
                            run_mode,
                            checkpoint,
                            expected_entries,
                        )
                        report.failed += 1
                        if provider_error.quota_exhausted:
                            report.stopped_reason = (
                                f"{request.model.name} subscription usage is "
                                "exhausted; no further calls were scheduled."
                            )
                            stop = True

                if stop:
                    for queued in future_map:
                        queued.cancel()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        if fatal_error is not None:
            raise fatal_error
        report.charged_or_reserved_cost_usd = (
            None if unpriced_oauth_selected else budget.cost_usd
        )
        report.provider_attempts = budget.request_count
        return self._finalize_runtime_report(report)

    def backfill_lenient(
        self,
        *,
        benchmark_id: str,
        config_path: Path,
        run_mode: str,
        model_names: Optional[set[str]] = None,
        subjects: Optional[set[str]] = None,
        output_dir: Optional[Path] = None,
    ) -> dict[str, Any]:
        """
        @brief 기존 checkpoint에 lenient(v2) 파싱 필드를 채우고 verified를 재생성.

        API 호출 없이 raw 응답 텍스트를 strict=False로 재파싱합니다.
        raw 파일이 없는 entry는 공식(v1) 결과를 lenient 값으로 복사합니다.

        @param benchmark_id Registry benchmark id.
        @param config_path Model config path.
        @param run_mode "question" 또는 "subject".
        @param model_names 대상 모델 이름 집합(없으면 전체).
        @param subjects 대상 섹션 필터(없으면 전체).
        @param output_dir run 파일 위치 재정의.
        @return 모델별 backfill 통계 요약.
        @throws ConfigurationError 알 수 없는 모델이 선택된 경우.
        """
        benchmark = load_benchmark(self.registry_path, benchmark_id, self.repo_root)
        models = load_model_configs(_resolve_repo_path(self.repo_root, config_path))
        if model_names:
            models = tuple(model for model in models if model.name in model_names)
            missing = model_names - {model.name for model in models}
            if missing:
                raise ConfigurationError(f"Unknown selected models: {', '.join(sorted(missing))}")
        requests = build_requests(benchmark, models, run_mode, self.repo_root, subjects)
        run_root = self._run_root(benchmark, output_dir)
        expected_entries_by_section: dict[
            tuple[str, str], dict[str, tuple[str, str, tuple[Question, ...]]]
        ] = {}
        sections_by_key: dict[tuple[str, str], SectionDefinition] = {}
        models_by_name = {model.name: model for model in models}
        for planned in requests:
            key = (planned.model.name, planned.section.id)
            sections_by_key[key] = planned.section
            expected_entries_by_section.setdefault(key, {})[planned.request_key] = (
                planned.prompt_hash,
                planned.source_hash,
                planned.questions,
            )

        summary: dict[str, Any] = {}
        for model in models:
            checkpoint_path = (
                run_root / "checkpoints" / _model_directory(model) / f"{run_mode}.json"
            )
            store = AtomicJsonStore(checkpoint_path)
            checkpoint = store.read(None)
            if checkpoint is None:
                continue
            backfilled = rescued = raw_missing = 0
            touched_sections: set[str] = set()
            for request_key, entry in checkpoint.get("entries", {}).items():
                results = entry.get("results")
                if not results:
                    continue
                raw_path = (
                    run_root / "raw" / _model_directory(model) / run_mode
                    / f"{request_key}.json"
                )
                raw = AtomicJsonStore(raw_path).read(None)
                response = (raw or {}).get("response") or {}
                text = response.get("text")
                for row in results:
                    if run_mode == "question" and text is not None:
                        parsed = parse_question_answer(
                            text,
                            provider_refusal=bool(response.get("refusal")),
                            strict=False,
                        )
                        lenient_answer, lenient_status = _answer_for_status(parsed, 0)
                    else:
                        lenient_answer = row.get("extracted_answer")
                        lenient_status = row.get("answer_status")
                        if text is None:
                            raw_missing += 1
                    row["lenient_answer"] = lenient_answer
                    row["lenient_status"] = lenient_status
                    backfilled += 1
                    if row.get("answer_status") != "answered" and lenient_status == "answered":
                        rescued += 1
                if entry.get("subject_id"):
                    touched_sections.add(str(entry["subject_id"]))
            checkpoint["updated_at"] = _utc_now()
            store.write(checkpoint)
            for section_id in sorted(touched_sections):
                key = (model.name, section_id)
                section = sections_by_key.get(key)
                if section is None:
                    continue
                self._write_verified_from_checkpoint(
                    benchmark,
                    section,
                    models_by_name[model.name],
                    run_mode,
                    checkpoint,
                    expected_entries_by_section.get(key, {}),
                )
            summary[model.name] = {
                "backfilled_results": backfilled,
                "lenient_rescued": rescued,
                "raw_missing": raw_missing,
            }
        return summary

    def _run_root(self, benchmark: BenchmarkDefinition, output_dir: Optional[Path]) -> Path:
        """@brief Resolve the ignored directory that owns checkpoints and raw responses."""
        problems_root = (self.repo_root / "problems").resolve()
        if output_dir is not None:
            resolved = _resolve_repo_path(self.repo_root, output_dir)
            if not _is_relative_to(resolved, problems_root):
                raise ConfigurationError(
                    "--output-dir must be under this repository's ignored problems/ "
                    "so prompts and raw responses cannot be tracked accidentally."
                )
            return resolved
        if any(
            not _is_relative_to(section.problem_dir, problems_root)
            for section in benchmark.sections
        ):
            raise ConfigurationError(
                "Benchmark problemDir paths must stay under this repository's ignored problems/."
            )
        conventional = (self.repo_root / "problems" / benchmark.id).resolve()
        if all(_is_relative_to(section.problem_dir, conventional) for section in benchmark.sections):
            return conventional / ".runner"
        common = Path(os.path.commonpath([str(section.problem_dir) for section in benchmark.sections]))
        return (common if len(benchmark.sections) > 1 else common.parent) / ".runner"

    def _load_checkpoint(
        self,
        store: AtomicJsonStore,
        benchmark: BenchmarkDefinition,
        model: ModelConfig,
        run_mode: str,
        resume: bool,
    ) -> dict[str, Any]:
        """@brief Load or initialize one model/mode checkpoint."""
        expected = {
            "schema_version": 1,
            "benchmark_id": benchmark.id,
            "run_mode": run_mode,
            "prompt_version": PROMPT_VERSION,
            "model": model.public_dict(),
            "model_fingerprint": model.fingerprint(),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "entries": {},
        }
        if not resume:
            return expected
        current = store.read(None)
        if current is None:
            return expected
        identity = (
            current.get("benchmark_id"),
            current.get("run_mode"),
            current.get("prompt_version"),
            current.get("model_fingerprint"),
        )
        expected_identity = (benchmark.id, run_mode, PROMPT_VERSION, model.fingerprint())
        if identity != expected_identity:
            raise ConfigurationError(
                f"Checkpoint {store.path} belongs to a different benchmark, prompt, or model "
                "configuration. Use --no-resume only if replacing that local run is intended."
            )
        if not isinstance(current.get("entries"), dict):
            raise RunnerError(f"Checkpoint {store.path} has invalid entries.")
        return current

    def _should_skip(
        self,
        existing: Optional[Mapping[str, Any]],
        request: PlannedRequest,
        retry_failed: bool,
    ) -> bool:
        """@brief Decide whether a matching checkpoint entry is resumable."""
        if not existing:
            return False
        if (
            existing.get("prompt_hash") != request.prompt_hash
            or existing.get("source_hash") != request.source_hash
        ):
            return False
        status = existing.get("status")
        if status in _TERMINAL_ENTRY_STATUSES:
            return True
        return status == "failed" and not retry_failed

    def _send_with_retries(
        self,
        adapter: ProviderAdapter,
        request: PlannedRequest,
        budget: ExecutionBudget,
    ) -> tuple[ProviderResponse, int, Optional[float], bool]:
        """@brief Send with retries restricted to 429, 5xx, and timeout failures."""
        attempts = 0
        auth_refreshed = False
        while True:
            reserved = budget.reserve(request.estimated_cost_usd)
            attempts += 1
            self._respect_rate_limit(request.model)
            attempt_started_at = time.monotonic()
            try:
                response = adapter.send(request.prompt)
                self._record_attempt_latency(time.monotonic() - attempt_started_at)
                self._recover_rate_limit(request.model)
                actual_cost = _actual_cost(request.model, response)
                actual_cost_exceeded = budget.settle(reserved, actual_cost)
                return response, attempts, actual_cost, actual_cost_exceeded
            except Exception as exc:
                self._record_attempt_latency(time.monotonic() - attempt_started_at)
                budget.release(reserved)
                error = exc if isinstance(exc, ProviderError) else _provider_error_from_exception(exc)
                if error.authentication_fatal:
                    raise ConfigurationError(
                        f"{request.model.name} OAuth authentication failed. "
                        f"Run: python3 benchmark_auth.py login {request.model.provider} "
                        f"--profile {request.model.oauth_profile}"
                    ) from exc
                if error.auth_refresh_required:
                    if auth_refreshed:
                        raise ConfigurationError(
                            f"{request.model.name} remained unauthorized after token refresh. "
                            f"Run: python3 benchmark_auth.py login {request.model.provider} "
                            f"--profile {request.model.oauth_profile}"
                        ) from exc
                    if (
                        budget.max_requests is not None
                        and budget.request_count >= budget.max_requests
                    ):
                        raise BudgetExceeded(
                            f"max-requests cap reached ({budget.max_requests}) "
                            "before the OAuth retry."
                        )
                    refresh_auth = getattr(adapter, "refresh_auth", None)
                    if not callable(refresh_auth):
                        raise ConfigurationError(
                            f"{request.model.name} cannot refresh its OAuth credential."
                        ) from exc
                    try:
                        refresh_auth()
                    except Exception as refresh_exc:
                        raise ConfigurationError(
                            f"{request.model.name} OAuth token refresh failed. "
                            f"Run: python3 benchmark_auth.py login {request.model.provider} "
                            f"--profile {request.model.oauth_profile}"
                        ) from refresh_exc
                    auth_refreshed = True
                    continue
                if error.retryable:
                    self._record_retryable_error(error)
                if not error.retryable or attempts > request.model.max_retries:
                    error.attempts = attempts
                    raise error
                base_delay = min(30.0, 2 ** (attempts - 1))
                delay = max(
                    error.retry_after_seconds or 0.0,
                    base_delay * (1.0 + self._random()),
                )
                if error.status_code == 429:
                    self._slow_rate_limit(request.model, delay)
                self._sleep(delay)

    def _respect_rate_limit(self, model: ModelConfig) -> None:
        """@brief Apply the model's configured minimum interval before a provider attempt."""
        if self._ignore_rate_limit or model.requests_per_minute is None:
            return
        while True:
            with self._rate_limit_lock:
                multiplier = self._rate_multiplier.get(model.name, 1.0)
                minimum_interval = 60.0 / (model.requests_per_minute * multiplier)
                now = time.monotonic()
                target = self._rate_limit_not_before.get(model.name, 0.0)
                previous = self._last_request_at.get(model.name)
                if previous is not None:
                    target = max(target, previous + minimum_interval)
                if target <= now:
                    self._last_request_at[model.name] = now
                    return
                delay = target - now
            # Do not hold the shared lock while sleeping. Completed requests must be
            # able to record success and recover adaptive pacing while callers wait.
            self._sleep(delay)

    def _slow_rate_limit(self, model: ModelConfig, delay: float) -> None:
        """@brief Apply a shared cooldown and multiplicative slowdown after HTTP 429."""
        with self._rate_limit_lock:
            current = self._rate_multiplier.get(model.name, 1.0)
            self._rate_multiplier[model.name] = max(0.5, current * 0.75)
            self._rate_limit_not_before[model.name] = max(
                self._rate_limit_not_before.get(model.name, 0.0),
                time.monotonic() + delay,
            )

    def _recover_rate_limit(self, model: ModelConfig) -> None:
        """@brief Gradually recover to the configured request rate after successes."""
        if model.requests_per_minute is None:
            return
        with self._rate_limit_lock:
            current = self._rate_multiplier.get(model.name, 1.0)
            if current < 1.0:
                self._rate_multiplier[model.name] = min(1.0, current + 0.02)

    def _reset_runtime_stats(self) -> None:
        """@brief Reset per-run latency and retry counters."""
        with self._metrics_lock:
            self._attempt_latencies = []
            self._rate_limit_errors = 0
            self._retryable_errors = 0
            self._run_started_at = time.monotonic()
        with self._rate_limit_lock:
            self._last_request_at = {}
            self._rate_multiplier = {}
            self._rate_limit_not_before = {}

    def _record_attempt_latency(self, seconds: float) -> None:
        """@brief Record one provider attempt duration for the final report."""
        with self._metrics_lock:
            self._attempt_latencies.append(max(0.0, seconds))

    def _record_retryable_error(self, error: ProviderError) -> None:
        """@brief Count retryable errors, including transient 429 responses."""
        with self._metrics_lock:
            self._retryable_errors += 1
            if error.status_code == 429:
                self._rate_limit_errors += 1

    def _finalize_runtime_report(self, report: RunReport) -> RunReport:
        """@brief Attach latency and retry telemetry to an execution report."""
        with self._metrics_lock:
            latencies = sorted(self._attempt_latencies)
            report.rate_limit_errors = self._rate_limit_errors
            report.retryable_errors = self._retryable_errors
            started_at = self._run_started_at
        if latencies:
            report.attempt_latency_p50_seconds = round(
                _percentile(latencies, 0.50), 3
            )
            report.attempt_latency_p95_seconds = round(
                _percentile(latencies, 0.95), 3
            )
        if started_at is not None:
            report.elapsed_seconds = round(time.monotonic() - started_at, 3)
        return report

    def _parse_response(
        self, run_mode: str, request: PlannedRequest, response: ProviderResponse
    ) -> ParseResult:
        """@brief Select the strict parser for the execution unit."""
        if run_mode == "question":
            parsed = parse_question_answer(response.text, provider_refusal=response.refusal)
            if parsed.status == "answered":
                return ParseResult("answered", {request.questions[0].number: parsed.answers[0]})
            return parsed
        return parse_subject_answers(
            response.text,
            (question.number for question in request.questions),
            provider_refusal=response.refusal,
        )

    def _parse_response_lenient(
        self, run_mode: str, request: PlannedRequest, response: ProviderResponse
    ) -> ParseResult:
        """
        @brief Parse the same response with the lenient(v2) rules for 병행 표기.

        subject 모드는 v1/v2 구분이 없으므로 strict 결과와 동일합니다.

        @param run_mode Execution unit ("question" or "subject").
        @param request Planned request the response belongs to.
        @param response Provider response to parse.
        @return Lenient parse outcome keyed by question number.
        """
        if run_mode == "question":
            parsed = parse_question_answer(
                response.text, provider_refusal=response.refusal, strict=False
            )
            if parsed.status == "answered":
                return ParseResult("answered", {request.questions[0].number: parsed.answers[0]})
            return parsed
        return self._parse_response(run_mode, request, response)

    def _completed_entry(
        self,
        request: PlannedRequest,
        response: ProviderResponse,
        parsed: ParseResult,
        attempts: int,
        actual_cost_usd: Optional[float],
        parsed_lenient: Optional[ParseResult] = None,
    ) -> dict[str, Any]:
        """@brief Build a checkpoint entry without embedding raw response text."""
        results = []
        for question in request.questions:
            extracted, answer_status = _answer_for_status(parsed, question.number)
            lenient_extracted, lenient_status = _answer_for_status(
                parsed_lenient if parsed_lenient is not None else parsed, question.number
            )
            results.append(
                _result_row(
                    question,
                    request.model.name,
                    extracted,
                    answer_status,
                    response.stop_reason,
                    lenient_answer=lenient_extracted,
                    lenient_status=lenient_status,
                )
            )
        return {
            "status": "completed" if parsed.status == "answered" else parsed.status,
            "reason": parsed.reason,
            "subject_id": request.section.id,
            "question_numbers": [question.number for question in request.questions],
            "prompt_hash": request.prompt_hash,
            "source_hash": request.source_hash,
            "attempts": attempts,
            "token_usage": {
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "total_tokens": (
                    response.input_tokens + response.output_tokens
                    if response.input_tokens is not None and response.output_tokens is not None
                    else None
                ),
            },
            "provider_stop_reason": response.stop_reason,
            "cost_usd": actual_cost_usd,
            "results": results,
            "completed_at": _utc_now(),
        }

    def _failed_entry(
        self, request: PlannedRequest, error: ProviderError, attempts: int
    ) -> dict[str, Any]:
        """@brief Build a retryable checkpoint failure without result rows."""
        return {
            "status": "failed",
            "subject_id": request.section.id,
            "question_numbers": [question.number for question in request.questions],
            "prompt_hash": request.prompt_hash,
            "source_hash": request.source_hash,
            "attempts": attempts,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "status_code": error.status_code,
                "retryable": error.retryable,
            },
            "completed_at": _utc_now(),
        }

    def _write_raw(
        self,
        run_root: Path,
        request: PlannedRequest,
        run_mode: str,
        *,
        entry: Mapping[str, Any],
        response: Optional[ProviderResponse] = None,
        error: Optional[ProviderError] = None,
    ) -> None:
        """@brief Persist prompt and raw provider payload only under the ignored run tree."""
        payload = {
            "schema_version": 1,
            "benchmark_id": request.questions[0].benchmark_id,
            "run_mode": run_mode,
            "model": request.model.public_dict(),
            "request_key": request.request_key,
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": request.prompt_hash,
            "source_hash": request.source_hash,
            "prompt": request.prompt,
            "response": (
                {
                    "text": response.text,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "stop_reason": response.stop_reason,
                    "refusal": response.refusal,
                    "raw": _as_jsonable(response.raw),
                }
                if response is not None
                else None
            ),
            "error": entry.get("error") if error is not None else None,
            "recorded_at": _utc_now(),
        }
        path = (
            run_root
            / "raw"
            / _model_directory(request.model)
            / run_mode
            / f"{request.request_key}.json"
        )
        AtomicJsonStore(path).write(payload)

    def _write_verified_from_checkpoint(
        self,
        benchmark: BenchmarkDefinition,
        section: SectionDefinition,
        model: ModelConfig,
        run_mode: str,
        checkpoint: Mapping[str, Any],
        expected_entries: Mapping[str, tuple[str, str, tuple[Question, ...]]],
    ) -> None:
        """@brief Atomically merge one model's strict results into a sync-compatible file."""
        model_results: list[dict[str, Any]] = []
        token_input = 0
        token_output = 0
        token_complete = True
        source_hashes: set[str] = set()
        prompt_hashes: set[str] = set()
        cost_total = 0.0
        cost_complete = True
        failed_questions: list[Question] = []
        failed_errors: dict[int, Any] = {}
        for request_key, entry in checkpoint.get("entries", {}).items():
            expected_identity = expected_entries.get(request_key)
            if expected_identity is None or expected_identity[:2] != (
                entry.get("prompt_hash"),
                entry.get("source_hash"),
            ):
                continue
            if entry.get("subject_id") != section.id:
                continue
            current_questions = {
                question.number: question for question in expected_identity[2]
            }
            if entry.get("status") == "failed":
                # 재시도를 소진한 요청은 응답 자체가 없다. 담당 문항을 나중에
                # 무응답(0점)으로 기록하되, token/cost는 checkpoint에 없으므로
                # 집계에서 제외한다.
                for question in _failed_entry_questions(entry, current_questions):
                    failed_questions.append(question)
                    failed_errors.setdefault(question.number, entry.get("error"))
                continue
            for stored_result in entry.get("results", []):
                current_question = current_questions.get(stored_result.get("question_number"))
                if current_question is None:
                    continue
                extracted_answer = stored_result.get("extracted_answer")
                lenient_answer = stored_result.get("lenient_answer", extracted_answer)
                lenient_status = stored_result.get(
                    "lenient_status", stored_result.get("answer_status")
                )
                model_results.append({
                    **stored_result,
                    "correct_answer": current_question.correct_answer,
                    "points": current_question.points,
                    "is_correct": extracted_answer == current_question.correct_answer,
                    "lenient_answer": lenient_answer,
                    "lenient_status": lenient_status,
                    "lenient_is_correct": lenient_answer == current_question.correct_answer,
                })
            source_hashes.add(str(entry.get("source_hash")))
            prompt_hashes.add(str(entry.get("prompt_hash")))
            usage = entry.get("token_usage", {})
            if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
                token_complete = False
            else:
                token_input += int(usage["input_tokens"])
                token_output += int(usage["output_tokens"])
            if entry.get("cost_usd") is None:
                cost_complete = False
            else:
                cost_total += float(entry["cost_usd"])

        # 성공한 응답이 하나라도 있어야 영구 실패 문항을 무응답으로 확정한다.
        # 전부 실패한 실행은 시도 자체가 성립하지 않은 것이므로 0점으로 기록하지
        # 않고, 부분 완주한 실행만 분모를 채워 sync import가 가능하게 만든다.
        if model_results and failed_questions:
            model_results.extend(
                _unanswered_result_row(
                    model.name, question, failed_errors.get(question.number)
                )
                for question in failed_questions
            )

        model_results.sort(key=lambda item: int(item["question_number"]))
        score = sum(float(item["points"]) for item in model_results if item["is_correct"])
        correct_count = sum(1 for item in model_results if item["is_correct"])
        review_count = sum(1 for item in model_results if item["needs_manual_review"])
        lenient_score = sum(
            float(item["points"]) for item in model_results if item["lenient_is_correct"]
        )
        lenient_correct_count = sum(1 for item in model_results if item["lenient_is_correct"])
        lenient_rescued_count = sum(
            1
            for item in model_results
            if item["answer_status"] != "answered" and item["lenient_status"] == "answered"
        )
        filename = "results_verified.json" if run_mode == "question" else "hard_results_verified.json"
        store = AtomicJsonStore(section.problem_dir / filename)
        merged = store.read(
            {
                "schema_version": 2,
                "benchmark_id": benchmark.id,
                "run_mode": run_mode,
                "subject": section.subject,
                "section": section.section,
                "sheet_name": section.sheet,
                "total_points": section.max_score,
                "total_verified": 0,
                "correct_count": 0,
                "manual_review_count": 0,
                "model_scores": {},
                "model_scores_lenient": {},
                "model_metrics": {},
                "results": [],
                "run_metadata": {},
            }
        )
        merged["results"] = [
            item for item in merged.get("results", []) if item.get("model_name") != model.name
        ] + model_results
        merged["results"].sort(key=lambda item: (str(item["model_name"]), int(item["question_number"])))
        merged["schema_version"] = 2
        model_scores = merged.setdefault("model_scores", {})
        model_scores_lenient = merged.setdefault("model_scores_lenient", {})
        model_metrics = merged.setdefault("model_metrics", {})
        run_metadata = merged.setdefault("run_metadata", {})
        if model_results:
            model_scores[model.name] = score
            model_scores_lenient[model.name] = lenient_score
            metrics = {
                "score": score,
                "correct_count": correct_count,
                "total_verified": len(model_results),
                "manual_review_count": review_count,
                "lenient_score": lenient_score,
                "lenient_correct_count": lenient_correct_count,
                "lenient_rescued_count": lenient_rescued_count,
            }
            model_metrics[model.name] = metrics
            metadata = {
                "provider": model.provider,
                "model_id": model.model_id,
                "prompt_version": PROMPT_VERSION,
                "prompt_hashes": sorted(prompt_hashes),
                "source_hashes": sorted(source_hashes),
                "generation": {
                    "temperature": model.temperature,
                    "max_output_tokens": model.max_output_tokens,
                },
                "pricing": {
                    "input_per_million": model.input_cost_per_million,
                    "output_per_million": model.output_cost_per_million,
                },
                "token_usage": (
                    {
                        "input_tokens": token_input,
                        "output_tokens": token_output,
                        "total_tokens": token_input + token_output,
                    }
                    if token_complete
                    else None
                ),
                "cost_usd": cost_total if cost_complete else None,
                "updated_at": _utc_now(),
            }
            if model.is_oauth:
                public_model = model.public_dict()
                metadata["oauth"] = {
                    "profile": model.oauth_profile,
                    "protocol_version": public_model["oauth_protocol_version"],
                    "transform_version": public_model["oauth_transform_version"],
                    "transform_sha256": public_model["oauth_transform_sha256"],
                }
                if "oauth_system_sha256" in public_model:
                    metadata["oauth"]["system_sha256"] = public_model[
                        "oauth_system_sha256"
                    ]
                if model.provider == "openai-codex-oauth":
                    metadata["generation"].update(
                        {
                            "reasoning_effort": model.reasoning_effort,
                            "text_verbosity": model.text_verbosity,
                        }
                    )
                elif model.provider == "anthropic-oauth":
                    metadata["generation"]["effort"] = model.effort
            if model.provider == "google" and model.thinking_level is not None:
                metadata["generation"]["thinking_level"] = model.thinking_level
            run_metadata[model.name] = metadata
        else:
            model_scores.pop(model.name, None)
            model_metrics.pop(model.name, None)
            run_metadata.pop(model.name, None)

        summary = model_metrics.get(model.name)
        if summary is None and model_metrics:
            summary = next(iter(model_metrics.values()))
        summary = summary or {
            "total_verified": 0,
            "correct_count": 0,
            "manual_review_count": 0,
        }
        merged["total_verified"] = summary["total_verified"]
        merged["correct_count"] = summary["correct_count"]
        merged["manual_review_count"] = summary["manual_review_count"]
        merged["timestamp"] = _utc_now()
        store.write(merged)


def _result_row(
    question: Question,
    model_name: str,
    extracted_answer: Optional[int],
    answer_status: str,
    stop_reason: Optional[str],
    *,
    lenient_answer: Optional[int] = None,
    lenient_status: Optional[str] = None,
) -> dict[str, Any]:
    """
    @brief Build one sync-compatible result without raw response text.

    extracted_answer/answer_status는 공식(v1, strict) 파서 결과이고,
    lenient_* 필드는 병행 표기용 v2 파서 결과입니다. lenient 값이 주어지지
    않으면 공식 결과를 그대로 사용합니다.
    """
    if lenient_status is None:
        lenient_answer, lenient_status = extracted_answer, answer_status
    return {
        "model_name": model_name,
        "question_number": question.number,
        "extracted_answer": extracted_answer,
        "correct_answer": question.correct_answer,
        "is_correct": extracted_answer == question.correct_answer,
        "points": question.points,
        "needs_manual_review": answer_status != "answered",
        "answer_status": answer_status,
        "provider_stop_reason": "refusal" if answer_status == "refusal" else stop_reason,
        "lenient_answer": lenient_answer,
        "lenient_status": lenient_status,
        "lenient_is_correct": lenient_answer == question.correct_answer,
    }


def _failed_entry_questions(
    entry: Mapping[str, Any],
    current_questions: Mapping[int, "Question"],
) -> list["Question"]:
    """
    @brief 영구 실패한 checkpoint entry가 담당하던 문항을 복원한다.

    @param entry status가 "failed"인 checkpoint entry
    @param current_questions 현재 섹션의 문항 번호 -> 문항 매핑
    @return 문항 번호 순으로 정렬된 문항 목록
    """
    numbers = entry.get("question_numbers") or list(current_questions)
    resolved = []
    for number in numbers:
        try:
            question = current_questions.get(int(number))
        except (TypeError, ValueError):
            continue
        if question is not None:
            resolved.append(question)
    return sorted(resolved, key=lambda question: question.number)


def _unanswered_result_row(
    model_name: str,
    question: "Question",
    error: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """
    @brief 응답을 받지 못한 문항을 무응답(0점) 결과 행으로 만든다.

    @param model_name 모델 이름
    @param question 대상 문항
    @param error checkpoint에 기록된 실패 정보
    @return 무응답 결과 행
    """
    row = _result_row(question, model_name, NO_ANSWER, "no_answer", "request_failed")
    message = (error or {}).get("message")
    if message:
        row["failure_reason"] = str(message)
    return row


def _answer_for_status(parsed: ParseResult, number: int) -> tuple[Optional[int], str]:
    """@brief Map a response status to the repository answer sentinel contract."""
    if parsed.status == "answered":
        return parsed.answers[number], "answered"
    if parsed.status == "no_answer":
        return NO_ANSWER, "no_answer"
    if parsed.status == "refusal":
        return REFUSAL_ANSWER, "refusal"
    return None, "parse_failed"


def _default_adapter_factory(model: ModelConfig, credential: Any) -> ProviderAdapter:
    """@brief Construct a provider adapter only after --execute safety checks."""
    if model.provider == "openai-compatible":
        return OpenAICompatibleAdapter(model, str(credential))
    if model.provider == "anthropic":
        return AnthropicAdapter(model, str(credential))
    if model.provider == "google":
        return GoogleAdapter(model, str(credential))
    if model.provider == "openai-codex-oauth":
        from oauth_providers import OpenAICodexOAuthAdapter

        return OpenAICodexOAuthAdapter(model, credential)
    if model.provider == "anthropic-oauth":
        from oauth_providers import AnthropicOAuthAdapter

        return AnthropicOAuthAdapter(model, credential)
    raise ConfigurationError(f"Unsupported provider: {model.provider}")


def _read_api_key(model: ModelConfig) -> str:
    """@brief Resolve the configured credential environment variable at execution time only."""
    if model.api_key_env is None:
        raise ConfigurationError(f"{model.name} does not use an API-key environment variable.")
    api_key = os.environ.get(model.api_key_env, "").strip()
    if not api_key:
        raise ConfigurationError(
            f"Environment variable {model.api_key_env} is required to execute {model.name}."
        )
    return api_key


def _read_credential(model: ModelConfig) -> Any:
    """@brief Resolve API key or construct a lazy OAuth manager after safety checks."""
    if not model.is_oauth:
        if model.api_key_env is None and model.vertexai and model.vertex_project:
            credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
            if not credentials_path or not Path(credentials_path).is_file():
                raise ConfigurationError(
                    f"{model.name} uses Vertex ADC; set GOOGLE_APPLICATION_CREDENTIALS "
                    "to a readable service-account JSON path."
                )
            return None
        return _read_api_key(model)
    try:
        from oauth_auth import OAuthCredentialManager, OAuthError

        return OAuthCredentialManager(
            model.provider,
            model.oauth_profile or "default",
        )
    except OAuthError as exc:
        raise ConfigurationError(
            f"Cannot initialize OAuth credentials for {model.name}: {exc}"
        ) from exc


def _validate_execution_caps(
    max_requests: Optional[int],
    max_cost_usd: Optional[float],
) -> None:
    """@brief Require an explicit positive execution cap for every real run."""
    if max_requests is None and max_cost_usd is None:
        raise ConfigurationError(
            "--execute requires --max-requests and/or --max-cost-usd. No API call was made."
        )
    if max_requests is not None and max_requests <= 0:
        raise ConfigurationError("--max-requests must be positive.")
    if max_cost_usd is not None and max_cost_usd <= 0:
        raise ConfigurationError("--max-cost-usd must be positive.")


def _load_question_text(
    raw: Mapping[str, Any], metadata_path: Path, repo_root: Path
) -> tuple[str, Optional[Path], str]:
    """@brief Read one UTF-8 local question body or an inline test fixture body."""
    inline = raw.get("text") or raw.get("question_text")
    if inline is not None:
        text = str(inline).strip()
        if not text:
            raise ConfigurationError(f"Question {raw.get('number')} has empty inline text.")
        return text, None, _sha256_text(text)
    path_value = raw.get("question_path")
    if not path_value:
        raise ConfigurationError(
            f"Question {raw.get('number')} in {metadata_path} has no question_path. "
            "Public answer metadata is insufficient for model execution."
        )
    path = Path(str(path_value))
    candidates = [path] if path.is_absolute() else [repo_root / path, metadata_path.parent / path]
    source_path = next((candidate.resolve() for candidate in candidates if candidate.exists()), None)
    if source_path is None:
        raise ConfigurationError(
            f"Question text not found for {raw.get('number')}: {path_value} "
            f"(from {metadata_path})."
        )
    try:
        payload = source_path.read_bytes()
        text = payload.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"Cannot read UTF-8 question text {source_path}: {exc}") from exc
    if not text:
        raise ConfigurationError(f"Question text is empty: {source_path}")
    return text, source_path, hashlib.sha256(payload).hexdigest()


def _provider_error_from_exception(exc: Exception) -> ProviderError:
    """@brief Classify retries exclusively for timeouts, HTTP 429, and HTTP 5xx."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    type_name = type(exc).__name__.lower()
    is_timeout = isinstance(exc, TimeoutError) or "timeout" in type_name
    explicit_retryable = getattr(exc, "retryable", None)
    retryable = (
        bool(explicit_retryable)
        if explicit_retryable is not None
        else is_timeout
        or status_code == 429
        or bool(status_code and 500 <= status_code <= 599)
    )
    category = str(getattr(exc, "category", ""))
    retry_after_seconds: Optional[float] = None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            retry_after_seconds = float(headers.get("retry-after"))
        except (TypeError, ValueError):
            retry_after_seconds = None
    return ProviderError(
        str(exc),
        retryable=retryable,
        status_code=status_code,
        auth_refresh_required=bool(
            getattr(exc, "auth_refresh_required", False)
        ),
        authentication_fatal=bool(getattr(exc, "authentication_fatal", False)),
        quota_exhausted=category == "subscription_quota",
        retry_after_seconds=retry_after_seconds,
    )


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """@brief Return a nearest-rank percentile from a non-empty sorted sequence."""
    index = min(len(sorted_values) - 1, max(0, math.ceil(len(sorted_values) * fraction) - 1))
    return float(sorted_values[index])


def _estimate_cost(
    model: ModelConfig, input_tokens: Optional[int], output_tokens: Optional[int]
) -> Optional[float]:
    """@brief Compute configured token cost, or None when prices/usage are incomplete."""
    if (
        input_tokens is None
        or output_tokens is None
        or model.input_cost_per_million is None
        or model.output_cost_per_million is None
    ):
        return None
    return (
        input_tokens * model.input_cost_per_million
        + output_tokens * model.output_cost_per_million
    ) / 1_000_000


def _actual_cost(model: ModelConfig, response: ProviderResponse) -> Optional[float]:
    """@brief Compute cost from provider-reported usage when available."""
    return _estimate_cost(model, response.input_tokens, response.output_tokens)


def _combined_source_hash(questions: Sequence[Question]) -> str:
    """@brief Hash ordered question numbers and their source hashes."""
    return _sha256_json([[question.number, question.source_hash] for question in questions])


def _preview_item(request: PlannedRequest, *, include_prompt: bool = False) -> dict[str, Any]:
    """@brief Return a prompt-free dry-run preview item."""
    preview = {
        "model": request.model.name,
        "provider": request.model.provider,
        "request_key": request.request_key,
        "section": request.section.sheet,
        "question_numbers": [question.number for question in request.questions],
        "prompt_hash": request.prompt_hash,
        "source_hash": request.source_hash,
        "estimated_input_tokens": request.estimated_input_tokens,
        "max_output_tokens": request.model.max_output_tokens,
        "estimated_max_cost_usd": request.estimated_cost_usd,
    }
    if include_prompt:
        preview["prompt_preview"] = request.prompt[:500]
    return preview


def _normalize_answer_symbols(text: str) -> str:
    """@brief Normalize circled digits and compatibility punctuation for parsing."""
    return unicodedata.normalize("NFKC", str(text or ""))


def _looks_like_refusal(text: str) -> bool:
    """@brief Detect explicit Korean or English refusal language."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _REFUSAL_PATTERNS)


def _read_json(path: Path) -> dict[str, Any]:
    """@brief Read an expected JSON object with contextual errors."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Required JSON file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"Expected a JSON object in {path}.")
    return value


def _resolve_repo_path(repo_root: Path, path: Path) -> Path:
    """@brief Resolve a potentially relative path against the repository root."""
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    """@brief Return whether path is contained in parent on Python 3.10+."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sha256_text(value: str) -> str:
    """@brief Hash UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    """@brief Hash deterministic compact JSON."""
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(serialized)


def _slugify(value: str) -> str:
    """@brief Create a stable filesystem-safe lowercase label."""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    return slug or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _model_directory(model: ModelConfig) -> str:
    """@brief Prevent model-name collisions in local state directories."""
    return f"{_slugify(model.name)}-{model.fingerprint()[:8]}"


def _utc_now() -> str:
    """@brief Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _optional_int(value: Any) -> Optional[int]:
    """@brief Convert provider metadata to int when present."""
    return int(value) if value is not None else None


def _optional_string(value: Any) -> Optional[str]:
    """@brief Convert provider metadata to str when present."""
    return str(value) if value is not None else None


def _enum_value(value: Any) -> Optional[str]:
    """@brief Extract a stable string from SDK enum-like values."""
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _normalized_reason(value: Any) -> Optional[str]:
    """@brief Normalize SDK enum and dotted reason names for refusal checks."""
    raw = _enum_value(value)
    if raw is None:
        return None
    return raw.rsplit(".", 1)[-1].upper()


def _coerce_text(value: Any) -> str:
    """@brief Normalize common SDK text response shapes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, Mapping):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(getattr(item, "text", item)))
        return "".join(parts)
    return str(value)


def _safe_response_text(response: Any) -> str:
    """@brief Read SDK response text while treating safety-blocked properties as empty."""
    try:
        return _coerce_text(getattr(response, "text", ""))
    except (AttributeError, TypeError, ValueError):
        return ""


def _as_jsonable(value: Any) -> Any:
    """@brief Convert SDK models and enums to JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _as_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_jsonable(item) for item in value]
    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _as_jsonable(method())
            except Exception:
                pass
    if hasattr(value, "value"):
        return _as_jsonable(value.value)
    return repr(value)


def _build_parser() -> argparse.ArgumentParser:
    """@brief Define the safe benchmark runner command line."""
    parser = argparse.ArgumentParser(
        description="Preview or run a registry benchmark. Dry-run is the default."
    )
    parser.add_argument("--benchmark", default="bar-exam-15")
    parser.add_argument("--registry", type=Path, default=Path("benchmarks/registry.json"))
    parser.add_argument("--config", type=Path, default=Path("benchmark_models.json"))
    parser.add_argument("--run-mode", required=True, choices=("question", "subject"))
    parser.add_argument("--models", nargs="+", help="Exact model names from the local config.")
    parser.add_argument("--subjects", nargs="+", help="Section ids, sheets, or subject labels.")
    parser.add_argument("--execute", action="store_true", help="Enable real provider calls.")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent provider requests; request start rate limits still apply.",
    )
    parser.add_argument(
        "--no-rate-limit",
        action="store_true",
        help="Ignore configured request start pacing; provider 429 retries still apply.",
    )
    parser.add_argument("--preview-limit", type=int, default=5)
    parser.add_argument(
        "--include-prompt-preview",
        action="store_true",
        help="Print the first 500 characters of local copyrighted prompts.",
    )
    parser.add_argument(
        "--backfill-lenient",
        action="store_true",
        help="Re-parse stored raw responses with the lenient(v2) parser and "
        "refresh checkpoints plus verified files. No API calls are made.",
    )
    return parser


def _load_env_files(repo_root: Path) -> None:
    """
    @brief .env와 .env.local의 KEY=VALUE를 환경변수로 로드한다.

    이미 설정된 실제 환경변수는 덮어쓰지 않으며, .env.local이 .env보다
    우선한다. 값 양끝의 따옴표는 제거한다.

    @param repo_root 저장소 루트 경로.
    """
    loaded: dict[str, str] = {}
    for filename in (".env", ".env.local"):
        path = repo_root / filename
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            loaded[key] = value.strip().strip("'\"")
    for key, value in loaded.items():
        if key not in os.environ:
            os.environ[key] = value


def main(argv: Optional[Sequence[str]] = None) -> int:
    """@brief CLI entrypoint that reports failures without exposing secrets."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _load_env_files(Path.cwd())
    runner = BenchmarkRunner(Path.cwd(), args.registry)
    if args.backfill_lenient:
        try:
            summary = runner.backfill_lenient(
                benchmark_id=args.benchmark,
                config_path=args.config,
                run_mode=args.run_mode,
                model_names=set(args.models) if args.models else None,
                subjects=set(args.subjects) if args.subjects else None,
                output_dir=args.output_dir,
            )
        except RunnerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    try:
        report = runner.run(
            benchmark_id=args.benchmark,
            config_path=args.config,
            run_mode=args.run_mode,
            model_names=set(args.models) if args.models else None,
            subjects=set(args.subjects) if args.subjects else None,
            execute=args.execute,
            max_requests=args.max_requests,
            max_cost_usd=args.max_cost_usd,
            retry_failed=args.retry_failed,
            resume=not args.no_resume,
            output_dir=args.output_dir,
            workers=args.workers,
            ignore_rate_limit=args.no_rate_limit,
            preview_limit=args.preview_limit,
            include_prompt_preview=args.include_prompt_preview,
        )
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if report.dry_run:
        print("DRY RUN: no API calls were made and no run files were written.")
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 1 if report.failed or report.stopped_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
