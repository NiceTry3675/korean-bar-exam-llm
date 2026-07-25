"""@brief Verify the offline Python test guard itself."""

import os
import socket
import subprocess
import sys
import unittest


@unittest.skipUnless(
    os.environ.get("BENCHMARK_TEST_NETWORK_BLOCKED") == "1",
    "scripts/run_tests_offline.py에서만 네트워크 차단을 검증합니다.",
)
class OfflineGuardTest(unittest.TestCase):
    """@brief Direct socket and subprocess bypasses must fail before OS access."""

    def test_socket_and_subprocess_are_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "Network access is blocked"):
            socket.SocketType()
        with self.assertRaisesRegex(RuntimeError, "Network access is blocked"):
            subprocess.run([sys.executable, "--version"], check=False)


if __name__ == "__main__":
    unittest.main()
