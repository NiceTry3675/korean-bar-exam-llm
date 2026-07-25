"""
@brief HTTP adapters for subscription OAuth benchmark providers.

The transformations in this file are intentionally minimal: the benchmark
prompt remains one unmodified user message and no coding-agent tools or
instructions are added.
Reference implementation notices are preserved in THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from oauth_auth import OAuthError


OPENAI_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages?beta=true"
OPENAI_CODEX_PROTOCOL_VERSION = "codex-responses-http-2026-07-24"
ANTHROPIC_PROTOCOL_VERSION = "claude-pro-max-oauth-2.1.87-neutral-v1"
OPENAI_TRANSFORM_VERSION = "openai-codex-neutral-v1"
ANTHROPIC_TRANSFORM_VERSION = "claude-agent-sdk-2.1.87-neutral-v2"
OPENAI_TRANSFORM_SPEC_SHA256 = hashlib.sha256(
    (
        "input=one unchanged user message\n"
        "instructions=omitted\n"
        "tools=[];tool_choice=auto;parallel_tool_calls=false\n"
        "store=false;stream=true;include=reasoning.encrypted_content\n"
        "reasoning=optional;verbosity=optional"
    ).encode("utf-8")
).hexdigest()
ANTHROPIC_REQUIRED_BETAS = (
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
)
CLAUDE_CODE_VERSION = "2.1.87"
CLAUDE_CODE_ENTRYPOINT = "sdk-cli"
CLAUDE_CODE_USER_AGENT = "claude-cli/2.1.87 (external, cli)"
CLAUDE_CODE_IDENTITY = (
    "You are a Claude agent, built on Anthropic's Claude Agent SDK."
)
_CCH_SALT = "59cf53e54c78"
_CCH_POSITIONS = (4, 7, 20)
ANTHROPIC_SYSTEM_SPEC_SHA256 = hashlib.sha256(
    (
        "billing:cch=sha256(prompt)[:5];suffix=sha256("
        "59cf53e54c78+prompt[4,7,20]+2.1.87)[:3]\n"
        + CLAUDE_CODE_IDENTITY
    ).encode("utf-8")
).hexdigest()
_QUOTA_CODES = {
    "usage_limit_reached",
    "usage_not_included",
    "insufficient_quota",
    "extra_usage_exhausted",
}


class OAuthCredentialProvider(Protocol):
    """@brief Credential operations needed by provider adapters."""

    @property
    def account_id(self) -> Optional[str]:
        ...

    def get_access_token(self) -> str:
        ...

    def force_refresh(self) -> str:
        ...


class OAuthModelConfig(Protocol):
    """@brief Secret-free model fields consumed by OAuth adapters."""

    model_id: str
    max_output_tokens: int
    request_timeout_seconds: float
    temperature: Optional[float]
    reasoning_effort: Optional[str]
    text_verbosity: Optional[str]
    effort: Optional[str]
    thinking: Optional[str]


@dataclass(frozen=True)
class OAuthProviderResponse:
    """@brief Structural equivalent of benchmark_runner.ProviderResponse."""

    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    stop_reason: Optional[str] = None
    refusal: bool = False
    raw: Any = None


class OAuthProviderRequestError(RuntimeError):
    """@brief Secret-free HTTP error with runner retry/auth metadata."""

    def __init__(
        self,
        provider: str,
        category: str,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
        auth_refresh_required: bool = False,
        authentication_fatal: bool = False,
    ):
        status = f" HTTP {status_code}" if status_code is not None else ""
        super().__init__(f"{provider} {category} error{status}.")
        self.provider = provider
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.auth_refresh_required = auth_refresh_required
        self.authentication_fatal = authentication_fatal


class OpenAICodexOAuthAdapter:
    """@brief Direct HTTP adapter for ChatGPT's Codex Responses backend."""

    provider_id = "openai-codex-oauth"

    def __init__(
        self,
        config: OAuthModelConfig,
        credentials: OAuthCredentialProvider,
        *,
        transport: Any = None,
    ):
        self._config = config
        self._credentials = credentials
        self._transport = transport or _requests_session()

    def refresh_auth(self) -> None:
        """@brief Rotate the credential once after a runner-counted 401."""
        try:
            self._credentials.force_refresh()
        except OAuthError:
            raise OAuthProviderRequestError(
                self.provider_id,
                "authentication",
                authentication_fatal=True,
            ) from None

    def send(self, prompt: str) -> OAuthProviderResponse:
        """@brief Send one unchanged user prompt and normalize the SSE completion."""
        try:
            access_token = self._credentials.get_access_token()
            account_id = self._credentials.account_id
        except OAuthError:
            raise OAuthProviderRequestError(
                self.provider_id,
                "authentication",
                authentication_fatal=True,
            ) from None
        if not access_token or not account_id:
            raise OAuthProviderRequestError(
                self.provider_id,
                "authentication",
                authentication_fatal=True,
            )
        body: dict[str, Any] = {
            "model": self._config.model_id,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        reasoning_effort = getattr(self._config, "reasoning_effort", None)
        if reasoning_effort:
            body["reasoning"] = {
                "effort": reasoning_effort,
                "summary": "auto",
            }
        text_verbosity = getattr(self._config, "text_verbosity", None)
        if text_verbosity:
            body["text"] = {"verbosity": text_verbosity}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-ID": account_id,
            "originator": "codex_cli_rs",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        response = _post(
            self._transport,
            self.provider_id,
            OPENAI_CODEX_RESPONSES_URL,
            headers=headers,
            body=body,
            timeout=self._config.request_timeout_seconds,
            stream=True,
        )
        _raise_for_error_response(self.provider_id, response)
        return _parse_openai_sse(response)


class AnthropicOAuthAdapter:
    """@brief Direct HTTP adapter for Claude Pro/Max OAuth inference."""

    provider_id = "anthropic-oauth"

    def __init__(
        self,
        config: OAuthModelConfig,
        credentials: OAuthCredentialProvider,
        *,
        transport: Any = None,
    ):
        self._config = config
        self._credentials = credentials
        self._transport = transport or _requests_session()

    def refresh_auth(self) -> None:
        """@brief Rotate the credential once after a runner-counted 401."""
        try:
            self._credentials.force_refresh()
        except OAuthError:
            raise OAuthProviderRequestError(
                self.provider_id,
                "authentication",
                authentication_fatal=True,
            ) from None

    def send(self, prompt: str) -> OAuthProviderResponse:
        """@brief Send one user message with only required Claude identity metadata."""
        try:
            access_token = self._credentials.get_access_token()
        except OAuthError:
            raise OAuthProviderRequestError(
                self.provider_id,
                "authentication",
                authentication_fatal=True,
            ) from None
        if not access_token:
            raise OAuthProviderRequestError(
                self.provider_id,
                "authentication",
                authentication_fatal=True,
            )
        body: dict[str, Any] = {
            "model": self._config.model_id,
            "max_tokens": self._config.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "system": [
                {"type": "text", "text": build_anthropic_billing_block(prompt)},
                {"type": "text", "text": CLAUDE_CODE_IDENTITY},
            ],
        }
        if self._config.temperature is not None:
            body["temperature"] = self._config.temperature
        effort = getattr(self._config, "effort", None)
        if effort:
            body["output_config"] = {"effort": effort}
        thinking = getattr(self._config, "thinking", None)
        if thinking == "disabled":
            body["thinking"] = {"type": "disabled"}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": ",".join(ANTHROPIC_REQUIRED_BETAS),
            "User-Agent": CLAUDE_CODE_USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        response = _post(
            self._transport,
            self.provider_id,
            ANTHROPIC_MESSAGES_URL,
            headers=headers,
            body=body,
            timeout=self._config.request_timeout_seconds,
            stream=False,
        )
        _raise_for_error_response(self.provider_id, response)
        payload = _response_json(response, self.provider_id)
        content = payload.get("content")
        blocks = content if isinstance(content, list) else []
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
        refusal = any(
            isinstance(block, Mapping)
            and block.get("type") in {"refusal", "content_filter"}
            for block in blocks
        )
        stop_reason = _optional_string(payload.get("stop_reason"))
        usage = payload.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        return OAuthProviderResponse(
            text=text,
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
            stop_reason=stop_reason,
            refusal=refusal or stop_reason in {"refusal", "content_filter"},
            raw=payload,
        )


def compute_cch(prompt: str) -> str:
    """@brief Return the first five hex characters of the prompt SHA-256."""
    return hashlib.sha256(_javascript_utf8(prompt)).hexdigest()[:5]


def compute_claude_version_suffix(
    prompt: str, version: str = CLAUDE_CODE_VERSION
) -> str:
    """@brief Reproduce JavaScript UTF-16 charAt indexing used by Claude CLI."""
    prompt_utf16 = prompt.encode("utf-16-le", errors="surrogatepass")
    sampled_utf16 = bytearray()
    for index in _CCH_POSITIONS:
        offset = index * 2
        if offset + 2 <= len(prompt_utf16):
            sampled_utf16.extend(prompt_utf16[offset : offset + 2])
        else:
            sampled_utf16.extend("0".encode("utf-16-le"))
    sampled = bytes(sampled_utf16).decode("utf-16-le", errors="replace")
    return hashlib.sha256(
        f"{_CCH_SALT}{sampled}{version}".encode("utf-8")
    ).hexdigest()[:3]


def build_anthropic_billing_block(prompt: str) -> str:
    """@brief Build the exact first Claude system block required by OAuth."""
    suffix = compute_claude_version_suffix(prompt)
    return (
        "x-anthropic-billing-header: "
        f"cc_version={CLAUDE_CODE_VERSION}.{suffix}; "
        f"cc_entrypoint={CLAUDE_CODE_ENTRYPOINT}; "
        f"cch={compute_cch(prompt)};"
    )


def _post(
    transport: Any,
    provider: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
    stream: bool,
) -> Any:
    """@brief Send one inference request while reducing transport errors to safe types."""
    try:
        return transport.post(
            url,
            headers=dict(headers),
            json=dict(body),
            timeout=timeout,
            stream=stream,
            allow_redirects=False,
        )
    except Exception as exc:
        type_name = type(exc).__name__.lower()
        is_timeout = isinstance(exc, TimeoutError) or "timeout" in type_name
        raise OAuthProviderRequestError(
            provider,
            "timeout" if is_timeout else "transport",
            retryable=is_timeout,
        ) from None


def _raise_for_error_response(provider: str, response: Any) -> None:
    status = int(getattr(response, "status_code", 0))
    if 200 <= status < 300:
        return
    error_code, message = _safe_error_metadata(response)
    normalized_message = message.lower()
    quota = (
        error_code in _QUOTA_CODES
        or "out of extra usage" in normalized_message
        or "usage limit" in normalized_message
        or "usage is not included" in normalized_message
    )
    if status == 401:
        raise OAuthProviderRequestError(
            provider,
            "authentication",
            status_code=status,
            auth_refresh_required=True,
        )
    if quota:
        raise OAuthProviderRequestError(
            provider,
            "subscription_quota",
            status_code=status,
            retryable=False,
        )
    if status == 429:
        raise OAuthProviderRequestError(
            provider,
            "rate_limit",
            status_code=status,
            retryable=True,
        )
    if 500 <= status <= 599:
        raise OAuthProviderRequestError(
            provider,
            "server",
            status_code=status,
            retryable=True,
        )
    raise OAuthProviderRequestError(
        provider,
        "request",
        status_code=status,
        retryable=False,
    )


def _safe_error_metadata(response: Any) -> tuple[str, str]:
    """@brief Inspect only classification fields and never include them in exceptions."""
    try:
        payload = response.json()
    except Exception:
        return "", ""
    if not isinstance(payload, Mapping):
        return "", ""
    error = payload.get("error")
    if isinstance(error, Mapping):
        code = error.get("code") or error.get("type")
        message = error.get("message")
    else:
        code = payload.get("code") or payload.get("type")
        message = payload.get("message")
    return str(code or "").lower(), str(message or "")


def _parse_openai_sse(response: Any) -> OAuthProviderResponse:
    final_response: Optional[Mapping[str, Any]] = None
    text_deltas: list[str] = []
    refusal_seen = False
    data_lines: list[str] = []

    def consume() -> None:
        nonlocal final_response, refusal_seen
        if not data_lines:
            return
        raw_data = "\n".join(data_lines)
        data_lines.clear()
        if raw_data == "[DONE]":
            return
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            return
        if not isinstance(event, Mapping):
            return
        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta":
            text_deltas.append(str(event.get("delta") or ""))
        elif event_type == "response.refusal.delta":
            refusal_seen = True
        elif event_type == "response.failed":
            candidate = event.get("response")
            candidate = candidate if isinstance(candidate, Mapping) else {}
            error = candidate.get("error")
            error = error if isinstance(error, Mapping) else event.get("error")
            error = error if isinstance(error, Mapping) else {}
            code = str(error.get("code") or error.get("type") or "").lower()
            if code in _QUOTA_CODES:
                raise OAuthProviderRequestError(
                    "openai-codex-oauth",
                    "subscription_quota",
                    retryable=False,
                )
            if code in {
                "server_is_overloaded",
                "slow_down",
                "rate_limit_exceeded",
            }:
                raise OAuthProviderRequestError(
                    "openai-codex-oauth",
                    "server" if code == "server_is_overloaded" else "rate_limit",
                    retryable=True,
                )
            raise OAuthProviderRequestError(
                "openai-codex-oauth",
                "request",
                retryable=False,
            )
        elif event_type == "response.incomplete":
            raise OAuthProviderRequestError(
                "openai-codex-oauth",
                "incomplete_response",
                retryable=False,
            )
        if event_type in {"response.completed", "response.done"}:
            candidate = event.get("response")
            if isinstance(candidate, Mapping):
                final_response = candidate

    try:
        lines = response.iter_lines(decode_unicode=True)
        for raw_line in lines:
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else str(raw_line)
            )
            if line == "":
                consume()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        consume()
    except OAuthProviderRequestError:
        raise
    except Exception:
        raise OAuthProviderRequestError(
            "openai-codex-oauth",
            "invalid_response",
            retryable=False,
        ) from None
    if final_response is None:
        raise OAuthProviderRequestError(
            "openai-codex-oauth",
            "invalid_response",
            retryable=False,
        )
    text, final_refusal = _extract_openai_text(final_response)
    if not text:
        text = "".join(text_deltas)
    usage = final_response.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    incomplete = final_response.get("incomplete_details")
    stop_reason = (
        _optional_string(incomplete.get("reason"))
        if isinstance(incomplete, Mapping)
        else None
    ) or _optional_string(final_response.get("status"))
    return OAuthProviderResponse(
        text=text,
        input_tokens=_optional_int(usage.get("input_tokens")),
        output_tokens=_optional_int(usage.get("output_tokens")),
        stop_reason=stop_reason,
        refusal=refusal_seen or final_refusal,
        raw=final_response,
    )


