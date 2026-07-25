"""@brief Exact, offline wire-contract tests for OAuth provider adapters."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from oauth_providers import (
    ANTHROPIC_MESSAGES_URL,
    CLAUDE_CODE_IDENTITY,
    OPENAI_CODEX_RESPONSES_URL,
    AnthropicOAuthAdapter,
    OAuthProviderRequestError,
    OpenAICodexOAuthAdapter,
    build_anthropic_billing_block,
    compute_cch,
    compute_claude_version_suffix,
)


class FakeCredentials:
    """@brief Secret fixture with observable refresh behavior."""

    def __init__(self):
        self.token = "access-token-sentinel"
        self._account_id = "account-id-sentinel"
        self.refreshes = 0

    @property
    def account_id(self):
        return self._account_id

    def get_access_token(self):
        return self.token

    def force_refresh(self):
        self.refreshes += 1
        self.token = "rotated-token-sentinel"
        return self.token


class FakeResponse:
    """@brief Minimal requests response supporting JSON or SSE."""

    def __init__(self, status_code=200, payload=None, lines=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._lines = lines or []

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        return iter(self._lines)


class FakeTransport:
    """@brief Capture exactly one provider request at a time."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _config(**overrides):
    """@brief Build a structural OAuth model configuration."""
    values = {
        "model_id": "model-id-as-configured",
        "max_output_tokens": 321,
        "request_timeout_seconds": 12.5,
        "temperature": None,
        "reasoning_effort": None,
        "text_verbosity": None,
        "effort": None,
        "thinking": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sse_event(event):
    import json

    return [f"data: {json.dumps(event)}", ""]


class OpenAIAdapterTests(unittest.TestCase):
    """@brief Verify the neutral Codex request and SSE normalization."""

    def test_exact_headers_body_and_completed_sse(self):
        final = {
            "id": "response-id",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "정답: 3"}],
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 8},
        }
        lines = [
            *_sse_event({"type": "response.output_text.delta", "delta": "정답: "}),
            *_sse_event({"type": "response.completed", "response": final}),
            "data: [DONE]",
            "",
        ]
        transport = FakeTransport([FakeResponse(lines=lines)])
        adapter = OpenAICodexOAuthAdapter(
            _config(reasoning_effort="high", text_verbosity="medium"),
            FakeCredentials(),
            transport=transport,
        )
        response = adapter.send("원본 문제")
        self.assertEqual("정답: 3", response.text)
        self.assertEqual(20, response.input_tokens)
        self.assertEqual(8, response.output_tokens)
        self.assertEqual("completed", response.stop_reason)

        url, kwargs = transport.calls[0]
        self.assertEqual(OPENAI_CODEX_RESPONSES_URL, url)
        self.assertFalse(kwargs["allow_redirects"])
        headers = kwargs["headers"]
        self.assertEqual("Bearer access-token-sentinel", headers["Authorization"])
        self.assertEqual("account-id-sentinel", headers["ChatGPT-Account-ID"])
        self.assertEqual("codex_cli_rs", headers["originator"])
        self.assertNotIn("x-api-key", {key.lower() for key in headers})
        self.assertNotIn("openai-beta", {key.lower() for key in headers})
        body = kwargs["json"]
        self.assertEqual("model-id-as-configured", body["model"])
        self.assertEqual("원본 문제", body["input"][0]["content"][0]["text"])
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertFalse(body["parallel_tool_calls"])
        self.assertEqual("auto", body["tool_choice"])
        self.assertEqual(
            {"effort": "high", "summary": "auto"},
            body["reasoning"],
        )
        self.assertEqual({"verbosity": "medium"}, body["text"])
        self.assertNotIn("instructions", body)
        self.assertEqual([], body["tools"])
        self.assertNotIn("max_output_tokens", body)
        self.assertNotIn("temperature", body)

    def test_missing_final_event_is_nonretryable_invalid_response(self):
        transport = FakeTransport(
            [
                FakeResponse(
                    lines=_sse_event(
                        {"type": "response.output_text.delta", "delta": "partial"}
                    )
                )
            ]
        )
        adapter = OpenAICodexOAuthAdapter(
            _config(), FakeCredentials(), transport=transport
        )
        with self.assertRaises(OAuthProviderRequestError) as raised:
            adapter.send("prompt")
        self.assertEqual("invalid_response", raised.exception.category)
        self.assertFalse(raised.exception.retryable)
        _, kwargs = transport.calls[0]
        self.assertNotIn("reasoning", kwargs["json"])
        self.assertNotIn("text", kwargs["json"])

    def test_refusal_block_is_normalized(self):
        final = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "cannot"}],
                }
            ],
            "usage": {},
        }
        adapter = OpenAICodexOAuthAdapter(
            _config(),
            FakeCredentials(),
            transport=FakeTransport(
                [FakeResponse(lines=_sse_event({"type": "response.done", "response": final}))]
            ),
        )
        self.assertTrue(adapter.send("prompt").refusal)

    def test_failed_and_incomplete_sse_events_are_classified(self):
        cases = [
            (
                {
                    "type": "response.failed",
                    "response": {
                        "error": {"code": "insufficient_quota", "message": "redacted"}
                    },
                },
                "subscription_quota",
                False,
            ),
            (
                {
                    "type": "response.failed",
                    "response": {
                        "error": {"code": "server_is_overloaded", "message": "redacted"}
                    },
                },
                "server",
                True,
            ),
            (
                {
                    "type": "response.incomplete",
                    "response": {"incomplete_details": {"reason": "max_output_tokens"}},
                },
                "incomplete_response",
                False,
            ),
        ]
        for event, category, retryable in cases:
            with self.subTest(category=category):
                adapter = OpenAICodexOAuthAdapter(
                    _config(),
                    FakeCredentials(),
                    transport=FakeTransport([FakeResponse(lines=_sse_event(event))]),
                )
                with self.assertRaises(OAuthProviderRequestError) as raised:
                    adapter.send("prompt")
                self.assertEqual(category, raised.exception.category)
                self.assertEqual(retryable, raised.exception.retryable)


