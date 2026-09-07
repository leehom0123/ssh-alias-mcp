"""Security layer tests — whitelist, blacklist, command_template, sudo/alias checks.

End-to-end: every case runs real commands through cli.py against the
``security-test`` server (whitelist + blacklist + command_template + aliases)
or the unrestricted ``test-server``. Both require a reachable SSH host, so the
whole module is gated behind ``@pytest.mark.live`` and skips when
``SSH_TEST_SERVER`` is unset (or set to a different server).

Run live:  SSH_TEST_SERVER=security-test pytest test/test_security.py -m live
"""
import pytest

pytestmark = pytest.mark.live

SECURITY_SERVER = "security-test"
PLAIN_SERVER = "test-server"


@pytest.fixture
def security_server(live_server):
    """Require the live test to target the security-hardened server."""
    if live_server != SECURITY_SERVER:
        pytest.skip(
            f"security cases require SSH_TEST_SERVER={SECURITY_SERVER!r} "
            f"(got {live_server!r})"
        )
    return live_server


@pytest.fixture
def plain_server(live_server):
    """Require the live test to target the unrestricted server."""
    if live_server != PLAIN_SERVER:
        pytest.skip(
            f"empty-whitelist cases require SSH_TEST_SERVER={PLAIN_SERVER!r} "
            f"(got {live_server!r})"
        )
    return live_server


# ────────────────────────────────────────────────────────────────────
# 1. Blacklist — denylist
# ────────────────────────────────────────────────────────────────────

def test_blacklist_blocks_rm_rf(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "rm -rf /", "-t", "10")
    assert code != 0, f"rm -rf / should be blocked; code={code} stdout={out!r}"


def test_blacklist_rm_rf_stderr_message(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "rm -rf /", "-t", "10")
    assert "blocked by blacklist" in err, f"stderr={err.strip()!r}"


def test_blacklist_allows_echo(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "echo whitelist-ok", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip() == "whitelist-ok"


def test_blacklist_allowed_command_has_clean_stderr(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "echo whitelist-ok", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert err == "", f"stderr should be empty for allowed command; got {err.strip()!r}"


def test_blacklist_blocks_mkfs(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "mkfs.ext4 /dev/sda", "-t", "10")
    assert code != 0, f"mkfs.ext4 should be blocked; code={code}"


def test_blacklist_does_not_block_whoami(run_cli, security_server, probe_server):
    out, err, code = run_cli(security_server, "run", "whoami", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip().lower() == probe_server["user"].lower(), \
        f"whoami should return probed user; got {out.strip()!r}"


# ────────────────────────────────────────────────────────────────────
# 2. Whitelist — allowlist
# ────────────────────────────────────────────────────────────────────

def test_whitelist_allows_whoami(run_cli, security_server, probe_server):
    out, err, code = run_cli(security_server, "run", "whoami", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip().lower() == probe_server["user"].lower(), \
        f"whoami should return probed user; got {out.strip()!r}"


def test_whitelist_rejects_ls_root(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "ls /", "-t", "10")
    assert code != 0, f"ls / is not in whitelist; code={code} stdout={out!r}"


def test_whitelist_rejection_stderr_message(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "ls /", "-t", "10")
    assert "not in whitelist" in err, f"stderr={err.strip()!r}"


def test_whitelist_allows_uptime(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "uptime", "-t", "10")
    assert code == 0, f"uptime matches whitelist; code={code} stderr={err.strip()!r}"


# ────────────────────────────────────────────────────────────────────
# 3. Command Template — <command> placeholder
# ────────────────────────────────────────────────────────────────────

def test_command_template_wraps_pwd(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "pwd", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip() == "/tmp", f"template should cd /tmp first; got {out.strip()!r}"


def test_command_template_preserves_output(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "echo hello", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip() == "hello"


def test_command_template_whitelist_checks_original_command(run_cli, security_server):
    # The ORIGINAL command is checked against the whitelist before the
    # template wrapping, so "echo" (not "cd") must match the allowlist.
    out, err, code = run_cli(security_server, "run", "echo template-test", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip() == "template-test"


# ────────────────────────────────────────────────────────────────────
# 4. Alias execution also goes through _check_command
# ────────────────────────────────────────────────────────────────────

def test_alias_inline_passes_whitelist(run_cli, security_server):
    out, err, code = run_cli(security_server, "alias", "test-echo")
    assert code == 0, f"stderr={err.strip()!r}"
    assert "test-inline-ok" in out, f"stdout={out.strip()!r}"


def test_alias_blocked_by_whitelist(run_cli, security_server):
    # test-error-inline runs 'false' — 'false' is NOT in the whitelist.
    out, err, code = run_cli(security_server, "alias", "test-error-inline")
    assert code != 0, f"alias running 'false' should be blocked; code={code} stdout={out!r}"


def test_alias_rejection_stderr_message(run_cli, security_server):
    out, err, code = run_cli(security_server, "alias", "test-error-inline")
    assert "not in whitelist" in err, f"stderr={err.strip()!r}"


# ────────────────────────────────────────────────────────────────────
# 5. Sudo execution also goes through _check_command
# ────────────────────────────────────────────────────────────────────

def test_sudo_whoami_passes_whitelist(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "whoami", "-s", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert "root" in out, f"sudo whoami should return root; got {out.strip()!r}"


def test_sudo_ls_root_blocked_by_whitelist(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "ls /", "-s", "-t", "10")
    assert code != 0, f"sudo ls / should be blocked; code={code} stdout={out!r}"


# ────────────────────────────────────────────────────────────────────
# 6. Multiple patterns & edge cases
# ────────────────────────────────────────────────────────────────────

def test_blacklist_blocks_dd(run_cli, security_server):
    out, err, code = run_cli(
        security_server, "run", "dd if=/dev/zero of=/dev/null", "-t", "10"
    )
    assert code != 0, f"dd should be blocked by blacklist; code={code}"


def test_whitelist_allows_cat(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "cat /etc/hostname", "-t", "10")
    assert code == 0, f"cat matches whitelist; code={code} stderr={err.strip()!r}"


def test_quoted_command_does_not_crash_regex(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "echo 'hello world'", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert out.strip() == "hello world"


# ────────────────────────────────────────────────────────────────────
# 7. Sudo — password configured → sudo works (security-test has sudo_password)
# ────────────────────────────────────────────────────────────────────

def test_sudo_works_when_password_configured(run_cli, security_server):
    out, err, code = run_cli(security_server, "run", "whoami", "-s", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert "root" in out, f"sudo whoami should return root; got {out.strip()!r}"


# ────────────────────────────────────────────────────────────────────
# 8. Empty whitelist — no restriction (test-server)
# ────────────────────────────────────────────────────────────────────

def test_empty_whitelist_allows_ls(run_cli, plain_server):
    # test-server has no whitelist/blacklist, so a command that is blocked
    # on security-test must pass here.
    out, err, code = run_cli(plain_server, "run", "ls /", "-t", "10")
    assert code == 0, f"ls / should pass when whitelist is empty; code={code} stderr={err.strip()!r}"


def test_empty_whitelist_ls_returns_output(run_cli, plain_server):
    out, err, code = run_cli(plain_server, "run", "ls /", "-t", "10")
    assert code == 0, f"stderr={err.strip()!r}"
    assert len(out.strip()) > 0, "ls / should return non-empty output"