def _extract_openai_text(response: Mapping[str, Any]) -> tuple[str, bool]:
    parts: list[str] = []
    refusal = False
    output = response.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "output_text":
            parts.append(str(item.get("text") or ""))
        content = item.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type in {"output_text", "text"}:
                parts.append(str(block.get("text") or ""))
            elif block_type in {"refusal", "content_filter"}:
                refusal = True
    direct = response.get("output_text")
    if not parts and isinstance(direct, str):
        parts.append(direct)
    return "".join(parts), refusal


def _response_json(response: Any, provider: str) -> Mapping[str, Any]:
    try:
        value = response.json()
    except Exception:
        raise OAuthProviderRequestError(
            provider,
            "invalid_response",
            retryable=False,
        ) from None
    if not isinstance(value, Mapping):
        raise OAuthProviderRequestError(
            provider,
            "invalid_response",
            retryable=False,
        )
    return value


def _requests_session() -> Any:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("The requests package is required for OAuth providers.") from exc
    return requests.Session()


def _javascript_utf8(value: str) -> bytes:
    """@brief Match JavaScript's UTF-8 replacement behavior for lone surrogates."""
    normalized = value.encode(
        "utf-16-le", errors="surrogatepass"
    ).decode("utf-16-le", errors="replace")
    return normalized.encode("utf-8")


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> Optional[str]:
    return str(value) if value is not None else None
