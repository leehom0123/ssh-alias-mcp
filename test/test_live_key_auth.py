#!/usr/bin/env python3
"""Live E2E tests for the SSH key-authentication server scenario.

Split out of test_key_auth.py (kept separate from test_live_e2e.py because
key auth targets its own server, e.g. SSH_TEST_SERVER=test-key-auth). All
tests are ``live`` (file-level pytestmark) and go through the conftest
``live_server`` / ``probe_server`` / ``run_cli`` fixtures, so they skip
cleanly when SSH_TEST_SERVER is unset or the server is not configured.

De-hardcoding:
  * whoami is asserted against the runtime-probed user (was: output must
    contain the literal account name "bit").
  * server host/port are asserted as configured-on-this-server properties
    read back from ``list-servers`` (was: hardcoded 192.168.8.8).
  * the optional ``test-key-echo`` alias is probed at runtime via
    ``list-aliases``; missing => pytest.skip instead of fail.

Old-file -> new-function coverage map (test_key_auth.py):
  test_key_auth_basic case 1 (whoami)     -> test_key_auth_whoami
  test_key_auth_basic case 2 (echo)       -> test_key_auth_echo
  test_key_auth_basic case 3 (list host)  -> test_key_auth_server_config
  test_key_auth_errors case 1 (fake run)  -> test_key_auth_fake_server_cli
  test_key_auth_errors case 2 (pool.get)  -> test_key_auth_pool_filenotfound
  test_key_auth_sudo cases 1-2            -> test_key_auth_sudo
  test_key_auth_alias cases 1-2           -> test_key_auth_alias_execution
  test_key_auth_upload_download cases 1-3 -> test_key_auth_upload_run_download
  test_key_auth_list_scripts cases 1-2    -> test_key_auth_list_scripts
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

TEST_DIR = Path(__file__).parent.resolve()

# Unique name guaranteed not to exist in the servers config, and valid under
# _config_target's name rules (leading underscore would raise ValueError
# before pool.get ever reaches the FileNotFoundError path).
FAKE_SERVER = "zz-no-such-key-server-zzz"


# ── 1. Basic key-auth connection (from test_key_auth_basic) ────────

def test_key_auth_whoami(run_cli, probe_server):
    """Key-auth connect + run; user checked against the probed value, not 'bit'."""
    out, err, code = run_cli(probe_server["server"], "run", "whoami",
                             "-t", "10", timeout=60)
    assert code == 0, f"key-auth run failed: {err[:200]!r} code={code}"
    assert out.strip().lower() == probe_server["user"].lower(), \
        f"stdout={out.strip()!r} expected probed user {probe_server['user']!r}"


def test_key_auth_echo(run_cli, live_server):
    out, err, code = run_cli(live_server, "run", "echo key-auth-test",
                             "-t", "10", timeout=60)
    assert code == 0 and "key-auth-test" in out, f"stdout={out!r} code={code}"


def test_key_auth_server_config(run_cli, probe_server):
    """Config-driven replacement for the hardcoded 192.168.8.8 assertion."""
    out, err, code = run_cli("list-servers", timeout=60)
    assert code == 0
    entry = next((s for s in json.loads(out)["servers"]
                  if s["name"] == probe_server["server"]), None)
    assert entry is not None, f"{probe_server['server']} not in list-servers"
    assert entry.get("host"), f"key-auth server entry lacks host: {entry!r}"
    assert entry.get("port"), f"key-auth server entry lacks port: {entry!r}"


# ── 2. Key-auth error handling (from test_key_auth_errors) ────────

def test_key_auth_fake_server_cli(run_cli, live_server):
    out, err, code = run_cli(FAKE_SERVER, "run", "echo test", "-t", "10", timeout=60)
    assert code != 0, f"unknown server config should error, code={code}"


def test_key_auth_pool_filenotfound(run_cli, live_server):
    from ssh_client import pool
    with pytest.raises(FileNotFoundError) as excinfo:
        pool.get(FAKE_SERVER)
    assert "Server config not found" in str(excinfo.value)


# ── 3. Key-auth + sudo (from test_key_auth_sudo; Linux servers only) ──

def test_key_auth_sudo(run_cli, probe_server):
    if probe_server["shell"] != "bash":
        pytest.skip(f"key-auth sudo scenario is bash-only (shell={probe_server['shell']})")
    out, err, code = run_cli(probe_server["server"], "run", "whoami",
                             "-s", "-t", "10", timeout=60)
    assert code == 0 and "root" in out, f"sudo whoami: stdout={out!r} code={code}"
    out, err, code = run_cli(probe_server["server"], "run", "cat /etc/hostname",
                             "-s", "-t", "10", timeout=60)
    assert code == 0 and out.strip(), f"stdout={out!r} code={code}"


# ── 4. Key-auth + alias execution (from test_key_auth_alias) ───────

def test_key_auth_alias_execution(run_cli, probe_server):
    server = probe_server["server"]
    out, err, code = run_cli(server, "list-aliases", timeout=60)
    if code != 0:
        pytest.skip(f"list-aliases failed: {err[:200]!r}")
    try:
        aliases = {a["name"] for a in json.loads(out)["aliases"]}
    except ValueError:
        pytest.skip(f"list-aliases invalid JSON: {out[:200]!r}")
    if "test-key-echo" not in aliases:
        pytest.skip("alias 'test-key-echo' not configured on this server")
    out, err, code = run_cli(server, "alias", "test-key-echo", timeout=60)
    assert code == 0 and "test-key-auth-ok" in out, f"stdout={out!r} code={code}"


# ── 5. Key-auth upload / run-script / download round-trip ─────────

def test_key_auth_upload_run_download(run_cli, probe_server, tmp_path):
    if probe_server["shell"] != "bash":
        pytest.skip(f"key-auth upload/download uses bash scripts (shell={probe_server['shell']})")
    server = probe_server["server"]
    script = tmp_path / "test_key_auth_upload.sh"
    script.write_text('#!/usr/bin/env bash\necho "key-upload-ok"\n', encoding="utf-8")

    out, err, code = run_cli(server, "upload", str(script), "-t", "10", timeout=120)
    assert "Script uploaded" in out, f"upload: stdout={out[:200]!r} code={code}"

    out, err, code = run_cli(server, "run-script", "test_key_auth_upload.sh",
                             "-t", "10", timeout=60)
    assert code == 0 and "key-upload-ok" in out, f"run-script: stdout={out[:200]!r}"

    out, err, code = run_cli(server, "run",
                             "echo 'key-download-test' > /tmp/test_key_dl.txt",
                             "-s", "-t", "10", timeout=60)
    assert code == 0, f"remote setup failed: {err[:200]!r}"
    local = TEST_DIR / "downloaded_key_test.txt"
    local.unlink(missing_ok=True)
    try:
        out, err, code = run_cli(server, "download", "/tmp/test_key_dl.txt",
                                 str(local), "-t", "10", timeout=120)
        assert code == 0 and "Downloaded" in out, f"download: stdout={out[:200]!r}"
        assert local.exists() and "key-download-test" in local.read_text()
    finally:
        local.unlink(missing_ok=True)
        run_cli(server, "run", "rm -f /tmp/test_key_dl.txt", "-s", "-t", "10", timeout=60)


# ── 6. Key-auth list-scripts (from test_key_auth_list_scripts) ────

def test_key_auth_list_scripts(run_cli, live_server):
    out, err, code = run_cli(live_server, "list-scripts", "-t", "10", timeout=60)
    assert code == 0 and out.strip(), f"list-scripts: code={code} stdout={out[:200]!r}"
    out, err, code = run_cli(live_server, "list-scripts", "-s", "-t", "10", timeout=60)
    assert code == 0, f"list-scripts -s: code={code} stderr={err[:200]!r}"
