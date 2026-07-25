#!/usr/bin/env python3
"""
@brief OAuth login, Keychain storage, and token refresh for benchmark providers.

Tokens are stored as one JSON secret in the operating-system credential store.
This module intentionally has no plaintext or environment-variable fallback.
Reference implementation notices are preserved in THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse


OPENAI_CODEX_PROVIDER = "openai-codex-oauth"
ANTHROPIC_OAUTH_PROVIDER = "anthropic-oauth"
SUPPORTED_OAUTH_PROVIDERS = {
    OPENAI_CODEX_PROVIDER,
    ANTHROPIC_OAUTH_PROVIDER,
}

# OS Keychain 항목의 service 이름. 저장소 초기 이름에서 유래했지만 벤치마크와는
# 무관한 식별자이며, 값을 바꾸면 이미 저장된 credential이 고아가 되어 모든
# 제공자를 다시 로그인해야 하므로 그대로 유지한다.
KEYRING_SERVICE = "2026-csat.oauth"
RISK_NOTICE_VERSION = "2026-07-24-v1"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "api.connectors.read",
    "api.connectors.invoke",
)
OPENAI_LOOPBACK_PORTS = (1455, 1457)

ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_CALLBACK_URL = "https://platform.claude.com/oauth/code/callback"
ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
ANTHROPIC_SCOPES = (
    "org:create_api_key",
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
)

_PROFILE_PATTERN = r"[A-Za-z0-9._-]+"
_REFRESH_SKEW_SECONDS = 5 * 60
_OPENAI_FALLBACK_REFRESH_SECONDS = 8 * 24 * 60 * 60
_TOKEN_TIMEOUT_SECONDS = 30
_SECURE_KEYRING_BACKEND_PREFIXES = (
    "keyring.backends.macos.",
    "keyring.backends.os_x.",
    "keyring.backends.windows.",
    "keyring.backends.secretservice.",
    "keyring.backends.kwallet.",
    "keyring.backends.libsecret.",
)
_RISK_NOTICES = {
    OPENAI_CODEX_PROVIDER: (
        "ChatGPT subscription OAuth is intended for Codex clients. Automated benchmark "
        "use may violate OpenAI consumer terms or trigger account restrictions. "
        "Continue only with an account whose suspension or loss you accept.\n"
        "Terms: https://openai.com/policies/terms-of-use/"
    ),
    ANTHROPIC_OAUTH_PROVIDER: (
        "Claude Pro/Max OAuth is not an approved third-party integration. Automated "
        "benchmark use may violate Anthropic consumer terms and can restrict or ban "
        "the account. Continue only with an account whose suspension or loss you accept.\n"
        "Terms: https://www.anthropic.com/legal/consumer-terms"
    ),
}
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class OAuthError(RuntimeError):
    """@brief Base class for secret-safe OAuth failures."""


class OAuthConfigurationError(OAuthError):
    """@brief Raised for unavailable storage or invalid local OAuth configuration."""


class OAuthAuthenticationError(OAuthError):
    """@brief Raised when login or token refresh is required."""


@dataclass(frozen=True)
class OAuthFlow:
    """@brief One authorization request with its PKCE and state verifier."""

    provider: str
    url: str
    redirect_uri: str
    state: str = field(repr=False)
    verifier: str = field(repr=False)


@dataclass(frozen=True)
class OAuthCredential:
    """@brief Complete provider credential persisted as one Keychain secret."""

    provider: str
    profile: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    id_token: Optional[str] = field(default=None, repr=False)
    account_id: Optional[str] = field(default=None, repr=False)
    expires_at: Optional[float] = None
    last_refresh_at: float = 0.0
    risk_notice_version: str = ""
    risk_notice_sha256: str = ""
    risk_accepted_at: str = ""

    def to_secret_json(self) -> str:
        """@brief Serialize the entire credential for one atomic Keychain update."""
        return json.dumps(
            {
                "schema_version": 1,
                "provider": self.provider,
                "profile": self.profile,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "id_token": self.id_token,
                "account_id": self.account_id,
                "expires_at": self.expires_at,
                "last_refresh_at": self.last_refresh_at,
                "risk_notice_version": self.risk_notice_version,
                "risk_notice_sha256": self.risk_notice_sha256,
                "risk_accepted_at": self.risk_accepted_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_secret_json(
        cls, payload: str, *, expected_provider: str, expected_profile: str
    ) -> "OAuthCredential":
        """@brief Parse and validate one Keychain record without echoing its content."""
        try:
            value = json.loads(payload)
            if not isinstance(value, Mapping) or value.get("schema_version") != 1:
                raise ValueError
            provider = str(value["provider"])
            profile = str(value["profile"])
            access_token = str(value["access_token"])
            refresh_token = str(value["refresh_token"])
            if (
                provider != expected_provider
                or profile != expected_profile
                or not access_token
                or not refresh_token
            ):
                raise ValueError
            expires = value.get("expires_at")
            account_id = (
                _valid_openai_account_id(value.get("account_id"))
                if provider == OPENAI_CODEX_PROVIDER
                else _optional_nonempty_string(value.get("account_id"))
            )
            if provider == OPENAI_CODEX_PROVIDER and not account_id:
                raise ValueError
            return cls(
                provider=provider,
                profile=profile,
                access_token=access_token,
                refresh_token=refresh_token,
                id_token=_optional_nonempty_string(value.get("id_token")),
                account_id=account_id,
                expires_at=float(expires) if expires is not None else None,
                last_refresh_at=float(value.get("last_refresh_at") or 0.0),
                risk_notice_version=str(value.get("risk_notice_version") or ""),
                risk_notice_sha256=str(value.get("risk_notice_sha256") or ""),
                risk_accepted_at=str(value.get("risk_accepted_at") or ""),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise OAuthConfigurationError(
                f"Stored OAuth credential for {expected_provider}/{expected_profile} is invalid. "
                "Log out and sign in again."
            ) from None


class KeyringCredentialStore:
    """@brief Store OAuth credentials exclusively in the operating-system Keychain."""

    def __init__(self, keyring_module: Any = None):
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise OAuthConfigurationError(
                    "The keyring package is required for OAuth credentials. "
                    "Install requirements.txt; plaintext fallback is disabled."
                ) from exc
        self._keyring = keyring_module
        self._validate_backend()

    def _validate_backend(self) -> None:
        """@brief Accept only native OS credential-store backends."""
        try:
            backend = self._keyring.get_keyring()
            backend_name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
            priority = getattr(backend, "priority", 0)
            if (
                float(priority) <= 0
                or not any(
                    backend_name.startswith(prefix)
                    for prefix in _SECURE_KEYRING_BACKEND_PREFIXES
                )
            ):
                raise OAuthConfigurationError(
                    "No supported OS Keychain backend is available; plaintext fallback is disabled."
                )
        except OAuthConfigurationError:
            raise
        except Exception as exc:
            raise OAuthConfigurationError(
                "Cannot initialize the OS Keychain; plaintext fallback is disabled."
            ) from exc

    @staticmethod
    def _account(provider: str, profile: str) -> str:
        return f"{provider}:{profile}"

    def load(self, provider: str, profile: str) -> Optional[OAuthCredential]:
        """@brief Read and validate one provider/profile credential."""
        try:
            payload = self._keyring.get_password(
                KEYRING_SERVICE, self._account(provider, profile)
            )
        except Exception:
            raise OAuthConfigurationError(
                "Cannot read the OAuth credential from OS Keychain."
            ) from None
        if payload is None:
            return None
        return OAuthCredential.from_secret_json(
            payload,
            expected_provider=provider,
            expected_profile=profile,
        )

    def save(self, credential: OAuthCredential) -> None:
        """@brief Replace the complete credential in one Keychain operation."""
        try:
            self._keyring.set_password(
                KEYRING_SERVICE,
                self._account(credential.provider, credential.profile),
                credential.to_secret_json(),
            )
        except Exception:
            raise OAuthConfigurationError(
                "Cannot save the OAuth credential to OS Keychain."
            ) from None

    def delete(self, provider: str, profile: str) -> bool:
        """@brief Delete one credential, treating an absent record as logged out."""
        try:
            account = self._account(provider, profile)
            if self._keyring.get_password(KEYRING_SERVICE, account) is None:
                return False
            self._keyring.delete_password(
                KEYRING_SERVICE, account
            )
        except Exception:
            raise OAuthConfigurationError(
                "Cannot delete the OAuth credential from OS Keychain."
            ) from None
        return True


class LoopbackCallbackServer:
    """@brief Minimal localhost callback receiver restricted to the Codex path."""

    def __init__(self, ports: Sequence[int] = OPENAI_LOOPBACK_PORTS):
        self._callback_input: Optional[str] = None
        self._expected_state: Optional[str] = None
        self._server: Optional[HTTPServer] = None
        owner = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def _send_html(self, status: int, message: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                self.wfile.write(message.encode("utf-8"))

            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                status, callback_input = _validate_loopback_callback(
                    self.path,
                    self.headers.get("Host", ""),
                    owner.port,
                    owner._expected_state,
                )
                if status == 400:
                    self._send_html(400, "<h1>Invalid OAuth callback</h1>")
                    return
                if status == 404:
                    self._send_html(404, "<h1>OAuth callback not found</h1>")
                    return
                owner._callback_input = callback_input
                self._send_html(
                    200,
                    "<h1>OAuth login received</h1><p>You can close this window.</p>",
                )

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        last_error: Optional[Exception] = None
        for port in ports:
            try:
                self._server = HTTPServer(("127.0.0.1", int(port)), CallbackHandler)
                address = getattr(self._server, "server_address", None)
                self.port = int(address[1]) if address else int(port)
                break
            except OSError as exc:
                last_error = exc
        if self._server is None:
            raise OAuthConfigurationError(
                "Cannot bind OAuth callback ports 1455 or 1457; retry with --manual."
            ) from last_error

    @property
    def redirect_uri(self) -> str:
        """@brief Return the registered localhost URI for the bound port."""
        return f"http://localhost:{self.port}/auth/callback"

    def expect_state(self, state: str) -> None:
        """@brief Install the state that the loopback callback must match."""
        self._expected_state = str(state)

    def wait(self, timeout_seconds: float = 120.0) -> str:
        """@brief Serve callbacks until one arrives or the bounded wait expires."""
        if self._server is None:
            raise OAuthConfigurationError("OAuth callback server is closed.")
        deadline = time.monotonic() + timeout_seconds
        while self._callback_input is None and time.monotonic() < deadline:
            self._server.timeout = min(0.5, max(0.05, deadline - time.monotonic()))
            self._server.handle_request()
        if self._callback_input is None:
            raise OAuthAuthenticationError(
                "OAuth callback timed out. Run login again or use --manual."
            )
        return self._callback_input

    def close(self) -> None:
        """@brief Always release the loopback listener."""
        if self._server is not None:
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "LoopbackCallbackServer":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _validate_loopback_callback(
    path: str,
    host_header: str,
    port: int,
    expected_state: Optional[str],
) -> tuple[int, Optional[str]]:
    """@brief Validate one loopback request without opening a network socket."""
    try:
        host = urlparse(f"//{host_header}")
        host_valid = (
            host.hostname in {"localhost", "127.0.0.1"}
            and host.port == port
            and host.username is None
            and host.password is None
        )
    except ValueError:
        host_valid = False
    if not host_valid:
        return 400, None
    parsed = urlparse(path)
    if parsed.path != "/auth/callback":
        return 404, None
    params = parse_qs(parsed.query, keep_blank_values=True)
    code = params.get("code", [""])[0]
    state = params.get("state", [""])[0]
    if not (
        code
        and state
        and expected_state
        and secrets.compare_digest(state, expected_state)
    ):
        return 400, None
    return 200, parsed.query


def validate_provider(provider: str) -> str:
    """@brief Validate an exact OAuth provider ID."""
    if provider not in SUPPORTED_OAUTH_PROVIDERS:
        expected = ", ".join(sorted(SUPPORTED_OAUTH_PROVIDERS))
        raise OAuthConfigurationError(
            f"Unsupported OAuth provider '{provider}'. Expected one of: {expected}."
        )
    return provider


def validate_profile(profile: str) -> str:
    """@brief Validate a profile label before using it as a Keychain account key."""
    import re

    if not re.fullmatch(_PROFILE_PATTERN, str(profile or "")):
        raise OAuthConfigurationError(
            "OAuth profile must match [A-Za-z0-9._-]+."
        )
    return str(profile)


def risk_notice(provider: str) -> str:
    """@brief Return the versioned account-risk notice for a provider."""
    return _RISK_NOTICES[validate_provider(provider)]


def risk_notice_sha256(provider: str) -> str:
    """@brief Hash the exact notice so changed wording requires new consent."""
    return hashlib.sha256(risk_notice(provider).encode("utf-8")).hexdigest()


def generate_pkce() -> tuple[str, str]:
    """@brief Generate a 64-byte URL-safe PKCE verifier and S256 challenge."""
    verifier = _base64url(os.urandom(64))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def create_authorization_flow(
    provider: str, *, redirect_uri: Optional[str] = None
) -> OAuthFlow:
    """@brief Construct a provider authorization URL with PKCE and state."""
    provider = validate_provider(provider)
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(32)
    if provider == OPENAI_CODEX_PROVIDER:
        redirect = redirect_uri or "http://localhost:1455/auth/callback"
        parsed_redirect = urlparse(redirect)
        try:
            redirect_valid = (
                parsed_redirect.scheme == "http"
                and parsed_redirect.hostname == "localhost"
                and parsed_redirect.port in OPENAI_LOOPBACK_PORTS
                and parsed_redirect.path == "/auth/callback"
                and not parsed_redirect.query
                and not parsed_redirect.fragment
                and parsed_redirect.username is None
                and parsed_redirect.password is None
            )
        except ValueError:
            redirect_valid = False
        if not redirect_valid:
            raise OAuthConfigurationError(
                "OpenAI redirect URI must be the allowlisted localhost callback "
                "on port 1455 or 1457."
            )
        params = {
            "response_type": "code",
            "client_id": OPENAI_CLIENT_ID,
            "redirect_uri": redirect,
            "scope": " ".join(OPENAI_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
        }
        base_url = OPENAI_AUTHORIZE_URL
    else:
        redirect = ANTHROPIC_CALLBACK_URL
        params = {
            "code": "true",
            "client_id": ANTHROPIC_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": " ".join(ANTHROPIC_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        base_url = ANTHROPIC_AUTHORIZE_URL
    return OAuthFlow(
        provider=provider,
        url=f"{base_url}?{urlencode(params)}",
        redirect_uri=redirect,
        state=state,
        verifier=verifier,
    )


def parse_callback_input(
    value: str,
    *,
    expected_state: str,
    expected_redirect_uri: Optional[str] = None,
) -> tuple[str, str]:
    """@brief Parse URL/query/code#state forms and require exact state matching."""
    trimmed = str(value or "").strip()
    code: Optional[str] = None
    state: Optional[str] = None
    if not trimmed:
        raise OAuthAuthenticationError("OAuth callback is empty.")
    try:
        parsed = urlparse(trimmed)
        if parsed.scheme and parsed.netloc:
            if expected_redirect_uri and not _same_callback_target(
                parsed, urlparse(expected_redirect_uri)
            ):
                raise OAuthAuthenticationError(
                    "OAuth callback URL did not match the expected redirect URI."
                )
            params = parse_qs(parsed.query, keep_blank_values=True)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            fragment_params = parse_qs(parsed.fragment, keep_blank_values=True)
            code = code or fragment_params.get("code", [None])[0]
            state = state or fragment_params.get("state", [None])[0]
            if code and not state and parsed.fragment and "=" not in parsed.fragment:
                state = parsed.fragment
    except OAuthAuthenticationError:
        raise
    except ValueError:
        pass
    if not code and trimmed.count("#") == 1:
        code, state = trimmed.split("#", 1)
    if not code:
        query = trimmed[1:] if trimmed.startswith("?") else trimmed
        params = parse_qs(query, keep_blank_values=True)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
    if not code or not state:
        raise OAuthAuthenticationError(
            "OAuth callback must include both code and state; code-only input is rejected."
        )
    if not secrets.compare_digest(str(state), expected_state):
        raise OAuthAuthenticationError("OAuth callback state did not match; login was cancelled.")
    return str(code), str(state)


