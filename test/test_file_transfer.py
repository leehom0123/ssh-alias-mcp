#!/usr/bin/env python3
"""Unit tests for the single-file transfer primitives and the reuse chain
(pytest).

Script vs. ordinary file is only a path difference, so one primitive serves
both directions:
    upload_script()  -> upload_file()
    download_script() -> download_file() -> download()

Everything here is offline: paramiko and the network are mocked.
"""
import pytest
from unittest.mock import MagicMock, patch

import mcp_server
from mcp_server import STATIC_TOOLS
from ssh_client import SSHConnection


def _conn(cfg=None):
    conn = SSHConnection({"host": "example.test", "user": "tester", **(cfg or {})})
    conn.connect = MagicMock()
    conn._client = MagicMock()
    conn.scripts_dir = "/opt/scripts"
    return conn


def _ok_result():
    return {"stdout": "", "stderr": "", "code": 0}


def test_upload_file_happy_path_uses_sftp(tmp_path):
    local = tmp_path / "config.json"
    local.write_text("{}")
    conn = _conn()
    conn.run = MagicMock(return_value=_ok_result())
    with patch("ssh_client.paramiko.SFTPClient") as sftp_cls:
        sftp = sftp_cls.from_transport.return_value
        result = conn.upload_file(str(local), "/srv/app/config.json")
    assert result["code"] == 0
    assert "Uploaded" in result["stdout"]
    assert result["remote_path"] == "/srv/app/config.json"
    sftp.put.assert_called_once_with(str(local), "/srv/app/config.json")


def test_upload_file_respects_path_allow_list(tmp_path):
    conn = _conn()
    conn._allowed_paths = {"local": [], "remote": ["/srv/app"]}
    local = tmp_path / "x.txt"
    local.write_text("x")
    with pytest.raises(ValueError, match="Remote path not allowed"):
        conn.upload_file(str(local), "/etc/evil.conf")


def test_generated_helper_commands_are_internal(tmp_path):
    # code-generated probes must bypass the user-command filter
    conn = _conn()
    conn.run = MagicMock(return_value=_ok_result())
    local = tmp_path / "run.sh"
    local.write_text("#!/bin/sh")
    with patch("ssh_client.paramiko.SFTPClient"):
        conn.upload_file(str(local), "/srv/app/run.sh",
                         overwrite=True, sudo=True, executable=True)
    assert conn.run.called, "expected helper run() calls"
    for call in conn.run.call_args_list:
        assert call.kwargs.get("internal", False), (
            f"helper run() call missing internal=True: {call}"
        )


def test_upload_script_delegates_to_upload_file(tmp_path):
    local = tmp_path / "deploy.sh"
    # a real file so any accidental read on the delegate path stays honest;
    # only the basename matters for the delegation
    local.write_text("#!/bin/sh\necho ok\n")
    conn = _conn()
    conn.upload_file = MagicMock(return_value=dict(_ok_result(), stdout="raw"))
    result = conn.upload_script(str(local), timeout=77, sudo=True)
    conn.upload_file.assert_called_once_with(
        str(local), "/opt/scripts/deploy.sh", 77, True, True,
        executable=True,
    )
    assert result["stdout"] == "Script uploaded to /opt/scripts/deploy.sh\n"


def test_download_file_delegates_to_download():
    conn = _conn()
    conn.download = MagicMock(return_value=dict(
        _ok_result(), stdout="Downloaded", remote_path="/r/f", local_path="out"))
    result = conn.download_file("/r/f", "out", timeout=7, overwrite=False, sudo=True)
    conn.download.assert_called_once_with(
        "/r/f", "out", timeout=7, overwrite=False, sudo=True)
    assert result["code"] == 0


def test_download_script_reuses_download_file():
    conn = _conn()
    conn.download_file = MagicMock(return_value=_ok_result())
    conn.download_script("fix.sh", "./fix.sh", timeout=33, overwrite=False, sudo=True)
    conn.download_file.assert_called_once_with(
        "/opt/scripts/fix.sh", "./fix.sh", timeout=33, overwrite=False, sudo=True)


def test_download_script_rejects_traversal():
    conn = _conn()
    conn.download_file = MagicMock()
    with pytest.raises(ValueError, match="Invalid script_name"):
        conn.download_script("../escape.sh", "./out")
    conn.download_file.assert_not_called()


def test_download_file_tool_spec_registered():
    spec = STATIC_TOOLS["ssh_download_file"]
    assert spec.name == "ssh_download_file"
    by_name = {a.name: a for a in spec.args}
    assert "server" in by_name
    assert by_name["remote_path"].required is True
    assert by_name["local_path"].required is True
    assert by_name["timeout"].default == 300
    assert by_name["overwrite"].default is True
    assert by_name["sudo"].default is False


def test_download_file_handler_maps_args_to_connection():
    conn = MagicMock()
    conn.download_file.return_value = _ok_result()
    with patch("mcp_server._server_conn", return_value=conn):
        STATIC_TOOLS["ssh_download_file"].handler({
            "server": "srv", "remote_path": "/etc/app.log",
            "local_path": "./app.log", "timeout": 60,
            "overwrite": False, "sudo": True,
        })
    # The MCP handler caps the requested timeout to the transport
    # lifetime via _mcp_safe_timeout, same as ssh_run.
    expected = mcp_server._mcp_safe_timeout(60)
    conn.download_file.assert_called_once_with(
        "/etc/app.log", "./app.log", expected, False, True)