class AnthropicAdapterTests(unittest.TestCase):
    """@brief Verify billing identity and Messages response normalization."""

    def test_billing_known_vector(self):
        prompt = "hello world test message"
        self.assertEqual("4ffc3", compute_cch(prompt))
        self.assertEqual("6ff", compute_claude_version_suffix(prompt))
        self.assertEqual(
            "x-anthropic-billing-header: "
            "cc_version=2.1.87.6ff; cc_entrypoint=sdk-cli; cch=4ffc3;",
            build_anthropic_billing_block(prompt),
        )

    def test_billing_matches_javascript_utf16_for_non_bmp_prompt(self):
        prompt = "😀hello world test message"
        self.assertEqual("46c54", compute_cch(prompt))
        self.assertEqual("b10", compute_claude_version_suffix(prompt))

    def test_exact_headers_body_and_response(self):
        payload = {
            "content": [{"type": "text", "text": "정답: 2"}],
            "usage": {"input_tokens": 30, "output_tokens": 4},
            "stop_reason": "end_turn",
        }
        transport = FakeTransport([FakeResponse(payload=payload)])
        adapter = AnthropicOAuthAdapter(
            _config(temperature=0.0, effort="max"),
            FakeCredentials(),
            transport=transport,
        )
        response = adapter.send("hello world test message")
        self.assertEqual("정답: 2", response.text)
        self.assertEqual(30, response.input_tokens)
        self.assertEqual(4, response.output_tokens)
        self.assertEqual("end_turn", response.stop_reason)

        url, kwargs = transport.calls[0]
        self.assertEqual(ANTHROPIC_MESSAGES_URL, url)
        self.assertFalse(kwargs["allow_redirects"])
        headers = kwargs["headers"]
        self.assertEqual("Bearer access-token-sentinel", headers["Authorization"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual(
            "oauth-2025-04-20,interleaved-thinking-2025-05-14",
            headers["anthropic-beta"],
        )
        self.assertEqual("claude-cli/2.1.87 (external, cli)", headers["User-Agent"])
        self.assertNotIn("x-api-key", {key.lower() for key in headers})
        body = kwargs["json"]
        self.assertEqual("model-id-as-configured", body["model"])
        self.assertEqual(321, body["max_tokens"])
        self.assertEqual(
            [{"role": "user", "content": "hello world test message"}],
            body["messages"],
        )
        self.assertIn("x-anthropic-billing-header", body["system"][0]["text"])
        self.assertEqual(CLAUDE_CODE_IDENTITY, body["system"][1]["text"])
        self.assertEqual(0.0, body["temperature"])
        self.assertEqual({"effort": "max"}, body["output_config"])
        self.assertNotIn("tools", body)

    def test_disabled_thinking_is_sent_and_effort_stays_absent(self):
        payload = {
            "content": [{"type": "text", "text": "정답: 4"}],
            "usage": {"input_tokens": 20, "output_tokens": 3},
            "stop_reason": "end_turn",
        }
        transport = FakeTransport([FakeResponse(payload=payload)])
        adapter = AnthropicOAuthAdapter(
            _config(thinking="disabled"),
            FakeCredentials(),
            transport=transport,
        )
        adapter.send("hello world test message")
        body = transport.calls[0][1]["json"]
        self.assertEqual({"type": "disabled"}, body["thinking"])
        self.assertNotIn("output_config", body)

    def test_refusal_block_and_stop_reason_are_normalized(self):
        adapter = AnthropicOAuthAdapter(
            _config(),
            FakeCredentials(),
            transport=FakeTransport(
                [
                    FakeResponse(
                        payload={
                            "content": [
                                {"type": "refusal", "refusal": "cannot comply"}
                            ],
                            "usage": {"input_tokens": 3, "output_tokens": 1},
                            "stop_reason": "refusal",
                        }
                    )
                ]
            ),
        )
        response = adapter.send("prompt")
        self.assertTrue(response.refusal)
        self.assertEqual("", response.text)
        self.assertEqual(3, response.input_tokens)
        self.assertEqual(1, response.output_tokens)
        self.assertEqual("refusal", response.stop_reason)


class ProviderErrorTests(unittest.TestCase):
    """@brief Lock retry categories and ensure error strings redact payloads."""

    def test_401_requests_runner_managed_refresh_without_refreshing_inside_send(self):
        credentials = FakeCredentials()
        adapter = OpenAICodexOAuthAdapter(
            _config(),
            credentials,
            transport=FakeTransport(
                [
                    FakeResponse(
                        status_code=401,
                        payload={
                            "error": {
                                "message": "secret response access-token-sentinel"
                            }
                        },
                    )
                ]
            ),
        )
        with self.assertRaises(OAuthProviderRequestError) as raised:
            adapter.send("prompt")
        self.assertTrue(raised.exception.auth_refresh_required)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(0, credentials.refreshes)
        self.assertNotIn("access-token-sentinel", str(raised.exception))
        adapter.refresh_auth()
        self.assertEqual(1, credentials.refreshes)

    def test_anthropic_401_uses_the_same_runner_managed_refresh_contract(self):
        credentials = FakeCredentials()
        adapter = AnthropicOAuthAdapter(
            _config(),
            credentials,
            transport=FakeTransport([FakeResponse(status_code=401)]),
        )
        with self.assertRaises(OAuthProviderRequestError) as raised:
            adapter.send("prompt")
        self.assertTrue(raised.exception.auth_refresh_required)
        self.assertEqual(0, credentials.refreshes)
        adapter.refresh_auth()
        self.assertEqual(1, credentials.refreshes)

    def test_quota_is_not_retryable_but_rate_limit_and_server_are(self):
        cases = [
            (
                FakeResponse(
                    status_code=404,
                    payload={"error": {"code": "usage_limit_reached"}},
                ),
                "subscription_quota",
                False,
            ),
            (
                FakeResponse(
                    status_code=400,
                    payload={"error": {"message": "You're out of extra usage"}},
                ),
                "subscription_quota",
                False,
            ),
            (FakeResponse(status_code=429), "rate_limit", True),
            (FakeResponse(status_code=503), "server", True),
        ]
        for response, category, retryable in cases:
            with self.subTest(category=category, status=response.status_code):
                adapter = AnthropicOAuthAdapter(
                    _config(),
                    FakeCredentials(),
                    transport=FakeTransport([response]),
                )
                with self.assertRaises(OAuthProviderRequestError) as raised:
                    adapter.send("prompt")
                self.assertEqual(category, raised.exception.category)
                self.assertEqual(retryable, raised.exception.retryable)

    def test_timeout_is_retryable_and_transport_error_is_not(self):
        for error, expected in (
            (TimeoutError("token sentinel"), True),
            (ConnectionError("token sentinel"), False),
        ):
            adapter = AnthropicOAuthAdapter(
                _config(),
                FakeCredentials(),
                transport=FakeTransport([error]),
            )
            with self.assertRaises(OAuthProviderRequestError) as raised:
                adapter.send("prompt")
            self.assertEqual(expected, raised.exception.retryable)
            self.assertNotIn("token sentinel", str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
