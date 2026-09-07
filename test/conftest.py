"""Shared pytest fixtures for the ssh-alias-mcp test suite.

Offline unit tests must never require a live SSH server. Live end-to-end
tests are gated behind the SSH_TEST_SERVER environment variable and skip
themselves cleanly when it is unset (or the server is unreachable).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent.resolve()
TEST_DIR = Path(__file__).parent.resolve()
CLI = [sys.executable, str(SKILL_DIR / "cli.py")]

# Make the project root importable for direct imports (ssh_client, cli, ...).
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def _run_cli(*args, timeout=300, input=None):
    """Run cli.py and return (stdout, stderr, returncode).

    ``input`` is fed to the child's stdin (used by `run -` stdin tests).
    """
    r = subprocess.run(
        CLI + [str(a) for a in args],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(SKILL_DIR), encoding="utf-8", errors="replace",
        input=input,
    )
    return r.stdout, r.stderr, r.returncode


@pytest.fixture
def run_cli():
    """Function-scoped CLI runner: run_cli("server", "run", "ls") -> (out, err, code)."""
    return _run_cli


@pytest.fixture
def live_server():
    """Name of the live SSH test server, or skip the test when unavailable.

    Enable live runs with:  SSH_TEST_SERVER=<server-name> pytest test/ -m live
    """
    server = (os.environ.get("SSH_TEST_SERVER") or "").strip()
    if not server:
        pytest.skip("SSH_TEST_SERVER not set; live E2E tests skipped")
    # Fail fast (once per test) if the configured server is unknown.
    out, err, code = _run_cli("list-servers", timeout=60)
    try:
        servers = {s["name"] for s in json.loads(out)["servers"]}
    except Exception:
        pytest.skip(f"list-servers failed; live tests skipped: {err[:200]}")
    if server not in servers:
        pytest.skip(f"SSH_TEST_SERVER={server!r} not configured; live tests skipped")
    return server


@pytest.fixture
def probe_server(live_server):
    """Probe (user, home, scripts_dir, shell) of the live server once per test."""
    out, err, code = _run_cli("list-servers", timeout=60)
    shell = "bash"
    for s in json.loads(out)["servers"]:
        if s["name"] == live_server:
            shell = s.get("shell", "bash")
            break
    out, err, code = _run_cli(live_server, "run", "whoami", timeout=60)
    if code != 0:
        pytest.skip(f"live server {live_server} unreachable: {err[:200]}")
    user = out.strip()
    out2, _, code2 = _run_cli(live_server, "run", "cd && pwd", timeout=60)
    home = out2.strip() if code2 == 0 else f"/home/{user}"
    return {
        "server": live_server,
        "user": user,
        "home": home,
        "scripts_dir": f"{home}/scripts",
        "shell": shell,
    }
