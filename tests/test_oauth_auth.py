"""@brief Offline tests for OAuth login, Keychain storage, and refresh."""

from __future__ import annotations

import base64
import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import benchmark_auth
import oauth_auth
from oauth_auth import (
    ANTHROPIC_CALLBACK_URL,
    ANTHROPIC_OAUTH_PROVIDER,
    KEYRING_SERVICE,
    OPENAI_CODEX_PROVIDER,
    OPENAI_LOOPBACK_PORTS,
    RISK_NOTICE_VERSION,
    KeyringCredentialStore,
    LoopbackCallbackServer,
    OAuthAuthenticationError,
    OAuthConfigurationError,
    OAuthCredential,
    OAuthCredentialManager,
    confirm_account_risk,
    create_authorization_flow,
    credential_status,
    exchange_authorization_code,
    generate_pkce,
    login,
    logout,
    parse_callback_input,
    risk_notice_sha256,
)


class FakeSecureBackend:
    """@brief Test double shaped like the native macOS Keychain backend."""

    priority = 1


FakeSecureBackend.__module__ = "keyring.backends.macOS"


class FakeKeyring:
    """@brief In-memory keyring module with a usable backend."""

    def __init__(self):
        self.values = {}
        self.calls = []
        self.backend = FakeSecureBackend()

    def get_keyring(self):
        return self.backend

    def get_password(self, service, account):
        self.calls.append(("get", service, account))
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.calls.append(("set", service, account))
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.calls.append(("delete", service, account))
        del self.values[(service, account)]


class FakeResponse:
    """@brief Minimal requests-compatible response."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeSession:
    """@brief Ordered token endpoint transport."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _jwt(payload):
    """@brief Create a signature-free JWT fixture; production never verifies signatures."""

    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode(payload)}."


def _credential(provider, *, now=1_000.0, **overrides):
    """@brief Build a current-consent credential fixture."""
    values = {
        "provider": provider,
        "profile": "default",
        "access_token": "access-sentinel",
        "refresh_token": "refresh-sentinel",
        "account_id": "account-sentinel" if provider == OPENAI_CODEX_PROVIDER else None,
        "expires_at": now + 3_600,
        "last_refresh_at": now,
        "risk_notice_version": RISK_NOTICE_VERSION,
        "risk_notice_sha256": risk_notice_sha256(provider),
        "risk_accepted_at": "2026-07-24T00:00:00Z",
    }
    values.update(overrides)
    return OAuthCredential(**values)