def _same_callback_target(actual: Any, expected: Any) -> bool:
    """@brief Compare a pasted callback URL to the provider's registered target."""
    try:
        return bool(
            actual.scheme.lower() == expected.scheme.lower()
            and actual.hostname
            and expected.hostname
            and actual.hostname.lower() == expected.hostname.lower()
            and actual.port == expected.port
            and (actual.path or "/") == (expected.path or "/")
            and actual.username is None
            and actual.password is None
        )
    except ValueError:
        return False


def exchange_authorization_code(
    flow: OAuthFlow,
    callback_input: str,
    *,
    session: Any = None,
    now_fn: Callable[[], float] = time.time,
) -> OAuthCredential:
    """@brief Validate a callback and exchange it without persisting tokens."""
    code, state = parse_callback_input(
        callback_input,
        expected_state=flow.state,
        expected_redirect_uri=flow.redirect_uri,
    )
    session = session or _requests_session()
    if flow.provider == OPENAI_CODEX_PROVIDER:
        response = _safe_post(
            session,
            OPENAI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": OPENAI_CLIENT_ID,
                "code": code,
                "code_verifier": flow.verifier,
                "redirect_uri": flow.redirect_uri,
            },
        )
    else:
        response = _safe_post(
            session,
            ANTHROPIC_TOKEN_URL,
            json_body={
                "code": code,
                "state": state,
                "grant_type": "authorization_code",
                "client_id": ANTHROPIC_CLIENT_ID,
                "redirect_uri": flow.redirect_uri,
                "code_verifier": flow.verifier,
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "axios/1.13.6",
            },
        )
    if int(getattr(response, "status_code", 0)) < 200 or int(
        getattr(response, "status_code", 0)
    ) >= 300:
        raise OAuthAuthenticationError(
            f"{flow.provider} token exchange failed with HTTP "
            f"{getattr(response, 'status_code', 'unknown')}."
        )
    payload = _response_json(response, context="token exchange")
    now = now_fn()
    access = _required_token_field(payload, "access_token")
    refresh = _required_token_field(payload, "refresh_token")
    id_token = (
        _required_token_field(payload, "id_token")
        if flow.provider == OPENAI_CODEX_PROVIDER
        else _optional_nonempty_string(payload.get("id_token"))
    )
    if (
        flow.provider == OPENAI_CODEX_PROVIDER
        and decode_jwt_payload(id_token or "") is None
    ):
        raise OAuthAuthenticationError("OpenAI login returned an invalid ID token.")
    account_id = (
        _extract_openai_account_id(access, id_token)
        if flow.provider == OPENAI_CODEX_PROVIDER
        else None
    )
    if flow.provider == OPENAI_CODEX_PROVIDER and not account_id:
        raise OAuthAuthenticationError(
            "OpenAI login token did not contain a ChatGPT account ID."
        )
    expires_at = _token_expiry(
        flow.provider,
        access,
        payload.get("expires_in"),
        now,
    )
    return OAuthCredential(
        provider=flow.provider,
        profile="default",
        access_token=access,
        refresh_token=refresh,
        id_token=id_token,
        account_id=account_id,
        expires_at=expires_at,
        last_refresh_at=now,
    )


