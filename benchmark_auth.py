#!/usr/bin/env python3
"""@brief Separate OAuth login/status/logout CLI for benchmark providers."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Optional, Sequence

from oauth_auth import (
    SUPPORTED_OAUTH_PROVIDERS,
    KeyringCredentialStore,
    OAuthError,
    credential_status,
    login,
    logout,
)


def _build_parser() -> argparse.ArgumentParser:
    """@brief Define the credential-only command line without benchmark options."""
    parser = argparse.ArgumentParser(
        description="Manage benchmark subscription OAuth credentials in OS Keychain."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    login_parser = commands.add_parser("login", help="Review risk and sign in.")
    login_parser.add_argument("provider", choices=sorted(SUPPORTED_OAUTH_PROVIDERS))
    login_parser.add_argument("--profile", default="default")
    login_parser.add_argument(
        "--manual",
        action="store_true",
        help="For OpenAI, paste a callback instead of running a localhost listener.",
    )
    login_parser.add_argument(
        "--accept-account-risk",
        action="store_true",
        help="Explicitly accept the displayed account-risk notice in non-interactive use.",
    )

    status_parser = commands.add_parser("status", help="Show non-secret login status.")
    status_parser.add_argument("provider", nargs="?", choices=sorted(SUPPORTED_OAUTH_PROVIDERS))
    status_parser.add_argument("--profile", default="default")

    logout_parser = commands.add_parser("logout", help="Delete tokens and risk consent.")
    logout_parser.add_argument("provider", choices=sorted(SUPPORTED_OAUTH_PROVIDERS))
    logout_parser.add_argument("--profile", default="default")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """@brief Run one auth operation and report only secret-free data."""
    args = _build_parser().parse_args(argv)
    try:
        store = KeyringCredentialStore()
        if args.command == "login":
            login(
                args.provider,
                profile=args.profile,
                manual=args.manual,
                accept_account_risk=args.accept_account_risk,
                store=store,
                callback_input_fn=getpass.getpass,
                interactive=sys.stdin.isatty(),
            )
            print(
                json.dumps(
                    credential_status(args.provider, args.profile, store=store),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "status":
            providers = [args.provider] if args.provider else sorted(SUPPORTED_OAUTH_PROVIDERS)
            print(
                json.dumps(
                    [
                        credential_status(provider, args.profile, store=store)
                        for provider in providers
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        removed = logout(args.provider, args.profile, store=store)
        print(
            json.dumps(
                {
                    "provider": args.provider,
                    "profile": args.profile,
                    "logged_out": True,
                    "credential_removed": removed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except OAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: OAuth operation cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
