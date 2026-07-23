#!/usr/bin/env python3
"""Regression tests for SSH reconnect throttling and request wake-up."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

import ssh_client
from ssh_client import SSHConnection


class ReconnectPolicyTests(unittest.TestCase):
    def test_five_fast_failures_then_slow_retry_and_request_retries_now(self):
        conn = SSHConnection({
            "host": "unreachable.test",
            "user": "deploy",
            "auto_reconnect": True,
            "reconnect_interval": 0.01,
        })
        conn.reconnect_slow_interval = 0.2
        attempts = []

        def fail_connect():
            attempts.append(time.monotonic())
            raise OSError("unreachable")

        conn._connect_direct = fail_connect
        try:
            with self.assertRaises(OSError):
                conn.connect()

            deadline = time.monotonic() + 1
            while len(attempts) < 5 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(len(attempts), 5)

            time.sleep(0.05)
            self.assertEqual(len(attempts), 5, "retry should be in slow cooldown")

            with self.assertRaises(OSError):
                conn.connect()
            self.assertEqual(
                len(attempts),
                6,
                "an explicit request must retry immediately during cooldown",
            )
        finally:
            conn.close()

    def test_auto_reconnect_false_disables_background_attempts(self):
        conn = SSHConnection({
            "host": "unreachable.test",
            "user": "deploy",
            "auto_reconnect": False,
            "reconnect_interval": 0.01,
        })
        attempts = []

        def fail_connect():
            attempts.append(time.monotonic())
            raise OSError("unreachable")

        conn._connect_direct = fail_connect
        try:
            with self.assertRaises(OSError):
                conn.connect()
            time.sleep(0.05)
            self.assertEqual(len(attempts), 1)
        finally:
            conn.close()

    def test_global_auto_reconnect_and_interval_are_inherited(self):
        with patch.object(ssh_client, "global_config", {
            "server": {
                "auto_reconnect": False,
                "reconnect_interval": 17,
            }
        }):
            conn = SSHConnection({
                "host": "example.test",
                "user": "deploy",
            })
        try:
            self.assertFalse(conn.auto_reconnect)
            self.assertEqual(conn.reconnect_interval, 17)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