def confirm_account_risk(
    provider: str,
    *,
    accepted_flag: bool,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    interactive: bool = True,
) -> None:
    """@brief Display the notice and require explicit acknowledgement."""
    output_fn(f"ACCOUNT RISK NOTICE ({RISK_NOTICE_VERSION})")
    output_fn(risk_notice(provider))
    if not interactive:
        if accepted_flag:
            return
        raise OAuthConfigurationError(
            "Non-interactive login requires --accept-account-risk."
        )
    try:
        accepted = input_fn("Type ACCEPT to continue: ").strip()
    except EOFError as exc:
        raise OAuthAuthenticationError(
            "OAuth login cancelled before account risk was accepted."
        ) from exc
    if accepted != "ACCEPT":
        raise OAuthAuthenticationError("OAuth login cancelled; account risk was not accepted.")


def login(
    provider: str,
    *,
    profile: str = "default",
    manual: bool = False,
    accept_account_risk: bool = False,
    store: Optional[KeyringCredentialStore] = None,
    session: Any = None,
    input_fn: Callable[[str], str] = input,
    callback_input_fn: Optional[Callable[[str], str]] = None,
    output_fn: Callable[[str], None] = print,
    browser_open: Callable[[str], Any] = webbrowser.open,
    interactive: bool = True,
    callback_server_factory: Callable[[], LoopbackCallbackServer] = LoopbackCallbackServer,
    now_fn: Callable[[], float] = time.time,
) -> OAuthCredential:
    """@brief Complete one interactive login and persist the resulting credential."""
    provider = validate_provider(provider)
    profile = validate_profile(profile)
    credential_store = store or KeyringCredentialStore()
    callback_input_fn = callback_input_fn or input_fn
    confirm_account_risk(
        provider,
        accepted_flag=accept_account_risk,
        input_fn=input_fn,
        output_fn=output_fn,
        interactive=interactive,
    )

    callback_server: Optional[LoopbackCallbackServer] = None
    if provider == OPENAI_CODEX_PROVIDER and not manual:
        try:
            callback_server = callback_server_factory()
        except OAuthConfigurationError:
            output_fn(
                "Local callback ports are unavailable; falling back to manual callback paste."
            )
            manual = True
        flow = create_authorization_flow(
            provider,
            redirect_uri=(
                callback_server.redirect_uri if callback_server is not None else None
            ),
        )
    else:
        flow = create_authorization_flow(provider)
    if callback_server is not None:
        callback_server.expect_state(flow.state)

    try:
        output_fn("Open this URL in a browser to sign in:")
        output_fn(flow.url)
        try:
            browser_open(flow.url)
        except Exception:
            output_fn("The browser could not be opened automatically; open the URL manually.")
        if callback_server is not None:
            callback_input = callback_server.wait()
        else:
            try:
                callback_input = callback_input_fn(
                    "Paste the full OAuth callback URL (or code#state): "
                )
            except EOFError as exc:
                raise OAuthAuthenticationError(
                    "OAuth login cancelled before a callback was provided."
                ) from exc
        credential = exchange_authorization_code(
            flow,
            callback_input,
            session=session,
            now_fn=now_fn,
        )
    finally:
        if callback_server is not None:
            callback_server.close()

    accepted_at = _utc_timestamp(now_fn())
    credential = OAuthCredential(
        provider=provider,
        profile=profile,
        access_token=credential.access_token,
        refresh_token=credential.refresh_token,
        id_token=credential.id_token,
        account_id=credential.account_id,
        expires_at=credential.expires_at,
        last_refresh_at=credential.last_refresh_at,
        risk_notice_version=RISK_NOTICE_VERSION,
        risk_notice_sha256=risk_notice_sha256(provider),
        risk_accepted_at=accepted_at,
    )
    credential_store.save(credential)
    return credential


