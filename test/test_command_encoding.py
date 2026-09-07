"""Regression tests for Unicode SSH command handling.

Offline: mocks the paramiko channel, no live SSH server required.
"""
import pytest
from unittest.mock import MagicMock

from ssh_client import SSHConnection, _repair_surrogateescaped_command

COMMAND = "printf '%s' '只回答一句话：你现在运行正常吗？'"


def test_valid_unicode_is_unchanged():
    assert _repair_surrogateescaped_command(COMMAND) == COMMAND


def test_repairs_utf8_misdecoded_as_gbk_with_surrogateescape():
    damaged = COMMAND.encode("utf-8").decode("gbk", errors="surrogateescape")
    assert any(0xDC80 <= ord(char) <= 0xDCFF for char in damaged)
    assert _repair_surrogateescaped_command(damaged) == COMMAND


def test_run_repairs_before_paramiko_exec_command():
    damaged = COMMAND.encode("utf-8").decode("gbk", errors="surrogateescape")
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

    channel.exec_command.assert_called_once_with(COMMAND)
    assert result == {"stdout": "", "stderr": "", "code": 0}


def test_unrepairable_surrogate_is_rejected():
    with pytest.raises(ValueError, match="invalid surrogate-escaped text"):
        _repair_surrogateescaped_command("echo \udc80")