class OAuthFlowTests(unittest.TestCase):
    """@brief Validate PKCE, state, endpoint, and callback contracts."""

    def test_pkce_is_url_safe_and_s256(self):
        verifier, challenge = generate_pkce()
        self.assertGreaterEqual(len(verifier), 80)
        self.assertNotIn("=", verifier)
        expected = base64.urlsafe_b64encode(
            __import__("hashlib").sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(expected, challenge)

    def test_authorization_urls_use_exact_provider_contracts(self):
        openai = create_authorization_flow(
            OPENAI_CODEX_PROVIDER,
            redirect_uri="http://localhost:1457/auth/callback",
        )
        params = parse_qs(urlparse(openai.url).query)
        self.assertEqual("https://auth.openai.com/oauth/authorize", openai.url.split("?")[0])
        self.assertEqual(
            "openid profile email offline_access api.connectors.read api.connectors.invoke",
            params["scope"][0],
        )
        self.assertEqual("http://localhost:1457/auth/callback", params["redirect_uri"][0])
        self.assertEqual("true", params["codex_cli_simplified_flow"][0])

        anthropic = create_authorization_flow(ANTHROPIC_OAUTH_PROVIDER)
        anthropic_params = parse_qs(urlparse(anthropic.url).query)
        self.assertEqual("https://claude.ai/oauth/authorize", anthropic.url.split("?")[0])
        self.assertEqual(ANTHROPIC_CALLBACK_URL, anthropic.redirect_uri)
        self.assertEqual(
            "org:create_api_key user:profile user:inference "
            "user:sessions:claude_code user:mcp_servers user:file_upload",
            anthropic_params["scope"][0],
        )

        with self.assertRaisesRegex(OAuthConfigurationError, "allowlisted localhost"):
            create_authorization_flow(
                OPENAI_CODEX_PROVIDER,
                redirect_uri="https://attacker.example/auth/callback",
            )

    def test_manual_callback_requires_code_and_exact_state(self):
        self.assertEqual(
            ("code-value", "expected"),
            parse_callback_input(
                "https://platform.claude.com/oauth/code/callback"
                "?code=code-value&state=expected",
                expected_state="expected",
                expected_redirect_uri=ANTHROPIC_CALLBACK_URL,
            ),
        )
        self.assertEqual(
            ("code-value", "expected"),
            parse_callback_input(
                "http://localhost:1457/auth/callback"
                "?code=code-value&state=expected",
                expected_state="expected",
                expected_redirect_uri="http://localhost:1457/auth/callback",
            ),
        )
        self.assertEqual(
            ("code-value", "expected"),
            parse_callback_input("code-value#expected", expected_state="expected"),
        )
        self.assertEqual(
            ("code-value", "expected"),
            parse_callback_input(
                "https://platform.claude.com/oauth/code/callback"
                "?code=code-value#expected",
                expected_state="expected",
                expected_redirect_uri=ANTHROPIC_CALLBACK_URL,
            ),
        )
        with self.assertRaisesRegex(OAuthAuthenticationError, "redirect URI"):
            parse_callback_input(
                "https://attacker.example/callback?code=code-value&state=expected",
                expected_state="expected",
                expected_redirect_uri=ANTHROPIC_CALLBACK_URL,
            )
        with self.assertRaisesRegex(OAuthAuthenticationError, "code-only"):
            parse_callback_input("code-value", expected_state="expected")
        with self.assertRaisesRegex(OAuthAuthenticationError, "did not match"):
            parse_callback_input(
                "code=code-value&state=attacker",
                expected_state="expected",
            )

    def test_loopback_tries_1455_then_1457(self):
        fake_server = SimpleNamespace(server_close=lambda: None)
        calls = []

        def factory(address, _handler):
            calls.append(address)
            if address[1] == 1455:
                raise OSError("occupied")
            return fake_server

        with patch.object(oauth_auth, "HTTPServer", side_effect=factory):
            server = LoopbackCallbackServer()
            self.assertEqual(1457, server.port)
            self.assertEqual("http://localhost:1457/auth/callback", server.redirect_uri)
            server.close()
        self.assertEqual(
            [("127.0.0.1", OPENAI_LOOPBACK_PORTS[0]), ("127.0.0.1", 1457)],
            calls,
        )

    def test_loopback_validation_ignores_wrong_state_and_host(self):
        self.assertEqual(
            (400, None),
            oauth_auth._validate_loopback_callback(
                "/auth/callback?code=attacker&state=wrong-state",
                "127.0.0.1:1455",
                1455,
                "expected-state",
            ),
        )
        self.assertEqual(
            (400, None),
            oauth_auth._validate_loopback_callback(
                "/auth/callback?code=valid-code&state=expected-state",
                "attacker.example:1455",
                1455,
                "expected-state",
            ),
        )
        self.assertEqual(
            (200, "code=valid-code&state=expected-state"),
            oauth_auth._validate_loopback_callback(
                "/auth/callback?code=valid-code&state=expected-state",
                "localhost:1455",
                1455,
                "expected-state",
            ),
        )

    def test_provider_token_exchange_shapes_and_account_claim(self):
        openai_flow = create_authorization_flow(
            OPENAI_CODEX_PROVIDER,
            redirect_uri="http://localhost:1455/auth/callback",
        )
        access = _jwt(
            {
                "exp": 9_000,
            }
        )
        id_token = _jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-fixture"
                }
            }
        )
        openai_session = FakeSession(
            [
                FakeResponse(
                    payload={
                        "access_token": access,
                        "refresh_token": "refresh-value",
                        "id_token": id_token,
                    }
                )
            ]
        )
        openai_credential = exchange_authorization_code(
            openai_flow,
            f"code=auth-code&state={openai_flow.state}",
            session=openai_session,
            now_fn=lambda: 1_000,
        )
        _, openai_kwargs = openai_session.calls[0]
        self.assertEqual("authorization_code", openai_kwargs["data"]["grant_type"])
        self.assertIsNone(openai_kwargs["json"])
        self.assertFalse(openai_kwargs["allow_redirects"])
        self.assertEqual("account-fixture", openai_credential.account_id)
        self.assertEqual(9_000, openai_credential.expires_at)

        anthropic_flow = create_authorization_flow(ANTHROPIC_OAUTH_PROVIDER)
        anthropic_session = FakeSession(
            [
                FakeResponse(
                    payload={
                        "access_token": "anthropic-access",
                        "refresh_token": "anthropic-refresh",
                        "expires_in": 600,
                    }
                )
            ]
        )
        exchange_authorization_code(
            anthropic_flow,
            f"auth-code#{anthropic_flow.state}",
            session=anthropic_session,
            now_fn=lambda: 1_000,
        )
        _, anthropic_kwargs = anthropic_session.calls[0]
        self.assertEqual(anthropic_flow.state, anthropic_kwargs["json"]["state"])
        self.assertIsNone(anthropic_kwargs["data"])
        self.assertFalse(anthropic_kwargs["allow_redirects"])

    def test_openai_without_jwt_exp_uses_eight_day_refresh_fallback(self):
        flow = create_authorization_flow(
            OPENAI_CODEX_PROVIDER,
            redirect_uri="http://localhost:1455/auth/callback",
        )
        id_token = _jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-fixture"}}
        )
        credential = exchange_authorization_code(
            flow,
            f"code=auth-code&state={flow.state}",
            session=FakeSession(
                [
                    FakeResponse(
                        payload={
                            "access_token": "opaque-access-token",
                            "refresh_token": "refresh-value",
                            "id_token": id_token,
                            "expires_in": 600,
                        }
                    )
                ]
            ),
            now_fn=lambda: 1_000,
        )
        self.assertIsNone(credential.expires_at)
        credential = OAuthCredential(
            provider=credential.provider,
            profile="default",
            access_token=credential.access_token,
            refresh_token=credential.refresh_token,
            id_token=credential.id_token,
            account_id=credential.account_id,
            expires_at=credential.expires_at,
            last_refresh_at=1_000,
            risk_notice_version=RISK_NOTICE_VERSION,
            risk_notice_sha256=risk_notice_sha256(OPENAI_CODEX_PROVIDER),
            risk_accepted_at="2026-07-24T00:00:00Z",
        )
        store = KeyringCredentialStore(FakeKeyring())
        store.save(credential)
        manager = OAuthCredentialManager(
            OPENAI_CODEX_PROVIDER,
            store=store,
            session=FakeSession([]),
            now_fn=lambda: 1_000 + (8 * 24 * 60 * 60) - 301,
        )
        self.assertEqual("opaque-access-token", manager.get_access_token())
        refreshed_access = _jwt(
            {
                "exp": 2_000_000,
                "chatgpt_account_id": "account-fixture",
            }
        )
        session = FakeSession(
            [
                FakeResponse(
                    payload={
                        "access_token": refreshed_access,
                        "refresh_token": "rotated-refresh",
                    }
                )
            ]
        )
        manager = OAuthCredentialManager(
            OPENAI_CODEX_PROVIDER,
            store=store,
            session=session,
            now_fn=lambda: 1_000 + (8 * 24 * 60 * 60) - 300,
        )
        self.assertEqual(refreshed_access, manager.get_access_token())
        self.assertEqual(1, len(session.calls))

    def test_openai_rejects_missing_or_header_unsafe_account_claim(self):
        flow = create_authorization_flow(
            OPENAI_CODEX_PROVIDER,
            redirect_uri="http://localhost:1455/auth/callback",
        )
        for claim in (None, "account\r\ninjected"):
            payload = {"exp": 9_000}
            if claim is not None:
                payload["https://api.openai.com/auth"] = {
                    "chatgpt_account_id": claim
                }
            with self.subTest(claim=claim):
                with self.assertRaisesRegex(
                    OAuthAuthenticationError, "account ID"
                ):
                    exchange_authorization_code(
                        flow,
                        f"code=auth-code&state={flow.state}",
                        session=FakeSession(
                            [
                                FakeResponse(
                                    payload={
                                        "access_token": _jwt({"exp": 9_000}),
                                        "refresh_token": "refresh-value",
                                        "id_token": _jwt(payload),
                                    }
                                )
                            ]
                        ),
                        now_fn=lambda: 1_000,
                    )
        with self.assertRaisesRegex(OAuthAuthenticationError, "invalid ID token"):
            exchange_authorization_code(
                flow,
                f"code=auth-code&state={flow.state}",
                session=FakeSession(
                    [
                        FakeResponse(
                            payload={
                                "access_token": _jwt(
                                    {
                                        "exp": 9_000,
                                        "chatgpt_account_id": "account-fixture",
                                    }
                                ),
                                "refresh_token": "refresh-value",
                                "id_token": "not-a-jwt",
                            }
                        )
                    ]
                ),
                now_fn=lambda: 1_000,
            )