class OAuthCredentialManager:
    """@brief Lazily load, validate, and rotate one Keychain credential."""

    def __init__(
        self,
        provider: str,
        profile: str = "default",
        *,
        store: Optional[KeyringCredentialStore] = None,
        session: Any = None,
        now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.provider = validate_provider(provider)
        self.profile = validate_profile(profile)
        self._store = store or KeyringCredentialStore()
        self._session = session or _requests_session()
        self._now = now_fn
        self._sleep = sleep_fn
        key = f"{self.provider}:{self.profile}"
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.Lock())

    @property
    def account_id(self) -> Optional[str]:
        """@brief Return the current account ID after consent and expiry checks."""
        return self.get_credential().account_id

    def get_access_token(self) -> str:
        """@brief Return a usable token, refreshing before expiry when needed."""
        return self.get_credential().access_token

    def get_credential(self) -> OAuthCredential:
        """@brief Load the record and perform one locked proactive refresh if needed."""
        credential = self._load_validated()
        if not self._needs_refresh(credential):
            return credential
        with self._lock:
            current = self._load_validated()
            if not self._needs_refresh(current):
                return current
            return self._refresh(current)

    def force_refresh(self) -> str:
        """@brief Force one provider refresh after a 401 and return the new token."""
        with self._lock:
            credential = self._load_validated()
            return self._refresh(credential).access_token

    def _load_validated(self) -> OAuthCredential:
        credential = self._store.load(self.provider, self.profile)
        if credential is None:
            raise OAuthAuthenticationError(
                f"No OAuth login exists for {self.provider}/{self.profile}. "
                f"Run: python3 benchmark_auth.py login {self.provider} "
                f"--profile {self.profile}"
            )
        if (
            credential.risk_notice_version != RISK_NOTICE_VERSION
            or credential.risk_notice_sha256 != risk_notice_sha256(self.provider)
        ):
            raise OAuthAuthenticationError(
                f"Account-risk consent for {self.provider}/{self.profile} is missing or stale. "
                "Log in again to review the current notice."
            )
        if self.provider == OPENAI_CODEX_PROVIDER and not credential.account_id:
            raise OAuthAuthenticationError(
                "The stored OpenAI credential has no ChatGPT account ID. Log in again."
            )
        return credential

    def _needs_refresh(self, credential: OAuthCredential) -> bool:
        now = self._now()
        if credential.expires_at is not None:
            return credential.expires_at <= now + _REFRESH_SKEW_SECONDS
        return (
            self.provider == OPENAI_CODEX_PROVIDER
            and credential.last_refresh_at + _OPENAI_FALLBACK_REFRESH_SECONDS
            <= now + _REFRESH_SKEW_SECONDS
        )

    def _refresh(self, credential: OAuthCredential) -> OAuthCredential:
        response = self._post_refresh_with_retries(credential.refresh_token)
        payload = _response_json(response, context="token refresh")
        access = _required_token_field(payload, "access_token")
        refresh = (
            _optional_nonempty_string(payload.get("refresh_token"))
            or credential.refresh_token
        )
        new_id_token = _optional_nonempty_string(payload.get("id_token"))
        if (
            self.provider == OPENAI_CODEX_PROVIDER
            and new_id_token
            and decode_jwt_payload(new_id_token) is None
        ):
            raise OAuthAuthenticationError(
                "OpenAI token refresh returned an invalid ID token. Log in again."
            )
        id_token = new_id_token or credential.id_token
        now = self._now()
        account_id = credential.account_id
        if self.provider == OPENAI_CODEX_PROVIDER:
            account_id = (
                _extract_openai_account_id(access, new_id_token)
                or credential.account_id
            )
            if not account_id:
                raise OAuthAuthenticationError(
                    "Refreshed OpenAI token has no ChatGPT account ID. Log in again."
                )
        refreshed = OAuthCredential(
            provider=credential.provider,
            profile=credential.profile,
            access_token=access,
            refresh_token=refresh,
            id_token=id_token,
            account_id=account_id,
            expires_at=_token_expiry(
                self.provider, access, payload.get("expires_in"), now
            ),
            last_refresh_at=now,
            risk_notice_version=credential.risk_notice_version,
            risk_notice_sha256=credential.risk_notice_sha256,
            risk_accepted_at=credential.risk_accepted_at,
        )
        self._store.save(refreshed)
        return refreshed

    def _post_refresh_with_retries(self, refresh_token: str) -> Any:
        for attempt in range(3):
            try:
                if self.provider == OPENAI_CODEX_PROVIDER:
                    response = self._session.post(
                        OPENAI_TOKEN_URL,
                        headers={"Content-Type": "application/json"},
                        json={
                            "client_id": OPENAI_CLIENT_ID,
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                        },
                        timeout=_TOKEN_TIMEOUT_SECONDS,
                        allow_redirects=False,
                    )
                else:
                    response = self._session.post(
                        ANTHROPIC_TOKEN_URL,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/plain, */*",
                            "User-Agent": "axios/1.13.6",
                        },
                        json={
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                            "client_id": ANTHROPIC_CLIENT_ID,
                        },
                        timeout=_TOKEN_TIMEOUT_SECONDS,
                        allow_redirects=False,
                    )
            except Exception:
                if attempt < 2:
                    self._sleep(0.5 * (2**attempt))
                    continue
                raise OAuthAuthenticationError(
                    f"{self.provider} token refresh failed due to a network error. "
                    "No provider request was made."
                ) from None
            status = int(getattr(response, "status_code", 0))
            if 200 <= status < 300:
                return response
            if 500 <= status <= 599 and attempt < 2:
                self._sleep(0.5 * (2**attempt))
                continue
            raise OAuthAuthenticationError(
                f"{self.provider} token refresh failed with HTTP {status}. Log in again."
            )
        raise OAuthAuthenticationError(f"{self.provider} token refresh failed.")


def credential_status(
    provider: str,
    profile: str = "default",
    *,
    store: Optional[KeyringCredentialStore] = None,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """@brief Return non-secret login metadata for the auth CLI."""
    provider = validate_provider(provider)
    profile = validate_profile(profile)
    credential = (store or KeyringCredentialStore()).load(provider, profile)
    if credential is None:
        return {
            "provider": provider,
            "profile": profile,
            "logged_in": False,
            "risk_consent_current": False,
            "expires_at": None,
            "refresh_needed": None,
        }
    consent_current = (
        credential.risk_notice_version == RISK_NOTICE_VERSION
        and credential.risk_notice_sha256 == risk_notice_sha256(provider)
    )
    refresh_needed = (
        credential.expires_at <= now_fn() + _REFRESH_SKEW_SECONDS
        if credential.expires_at is not None
        else (
            provider == OPENAI_CODEX_PROVIDER
            and credential.last_refresh_at + _OPENAI_FALLBACK_REFRESH_SECONDS
            <= now_fn() + _REFRESH_SKEW_SECONDS
        )
    )
    return {
        "provider": provider,
        "profile": profile,
        "logged_in": True,
        "risk_consent_current": consent_current,
        "risk_notice_version": credential.risk_notice_version,
        "risk_accepted_at": credential.risk_accepted_at,
        "expires_at": (
            _utc_timestamp(credential.expires_at)
            if credential.expires_at is not None
            else None
        ),
        "refresh_needed": refresh_needed,
    }


def logout(
    provider: str,
    profile: str = "default",
    *,
    store: Optional[KeyringCredentialStore] = None,
) -> bool:
    """@brief Remove both tokens and their associated risk acknowledgement."""
    return (store or KeyringCredentialStore()).delete(
        validate_provider(provider), validate_profile(profile)
    )


def _safe_post(
    session: Any,
    url: str,
    *,
    data: Optional[Mapping[str, str]] = None,
    json_body: Optional[Mapping[str, str]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> Any:
    """@brief Perform a token request while keeping transport details secret-safe."""
    request_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if json_body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    try:
        return session.post(
            url,
            headers=request_headers,
            data=data,
            json=json_body,
            timeout=_TOKEN_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except Exception:
        raise OAuthAuthenticationError(
            "OAuth token exchange failed due to a network error."
        ) from None


def _response_json(response: Any, *, context: str) -> Mapping[str, Any]:
    """@brief Parse an object response without copying a potentially sensitive body."""
    try:
        payload = response.json()
    except Exception:
        raise OAuthAuthenticationError(
            f"OAuth {context} returned invalid JSON."
        ) from None
    if not isinstance(payload, Mapping):
        raise OAuthAuthenticationError(f"OAuth {context} returned an invalid object.")
    return payload


def _required_token_field(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise OAuthAuthenticationError(
            f"OAuth token response is missing {field_name}."
        )
    return value


def _token_expiry(
    provider: str,
    access_token: str,
    expires_in: Any,
    now: float,
) -> Optional[float]:
    if provider == OPENAI_CODEX_PROVIDER:
        payload = decode_jwt_payload(access_token)
        exp = payload.get("exp") if payload else None
        if isinstance(exp, (int, float)) and float(exp) > 0:
            return float(exp)
        return None
    if isinstance(expires_in, (int, float)) and float(expires_in) > 0:
        return now + float(expires_in)
    if provider == ANTHROPIC_OAUTH_PROVIDER:
        raise OAuthAuthenticationError("Anthropic token response is missing expires_in.")
    return None


def decode_jwt_payload(token: str) -> Optional[Mapping[str, Any]]:
    """@brief Decode trusted-token claims for expiry/account routing without logging them."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + ("=" * (-len(parts[1]) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return payload if isinstance(payload, Mapping) else None
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _extract_openai_account_id(
    access_token: str, id_token: Optional[str]
) -> Optional[str]:
    for token in (id_token, access_token):
        if not token:
            continue
        payload = decode_jwt_payload(token)
        if not payload:
            continue
        direct = _valid_openai_account_id(payload.get("chatgpt_account_id"))
        if direct:
            return direct
        auth_claim = payload.get("https://api.openai.com/auth")
        if isinstance(auth_claim, Mapping):
            nested = _valid_openai_account_id(auth_claim.get("chatgpt_account_id"))
            if nested:
                return nested
    return None


def _requests_session() -> Any:
    try:
        import requests
    except ImportError as exc:
        raise OAuthConfigurationError(
            "The requests package is required for OAuth login and refresh."
        ) from exc
    return requests.Session()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _optional_nonempty_string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _valid_openai_account_id(value: Any) -> Optional[str]:
    """@brief Accept a bounded visible-ASCII account claim safe for an HTTP header."""
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        return None
    return value if all(0x21 <= ord(character) <= 0x7E for character in value) else None


def _utc_timestamp(epoch_seconds: float) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(epoch_seconds, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
