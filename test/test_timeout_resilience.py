#!/usr/bin/env python3
"""Regressions for MCP concurrency, parallel commands and security hardening."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import paramiko

import mcp_server
import ssh_client
from ssh_client import SSHConnection, _safe_relpath


class FakeChannel:
    def __init__(self, records=None, exit_ready=True):
        self.records = records if records is not None else []
        self.exit_ready = exit_ready

    def settimeout(self, timeout):
        pass

    def exec_command(self, command):
        self.records.append(("exec", command))

    def sendall(self, data):
        self.records.append(("stdin", data))

    def shutdown_write(self):
        self.records.append(("shutdown", None))

    def recv_ready(self):
        return False

    def recv_stderr_ready(self):
        return False

    def exit_status_ready(self):
        return self.exit_ready

    def recv_exit_status(self):
        return 0

    def close(self):
        pass


class FakeTransport:
    def __init__(self, open_error=None, records=None, exit_ready=True):
        self.open_error = open_error
        self.records = records if records is not None else []
        self.exit_ready = exit_ready

    def is_active(self):
        return True

    def is_authenticated(self):
        return True

    def open_session(self, timeout=None):
        if self.open_error:
            raise self.open_error
        return FakeChannel(self.records, self.exit_ready)


class FakeClient:
    def __init__(self, transport):
        self.transport = transport
        self.closed = False

    def get_transport(self):
        return self.transport

    def close(self):
        self.closed = True


class ResilienceTests(unittest.TestCase):
    def test_stale_transport_is_replaced_and_command_retried_before_execution(self):
        conn = SSHConnection({"host": "example.test", "user": "deploy"})
        stale = FakeClient(FakeTransport(EOFError("stale")))
        healthy = FakeClient(FakeTransport())
        conn._client = stale
        connects = 0

        def connect():
            nonlocal connects
            connects += 1
            if conn._client is None:
                conn._client = healthy

        conn.connect = connect
        result = conn.run("true", timeout=1)

        self.assertEqual(result["code"], 0)
        self.assertEqual(connects, 2)
        self.assertTrue(stale.closed)
        self.assertIs(conn._client, healthy)

    def test_commands_on_one_connection_run_in_parallel(self):
        conn = SSHConnection({"host": "example.test", "user": "deploy"})
        active = 0
        peak = 0
        state_lock = threading.Lock()

        def run(*args, **kwargs):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"stdout": "", "stderr": "", "code": 0}

        conn._run = run
        workers = [threading.Thread(target=conn.run, args=("true",)) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        self.assertGreater(peak, 1, "commands on one server must not serialize")

    def test_pool_get_returns_one_connection_per_server(self):
        pool = ssh_client.ConnectionPool()
        built = []
        cfg = {"server": {"host": "h", "user": "u"}}
        with patch.object(pool, "_resolve_config_path", lambda key: "x.yml"), \
                patch.object(pool, "_load_config", lambda key: cfg), \
                patch.object(ssh_client, "SSHConnection", side_effect=cfg["server"] and (lambda *a, **k: built.append(1) or object())):
            threads = [threading.Thread(target=pool.get, args=("srv",)) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(built), 1)

    def test_timeout_closes_channel_and_reports(self):
        conn = SSHConnection({"host": "example.test", "user": "deploy"})
        conn._client = FakeClient(FakeTransport(exit_ready=False))
        conn.connect = lambda: None
        started = time.time()
        result = conn.run("sleep 999", timeout=1)
        self.assertEqual(result["code"], -1)
        self.assertIn("timed out", result["stderr"])
        self.assertLess(time.time() - started, 5)

    def test_mcp_line_processing_can_complete_out_of_order(self):
        responses = []
        release = threading.Event()

        def handle(req):
            if req["id"] == 1:
                release.wait(1)
            return {"jsonrpc": "2.0", "id": req["id"], "result": {}}

        with patch.object(mcp_server, "handle_message", side_effect=handle), \
                patch.object(mcp_server, "write_response", side_effect=responses.append):
            slow = threading.Thread(
                target=mcp_server._process_line,
                args=(json.dumps({"id": 1}),),
            )
            fast = threading.Thread(
                target=mcp_server._process_line,
                args=(json.dumps({"id": 2}),),
            )
            slow.start()
            fast.start()
            fast.join(0.5)
            self.assertEqual(responses[0]["id"], 2)
            release.set()
            slow.join(0.5)

    def test_mcp_ping_is_supported(self):
        resp = mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}
        )
        self.assertEqual(resp["id"], 7)
        self.assertEqual(resp["result"], {})


class SecurityTests(unittest.TestCase):
    def test_sudo_password_goes_over_stdin_not_argv(self):
        conn = SSHConnection({
            "host": "example.test", "user": "deploy", "sudo_password": "s3cr3t",
        })
        records = []
        conn._client = FakeClient(FakeTransport(records=records))
        conn.connect = lambda: None
        result = conn.run("ls", timeout=1, sudo=True)
        self.assertEqual(result["code"], 0)
        exec_cmds = [c for kind, c in records if kind == "exec"]
        self.assertEqual(len(exec_cmds), 1)
        self.assertNotIn("s3cr3t", exec_cmds[0])
        self.assertTrue(exec_cmds[0].startswith("sudo -S -p '' bash -c "))
        self.assertIn(("stdin", b"s3cr3t\n"), records)

    def test_whitelist_requires_full_command_match(self):
        conn = SSHConnection({
            "host": "example.test", "user": "deploy", "whitelist": [r"ls -\w+"],
        })
        self.assertEqual(conn._check_command("ls -la"), "ls -la")
        with self.assertRaises(ValueError):
            conn._check_command("ls -la; rm -rf /")

    def test_global_security_section_is_loaded(self):
        fake_cfg = {"security": {"blacklist": ["rm -rf"], "whitelist": [],
                                 "command_template": ""}}
        with patch.object(ssh_client, "global_config", fake_cfg):
            conn = SSHConnection({"host": "example.test", "user": "deploy"})
        with self.assertRaises(ValueError):
            conn._check_command("sudo rm -rf /")

    def test_template_arguments_are_shell_quoted(self):
        conn = SSHConnection({"host": "example.test", "user": "deploy"})
        out = conn._cmd("mkdir", path="/tmp/x'; rm -rf /etc")
        self.assertEqual(out, "mkdir -p " + __import__("shlex").quote("/tmp/x'; rm -rf /etc"))
        self.assertNotIn("rm -rf", out.replace(__import__("shlex").quote("/tmp/x'; rm -rf /etc"), ""))

    def test_script_name_traversal_is_rejected(self):
        self.assertEqual(_safe_relpath("sub/dir.sh", "script_name"), "sub/dir.sh")
        for bad in ("../etc/passwd", "/abs/path", "..", "a/./b", "a//b", "a\\b"):
            with self.assertRaises(ValueError, msg=bad):
                _safe_relpath(bad, "script_name")

    def test_script_name_cannot_escape_scripts_dir(self):
        conn = SSHConnection({"host": "example.test", "user": "deploy"})
        conn.connect = lambda: None
        with self.assertRaises(ValueError):
            conn.run_script("../../etc/cron.d/evil")

    def test_path_prefix_boundary_enforced(self):
        conn = SSHConnection({
            "host": "example.test", "user": "deploy",
            "allowed_remote_paths": ["/allowed/dir"],
        })
        self.assertFalse(conn._path_blocked("/allowed/dir", "remote"))
        self.assertFalse(conn._path_blocked("/allowed/dir/file.log", "remote"))
        self.assertTrue(conn._path_blocked("/allowed/dir-evil", "remote"))
        self.assertTrue(conn._path_blocked("/other", "remote"))

    def test_host_key_trusted_on_first_use_and_mismatch_rejected(self):
        conn = SSHConnection({"host": "example.test", "user": "deploy"})
        with tempfile.TemporaryDirectory() as tmp:
            kh = Path(tmp) / "known_hosts"
            with patch.object(ssh_client, "KNOWN_HOSTS_PATH", kh):
                key1 = paramiko.RSAKey.generate(1024)
                conn._check_or_record_host_key(key1)  # tofu: records
                self.assertTrue(kh.exists())
                conn._check_or_record_host_key(key1)  # same key accepted
                key2 = paramiko.RSAKey.generate(1024)
                with self.assertRaises(paramiko.SSHException):
                    conn._check_or_record_host_key(key2)  # mismatch -> MITM guard

    def test_strict_mode_rejects_unknown_host_key(self):
        conn = SSHConnection({
            "host": "example.test", "user": "deploy", "host_key_checking": "strict",
        })
        with tempfile.TemporaryDirectory() as tmp:
            kh = Path(tmp) / "known_hosts"
            with patch.object(ssh_client, "KNOWN_HOSTS_PATH", kh):
                with self.assertRaises(paramiko.SSHException):
                    conn._check_or_record_host_key(paramiko.RSAKey.generate(1024))

    def test_mcp_redacts_key_password_in_error_echo(self):
        text = mcp_server._tool_args_summary(
            "ssh_update_server", {"server": "s", "config": {"server": {"key_password": "hunter2"}}}
        )
        self.assertNotIn("hunter2", text)
        self.assertIn("***REDACTED***", text)


if __name__ == "__main__":
    unittest.main()