class KeyringAndRefreshTests(unittest.TestCase):
    """@brief Verify one-record persistence, consent, refresh, and redaction."""

    def setUp(self):
        self.keyring = FakeKeyring()
        self.store = KeyringCredentialStore(self.keyring)

    def test_keyring_uses_exact_service_and_profile_account(self):
        credential = _credential(ANTHROPIC_OAUTH_PROVIDER)
        self.store.save(credential)
        account = f"{ANTHROPIC_OAUTH_PROVIDER}:default"
        self.assertIn((KEYRING_SERVICE, account), self.keyring.values)
        loaded = self.store.load(ANTHROPIC_OAUTH_PROVIDER, "default")
        self.assertEqual(credential, loaded)
        self.assertNotIn("access-sentinel", repr(loaded))
        self.assertNotIn("refresh-sentinel", repr(loaded))

    def test_corrupt_keychain_record_cannot_leak_through_exception_cause(self):
        sentinel = "ACCESS_TOKEN_SENTINEL"
        account = f"{ANTHROPIC_OAUTH_PROVIDER}:default"
        self.keyring.values[(KEYRING_SERVICE, account)] = (
            '{"access_token":"' + sentinel + '"'
        )
        with self.assertRaises(OAuthConfigurationError) as raised:
            self.store.load(ANTHROPIC_OAUTH_PROVIDER, "default")
        self.assertNotIn(sentinel, str(raised.exception))
        self.assertNotIn(sentinel, repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_fail_only_backend_has_no_plaintext_fallback(self):
        keyring = FakeKeyring()
        keyring.backend = SimpleNamespace(priority=0)
        with self.assertRaisesRegex(OAuthConfigurationError, "plaintext fallback"):
            KeyringCredentialStore(keyring)

    def test_positive_priority_plaintext_backend_is_rejected(self):
        class PlaintextBackend:
            priority = 1

        PlaintextBackend.__module__ = "keyrings.alt.file"
        keyring = FakeKeyring()
        keyring.backend = PlaintextBackend()
        with self.assertRaisesRegex(OAuthConfigurationError, "plaintext fallback"):
            KeyringCredentialStore(keyring)

    def test_stale_consent_blocks_token_before_network(self):
        self.store.save(
            _credential(
                ANTHROPIC_OAUTH_PROVIDER,
                risk_notice_version="old-notice",
            )
        )
        session = FakeSession([])
        manager = OAuthCredentialManager(
            ANTHROPIC_OAUTH_PROVIDER,
            store=self.store,
            session=session,
            now_fn=lambda: 1_000,
        )
        with self.assertRaisesRegex(OAuthAuthenticationError, "missing or stale"):
            manager.get_access_token()
        self.assertEqual([], session.calls)

    def test_openai_refresh_rotates_tokens_and_account_id(self):
        expired = _credential(
            OPENAI_CODEX_PROVIDER,
            access_token=_jwt({"exp": 1_050, "chatgpt_account_id": "old-account"}),
            expires_at=1_050,
        )
        self.store.save(expired)
        refreshed_access = _jwt({"exp": 8_000})
        refreshed_id = _jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "new-account"
                }
            }
        )
        session = FakeSession(
            [
                FakeResponse(
                    payload={
                        "access_token": refreshed_access,
                        "refresh_token": "rotated-refresh",
                        "id_token": refreshed_id,
                    }
                )
            ]
        )
        manager = OAuthCredentialManager(
            OPENAI_CODEX_PROVIDER,
            store=self.store,
            session=session,
            now_fn=lambda: 1_000,
        )
        self.assertEqual(refreshed_access, manager.get_access_token())
        saved = self.store.load(OPENAI_CODEX_PROVIDER, "default")
        self.assertEqual("rotated-refresh", saved.refresh_token)
        self.assertEqual(refreshed_id, saved.id_token)
        self.assertEqual("new-account", saved.account_id)
        _, kwargs = session.calls[0]
        self.assertEqual("refresh_token", kwargs["json"]["grant_type"])
        self.assertFalse(kwargs["allow_redirects"])

    def test_anthropic_refresh_retries_network_and_5xx_only(self):
        self.store.save(
            _credential(
                ANTHROPIC_OAUTH_PROVIDER,
                expires_at=1_050,
            )
        )
        session = FakeSession(
            [
                FakeResponse(status_code=503),
                FakeResponse(
                    payload={
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 500,
                    }
                ),
            ]
        )
        sleeps = []
        manager = OAuthCredentialManager(
            ANTHROPIC_OAUTH_PROVIDER,
            store=self.store,
            session=session,
            now_fn=lambda: 1_000,
            sleep_fn=sleeps.append,
        )
        self.assertEqual("new-access", manager.get_access_token())
        self.assertEqual([0.5], sleeps)
        self.assertEqual(2, len(session.calls))

    def test_concurrent_expiry_refresh_is_single_flight(self):
        self.store.save(
            _credential(
                ANTHROPIC_OAUTH_PROVIDER,
                expires_at=1_050,
            )
        )
        session = FakeSession(
            [
                FakeResponse(
                    payload={
                        "access_token": "single-flight-access",
                        "refresh_token": "single-flight-refresh",
                        "expires_in": 500,
                    }
                )
            ]
        )
        managers = [
            OAuthCredentialManager(
                ANTHROPIC_OAUTH_PROVIDER,
                store=self.store,
                session=session,
                now_fn=lambda: 1_000,
                sleep_fn=lambda _delay: None,
            )
            for _ in range(2)
        ]
        barrier = threading.Barrier(2)
        results = []

        def resolve(manager):
            barrier.wait()
            results.append(manager.get_access_token())

        threads = [
            threading.Thread(target=resolve, args=(manager,))
            for manager in managers
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(
            ["single-flight-access", "single-flight-access"],
            sorted(results),
        )
        self.assertEqual(1, len(session.calls))

    def test_status_and_logout_never_expose_secret(self):
        self.store.save(_credential(ANTHROPIC_OAUTH_PROVIDER))
        status = credential_status(
            ANTHROPIC_OAUTH_PROVIDER,
            store=self.store,
            now_fn=lambda: 1_000,
        )
        rendered = json.dumps(status)
        self.assertNotIn("access-sentinel", rendered)
        self.assertNotIn("refresh-sentinel", rendered)
        self.assertNotIn("account-sentinel", rendered)
        self.assertTrue(logout(ANTHROPIC_OAUTH_PROVIDER, store=self.store))
        self.assertFalse(logout(ANTHROPIC_OAUTH_PROVIDER, store=self.store))


class LoginAndCliTests(unittest.TestCase):
    """@brief Verify risk gating, manual login, and secret-free CLI output."""

    def test_noninteractive_risk_requires_flag(self):
        with self.assertRaisesRegex(OAuthConfigurationError, "accept-account-risk"):
            confirm_account_risk(
                OPENAI_CODEX_PROVIDER,
                accepted_flag=False,
                output_fn=lambda _value: None,
                interactive=False,
            )

    def test_interactive_risk_requires_exact_accept_even_with_flag(self):
        prompts = []
        with self.assertRaisesRegex(OAuthAuthenticationError, "not accepted"):
            confirm_account_risk(
                OPENAI_CODEX_PROVIDER,
                accepted_flag=True,
                input_fn=lambda prompt: prompts.append(prompt) or "accept",
                output_fn=lambda _value: None,
                interactive=True,
            )
        self.assertEqual(["Type ACCEPT to continue: "], prompts)

    def test_manual_login_saves_consent_with_notice_hash(self):
        keyring = FakeKeyring()
        store = KeyringCredentialStore(keyring)
        captured = {}

        def browser_open(url):
            captured["state"] = parse_qs(urlparse(url).query)["state"][0]

        def input_fn(_prompt):
            return f"code=auth-code&state={captured['state']}"

        access = _jwt(
            {"exp": 9_000}
        )
        id_token = _jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-fixture"}}
        )
        credential = login(
            OPENAI_CODEX_PROVIDER,
            manual=True,
            accept_account_risk=True,
            store=store,
            session=FakeSession(
                [
                    FakeResponse(
                        payload={
                            "access_token": access,
                            "refresh_token": "refresh-value",
                            "id_token": id_token,
                        }
                    )
                ]
            ),
            input_fn=input_fn,
            output_fn=lambda _value: None,
            browser_open=browser_open,
            interactive=False,
            now_fn=lambda: 1_000,
        )
        self.assertEqual(RISK_NOTICE_VERSION, credential.risk_notice_version)
        self.assertEqual(
            risk_notice_sha256(OPENAI_CODEX_PROVIDER),
            credential.risk_notice_sha256,
        )

    def test_login_falls_back_to_manual_when_ports_are_unavailable(self):
        keyring = FakeKeyring()
        store = KeyringCredentialStore(keyring)
        captured = {}
        output = []

        def unavailable():
            raise OAuthConfigurationError("ports unavailable")

        def browser_open(url):
            captured["state"] = parse_qs(urlparse(url).query)["state"][0]

        access = _jwt(
            {"exp": 9_000}
        )
        id_token = _jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-fixture"}}
        )
        login(
            OPENAI_CODEX_PROVIDER,
            accept_account_risk=True,
            store=store,
            session=FakeSession(
                [
                    FakeResponse(
                        payload={
                            "access_token": access,
                            "refresh_token": "refresh-value",
                            "id_token": id_token,
                        }
                    )
                ]
            ),
            input_fn=lambda _prompt: f"code=x&state={captured['state']}",
            output_fn=output.append,
            browser_open=browser_open,
            interactive=False,
            callback_server_factory=unavailable,
            now_fn=lambda: 1_000,
        )
        self.assertTrue(any("falling back" in line for line in output))

    def test_automatic_openai_login_uses_bound_redirect_and_closes_server(self):
        class SuccessServer:
            redirect_uri = "http://localhost:1457/auth/callback"

            def __init__(self):
                self.closed = False
                self.expected_state = None

            def expect_state(self, state):
                self.expected_state = state

            def wait(self):
                return f"code=auth-code&state={self.expected_state}"

            def close(self):
                self.closed = True

        server = SuccessServer()
        captured = {}

        def browser_open(url):
            captured.update(parse_qs(urlparse(url).query))

        access = _jwt({"exp": 9_000})
        id_token = _jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-fixture"}}
        )
        store = KeyringCredentialStore(FakeKeyring())
        login(
            OPENAI_CODEX_PROVIDER,
            accept_account_risk=True,
            store=store,
            session=FakeSession(
                [
                    FakeResponse(
                        payload={
                            "access_token": access,
                            "refresh_token": "refresh-value",
                            "id_token": id_token,
                        }
                    )
                ]
            ),
            output_fn=lambda _value: None,
            browser_open=browser_open,
            interactive=False,
            callback_server_factory=lambda: server,
            now_fn=lambda: 1_000,
        )
        self.assertEqual([server.redirect_uri], captured["redirect_uri"])
        self.assertTrue(server.expected_state)
        self.assertTrue(server.closed)
        self.assertIsNotNone(store.load(OPENAI_CODEX_PROVIDER, "default"))

    def test_login_always_closes_loopback_server_after_timeout(self):
        class TimeoutServer:
            redirect_uri = "http://localhost:1455/auth/callback"

            def __init__(self):
                self.closed = False
                self.expected_state = None

            def expect_state(self, state):
                self.expected_state = state

            def wait(self):
                raise OAuthAuthenticationError("callback timed out")

            def close(self):
                self.closed = True

        server = TimeoutServer()
        with self.assertRaisesRegex(OAuthAuthenticationError, "timed out"):
            login(
                OPENAI_CODEX_PROVIDER,
                accept_account_risk=True,
                store=KeyringCredentialStore(FakeKeyring()),
                session=FakeSession([]),
                output_fn=lambda _value: None,
                browser_open=lambda _url: None,
                interactive=False,
                callback_server_factory=lambda: server,
            )
        self.assertTrue(server.closed)
        self.assertTrue(server.expected_state)

    def test_cli_status_output_has_no_tokens_or_account_id(self):
        keyring = FakeKeyring()
        store = KeyringCredentialStore(keyring)
        store.save(_credential(OPENAI_CODEX_PROVIDER))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(benchmark_auth, "KeyringCredentialStore", return_value=store),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = benchmark_auth.main(
                ["status", OPENAI_CODEX_PROVIDER, "--profile", "default"]
            )
        self.assertEqual(0, result)
        rendered = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("access-sentinel", rendered)
        self.assertNotIn("refresh-sentinel", rendered)
        self.assertNotIn("account-sentinel", rendered)


if __name__ == "__main__":
    unittest.main()
