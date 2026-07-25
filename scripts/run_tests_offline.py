#!/usr/bin/env python3
"""@brief Run the Python test suite with outbound sockets disabled."""

from __future__ import annotations

from pathlib import Path
import os
import socket
import sys
import unittest


class OfflineNetworkError(RuntimeError):
    """@brief Raised when a test attempts outbound network access."""


def _blocked(*_args, **_kwargs):
    raise OfflineNetworkError("Network access is blocked during tests.")


class OfflineSocket(socket.socket):
    """@brief Socket subclass that permits construction but rejects outbound I/O."""

    def connect(self, *_args, **_kwargs):
        return _blocked()

    def connect_ex(self, *_args, **_kwargs):
        return _blocked()

    def sendto(self, *_args, **_kwargs):
        return _blocked()


def install_network_guard() -> None:
    """@brief Block all audited socket activity, including pre-imported aliases."""
    def _audit_network(event, _args):
        if event.startswith("socket.") or event in {
            "os.posix_spawn",
            "os.spawn",
            "os.system",
            "subprocess.Popen",
        }:
            raise OfflineNetworkError(
                f"Network access is blocked during tests ({event})."
            )

    sys.addaudithook(_audit_network)
    socket.socket = OfflineSocket
    socket.SocketType = OfflineSocket
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    os.environ["BENCHMARK_TEST_NETWORK_BLOCKED"] = "1"


def main() -> int:
    """@brief Install the guard, discover tests, and return a conventional exit code."""
    install_network_guard()

    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    suite = unittest.defaultTestLoader.discover(str(repo_root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
