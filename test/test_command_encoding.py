#!/usr/bin/env python3
"""Regression tests for Unicode SSH command handling."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

SKILL_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

from ssh_client import SSHConnection, _repair_surrogateescaped_command


class CommandEncodingTests(unittest.TestCase):
    command = "printf '%s' '只回答一句话：你现在运行正常吗？'"

    def test_valid_unicode_is_unchanged(self):
        self.assertEqual(_repair_surrogateescaped_command(self.command), self.command)

    def test_repairs_utf8_misdecoded_as_gbk_with_surrogateescape(self):
        damaged = self.command.encode("utf-8").decode("gbk", errors="surrogateescape")
        self.assertTrue(any(0xDC80 <= ord(char) <= 0xDCFF for char in damaged))
        self.assertEqual(_repair_surrogateescaped_command(damaged), self.command)

    def test_run_repairs_before_paramiko_exec_command(self):
        damaged = self.command.encode("utf-8").decode("gbk", errors="surrogateescape")
        channel = MagicMock()
        channel.exit_status_ready.return_value = True
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        channel.recv_exit_status.return_value = 0
        transport = MagicMock()
        transport.open_session.return_value = channel

        conn = SSHConnection({"host": "example.test", "user": "tester"})
        conn.connect = MagicMock()
        conn._client = MagicMock()
        conn._client.get_transport.return_value = transport

        result = conn.run(damaged, timeout=1)

        channel.exec_command.assert_called_once_with(self.command)
        self.assertEqual(result, {"stdout": "", "stderr": "", "code": 0})

    def test_unrepairable_surrogate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid surrogate-escaped text"):
            _repair_surrogateescaped_command("echo \udc80")


if __name__ == "__main__":
    unittest.main()
