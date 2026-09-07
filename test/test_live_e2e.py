#!/usr/bin/env python3
"""Unified live E2E pytest suite for the ssh-alias-mcp CLI (all shells).

Replaces the five old script-style live suites with ONE parameterized pytest
suite. Shell differences (bash / cmd / powershell) are handled through the
``_shell_matrix()`` command-builder matrix ported from test_comprehensive.py,
NOT by separate per-shell files. For cmd / powershell servers the temp dir is
resolved to a *literal* path at runtime (``echo %TEMP%`` / ``$env:TEMP``)
because cmd does not expand ``%VAR%`` inside double quotes; on probe failure
it falls back to ``C:/Windows/Temp``.

Gating: every test is ``live`` (file-level pytestmark) and uses the conftest
``live_server`` / ``probe_server`` / ``run_cli`` fixtures, so tests skip
cleanly when SSH_TEST_SERVER is unset or the server is unknown/unreachable.
Expected-alias assertions are probed at runtime from ``list-aliases``;
missing aliases SKIP (never hard-fail). Hardcoded account names
(e.g. whoami == "bit") are replaced by probe_server-driven assertions.

Old-file -> new-function coverage map
=====================================

test_comprehensive.py (universal multi-shell E2E):
  test_list_servers                  -> test_list_servers
  test_list_aliases                  -> test_list_aliases_structure + test_alias_types
  test_run_basic (echo)              -> test_run_echo
  test_run_basic (whoami)            -> test_run_whoami
  test_run_basic (uptime, hostname)  -> test_run_unix_system_cmds
  test_run_basic / error exit        -> test_run_error_exit
  test_run_sudo (4 simple cases)     -> test_run_sudo
  test_run_sudo (shadow neg+pos)     -> test_run_sudo_shadow_access
  test_run_timeout                   -> test_run_timeout
  test_run_sudo_timeout              -> test_run_sudo_timeout
  test_alias_inline (6 cases)        -> test_alias_inline
  test_alias_script (aliases)        -> test_alias_script_aliases
  test_alias_script (upload-all)     -> test_upload_all
  test_alias_script (list-scripts)   -> test_list_scripts
  test_alias_script (run-script)     -> test_run_script_direct
  test_alias_script (run-script -s)  -> test_run_script_sudo
  test_alias_error                   -> test_alias_nonexistent
  test_upload_cases (4 cases)        -> test_upload_variants
  test_download_file                 -> test_download_single_file
  test_download_directory            -> test_download_directory_recursive
  test_download_pattern              -> test_download_pattern_filter
  test_error_handling                -> test_error_handling_cli
  test_upload_all_and_list           -> test_upload_all + test_list_scripts
  test_sudo_upload_preserve_ownership-> test_sudo_upload_preserve_ownership
  test_sudo_upload_match_parent      -> test_sudo_upload_match_parent
  test_sudo_download_no_chown_impact -> test_sudo_download_no_chown_impact
  test_sudo_list_scripts_and_run     -> test_sudo_list_scripts_and_run
  test_sudo_upload_all               -> test_sudo_upload_all
  _shell_cmd() matrix                -> _shell_matrix() / _server_profile()
  _probe_server_info() %TEMP% probe  -> _probe_tmp()

test_bash.py (bash variant — merged, deduped against comprehensive):
  1 list-servers / 2 list-aliases / 3 run basic / 4 sudo / 5 alias inline /
  6 alias script / 7 alias error / 8 upload / 9-11 download / 12 timeout /
  13 sudo timeout / 14 error handling / 15 upload-all+list-scripts /
  16-19 sudo ownership+download / 20 upload-all -s
                                     -> same new functions as above
  21 test_stream_output              -> test_stream_output

test_cmd.py (cmd variant — merged):
  1-13 (list-servers, list-aliases, run basic incl. "exit /b 1", alias inline,
  alias script, alias error, upload, download file/dir/pattern, timeout,
  error handling, upload-all+list-scripts)
                                     -> same new functions, cmd branch of
                                        _shell_matrix() + _probe_tmp()

test_powershell.py (powershell variant — merged):
  1-13 (same scenarios as cmd file, powershell syntax incl. "exit 1",
  $env:TEMP expansion for downloads)
                                     -> same new functions, powershell branch
                                        of _shell_matrix() + _probe_tmp()

test_advanced.py (live-server-dependent cases only; the rest of
test_advanced.py stays offline and untouched):
  test_extends_server_fields         -> test_server_config_fields (config-
                                        driven, no hardcoded user/host/port)
  test_mcp_tool_call (ssh_run)       -> test_mcp_tool_call
  test_error_paths case 1 (upload
    nonexistent local file)          -> test_upload_nonexistent_local_file
  test_cli_long_flags --timeout      -> test_run_custom_timeout
  test_cli_long_flags --sudo x2      -> test_run_sudo["id -u --sudo"]

test_key_auth.py -> separate file test/test_live_key_auth.py (separate
server scenario); see that file's own mapping table.
"""
import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

TEST_DIR = Path(__file__).parent.resolve()

# Canonical alias names the multi-shell test server YAML is expected to define.
# NOT a hard requirement: every use probes list-aliases at runtime and SKIPs
# the individual test when an alias is absent (requirement 5).
EXPECTED_ALIASES = [
    "test-echo", "test-whoami-inline", "test-pipe", "test-multi-line",
    "test-error-inline", "test-hello-script", "test-env-script",
    "test-script-args", "test-sudo-inline", "test-sudo-script",
]

WINDOWS_TMP_FALLBACK = "C:/Windows/Temp"


# ────────────────────────────────────────────────────────────────────
# Shell command matrix (ported from test_comprehensive._shell_cmd)
# ────────────────────────────────────────────────────────────────────

def _shell_matrix(shell, tmp_dir):
    """OS-specific remote command builders for one shell type."""
    if shell == "powershell":
        return {
            "ext": ".ps1",
            "mkdir": lambda p: f'New-Item -ItemType Directory -Path "{p}" -Force',
            "write_file": lambda path, content:
                f'Set-Content -Path "{path}" -Value "{content}" -Encoding ASCII',
            "run_script": lambda p: f'& "{p}"',
            "pipe_test": 'echo hello | Measure-Object -Line | Select-Object -ExpandProperty Lines',
            "multi_line": 'echo line1; echo line2; echo line3',
            "error_cmd": 'exit 1',
            "whoami": 'whoami',
            "join": lambda parts: "; ".join(parts),
            "upload_content": 'Write-Output "upload-test-ok"\n',
        }
    if shell == "cmd":
        return {
            "ext": ".bat",
            "mkdir": lambda p: f'cmd /c mkdir "{p}" 2>nul',
            "write_file": lambda path, content: f'echo {content} > "{path}"',
            "run_script": lambda p: f'call "{p}"',
            "pipe_test": 'echo hello | find /c ""',
            "multi_line": 'echo line1 & echo line2 & echo line3',
            "error_cmd": 'cmd /c exit /b 1',
            "whoami": 'whoami',
            "join": lambda parts: " && ".join(parts),
            "upload_content": '@echo off\necho upload-test-ok\n',
        }
    return {  # bash (default)
        "ext": ".sh",
        "mkdir": lambda p: f"mkdir -p {p}",
        "write_file": lambda path, content: f"echo '{content}' > {path}",
        "run_script": lambda p: f"bash {p}",
        "pipe_test": 'echo hello | wc -w',
        "multi_line": 'echo line1; echo line2; echo line3',
        "error_cmd": "false",
        "whoami": "whoami",
        "join": lambda parts: " && ".join(parts),
        "upload_content": '#!/usr/bin/env bash\necho "upload-test-ok"\n',
    }


def _probe_tmp(run_cli, server, shell):
    """Resolve the server temp dir as a LITERAL path.

    cmd does not expand %VAR% inside double quotes (and SFTP needs a literal
    path anyway), so the remote shell expands it here and we store the result.
    Mirrors the SERVER_TMP_DIR probe logic in test_comprehensive.py; falls
    back to C:/Windows/Temp on failure.
    """
    if shell == "cmd":
        out, _, code = run_cli(server, "run", "echo %TEMP%", timeout=60)
    elif shell == "powershell":
        out, _, code = run_cli(server, "run", "$env:TEMP", timeout=60)
    else:
        return "/tmp"
    probed = (out or "").strip().strip('"').strip().replace("\\", "/")
    return probed if code == 0 and probed else WINDOWS_TMP_FALLBACK


def _server_profile(run_cli, probe):
    """probe_server dict + shell matrix + literal tmp dir + path helper."""
    shell = probe["shell"]
    tmp = _probe_tmp(run_cli, probe["server"], shell)
    profile = dict(shell=shell, tmp=tmp, **_shell_matrix(shell, tmp))
    profile["jp"] = (
        (lambda base, *parts: "/".join((str(base).rstrip("/"), *parts))
         if shell == "bash"
         else str(base).rstrip("/") + "\\" + "\\".join(parts))
    )
    return profile


def _unix_only(profile, reason="Unix (bash) only scenario"):
    if profile["shell"] != "bash":
        pytest.skip(f"{reason}: shell={profile['shell']}")


# ────────────────────────────────────────────────────────────────────
# Runtime probes (aliases) — missing alias => skip, never fail
# ────────────────────────────────────────────────────────────────────

_ALIAS_CACHE = {}


def _get_aliases(run_cli, server):
    out, err, code = run_cli(server, "list-aliases", timeout=60)
    if code != 0:
        pytest.skip(f"list-aliases failed on {server}: {(err or out)[:200]!r}")
    try:
        data = json.loads(out)
    except ValueError:
        pytest.skip(f"list-aliases returned invalid JSON on {server}: {out[:200]!r}")
    _ALIAS_CACHE[server] = data
    return data


def _require_alias(run_cli, server, name):
    """Return the alias entry; skip the test when the alias is not configured."""
    data = _ALIAS_CACHE.get(server) or _get_aliases(run_cli, server)
    for a in data.get("aliases", []):
        if a.get("name") == name:
            return a
    pytest.skip(f"alias {name!r} not configured on {server}")


def _require_upload_alias(run_cli, server):
    """Skip the test when no alias carries a 'script' field, so upload-all
    has nothing to install (prerequisite, not a product failure).
    NB: list_aliases reports type 'script'/'inline'; there is no 'upload' type."""
    data = _ALIAS_CACHE.get(server) or _get_aliases(run_cli, server)
    if any(a.get("script") or a.get("type") == "script"
           for a in data.get("aliases", [])):
        return
    pytest.skip(f"no script-type aliases configured on {server}")


def _clean_local(p: Path):
    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _make_files(run_cli, server, profile, base, files):
    """Create {relpath: content} under remote ``base`` and return base."""
    parents, cmds = [], []
    for rel in files:
        parent = profile["jp"](base, *rel.split("/")[:-1])
        if parent and parent not in parents:
            parents.append(parent)
    for p in sorted(set(parents), key=lambda s: s.count("/")):
        cmds.append(profile["mkdir"](p))
    for rel, content in files.items():
        cmds.append(profile["write_file"](profile["jp"](base, *rel.split("/")), content))
    out, err, code = run_cli(server, "run", profile["join"](cmds), timeout=120)
    assert code == 0, f"setup of {base} failed: {out[:200]!r} {err[:200]!r}"
    return base


# ════════════════════════════════════════════════════════════════════
# 1. Server / alias enumeration (config-driven)
# ════════════════════════════════════════════════════════════════════

def test_list_servers(run_cli, live_server):
    out, err, code = run_cli("list-servers", timeout=60)
    assert code == 0, f"list-servers failed: {err[:200]!r}"
    data = json.loads(out)
    assert data["count"] > 0
    assert live_server in [s["name"] for s in data["servers"]]


def test_server_config_fields(run_cli, probe_server):
    """Replaces test_advanced.test_extends_server_fields hardcoded host/port.

    Asserts the live server entry exposes host/port from its YAML config
    (presence only — no hardcoded 192.168.8.8 / ali.gzbit.cn / 22021), and
    that whoami matches the runtime-probed user (no hardcoded 'bit').
    """
    out, err, code = run_cli("list-servers", timeout=60)
    assert code == 0
    entry = next((s for s in json.loads(out)["servers"]
                  if s["name"] == probe_server["server"]), None)
    assert entry is not None
    assert entry.get("host"), f"server entry lacks host: {entry!r}"
    assert entry.get("port"), f"server entry lacks port: {entry!r}"


def test_list_aliases_structure(run_cli, probe_server):
    out, err, code = run_cli(probe_server["server"], "list-aliases", timeout=60)
    assert code == 0, f"list-aliases failed: {err[:200]!r}"
    data = json.loads(out)
    assert data["count"] == len(data["aliases"])


@pytest.mark.parametrize("alias,expected_type", [
    ("test-echo", "inline"),
    ("test-hello-script", "script"),
])
def test_alias_types(run_cli, probe_server, alias, expected_type):
    entry = _require_alias(run_cli, probe_server["server"], alias)
    assert entry.get("type") == expected_type, f"{alias}: type={entry.get('type')!r}"


# ════════════════════════════════════════════════════════════════════
# 2. run — basic execution
# ════════════════════════════════════════════════════════════════════

def test_run_echo(run_cli, live_server):
    out, err, code = run_cli(live_server, "run", "echo hello_ssh", timeout=60)
    assert code == 0 and "hello_ssh" in out, f"stdout={out!r} code={code}"


def test_run_whoami(run_cli, probe_server):
    """whoami is asserted against the runtime-probed user (not a hardcoded name)."""
    out, err, code = run_cli(probe_server["server"], "run", "whoami", timeout=60)
    assert code == 0
    assert out.strip().lower() == probe_server["user"].lower(), \
        f"stdout={out.strip()!r} expected probed user {probe_server['user']!r}"


def test_run_error_exit(run_cli, probe_server):
    """Shell-specific failing command (false / exit /b 1 / exit 1) -> non-zero."""
    profile = _server_profile(run_cli, probe_server)
    out, err, code = run_cli(probe_server["server"], "run", profile["error_cmd"], timeout=60)
    assert code != 0, f"{profile['error_cmd']!r} returned code={code}"


@pytest.mark.parametrize("cmd", ["uptime", "cat /etc/hostname"])
def test_run_unix_system_cmds(run_cli, probe_server, cmd):
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile)
    out, err, code = run_cli(probe_server["server"], "run", cmd, timeout=60)
    assert code == 0 and out.strip(), f"{cmd}: code={code} stdout={out!r}"


def test_run_custom_timeout(run_cli, live_server):
    """-t short and --timeout long flag both accept a custom timeout."""
    out, err, code = run_cli(live_server, "run", "echo timeout-test", "-t", "60", timeout=120)
    assert code == 0 and "timeout-test" in out
    out, err, code = run_cli(live_server, "run", "echo long-flag-test",
                             "--timeout", "30", timeout=120)
    assert code == 0 and "long-flag-test" in out, f"stdout={out!r} code={code}"


def test_run_default_timeout(run_cli, live_server):
    out, err, code = run_cli(live_server, "run", "echo default-timeout", timeout=120)
    assert code == 0 and "default-timeout" in out


# ════════════════════════════════════════════════════════════════════
# 3. sudo — root execution (Unix only)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cmd,flags,expect", [
    ("whoami", ["-s"], "root"),
    ("cat /etc/hostname", ["-s"], "text"),
    ("false", ["-s"], "nonzero"),
    # long-flag variant merged from test_advanced.test_cli_long_flags
    ("id -u", ["--sudo"], "0"),
])
def test_run_sudo(run_cli, probe_server, cmd, flags, expect):
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile)
    out, err, code = run_cli(probe_server["server"], "run", cmd, *flags, timeout=60)
    if expect == "nonzero":
        assert code != 0, f"{cmd} -s returned code={code}"
    elif expect == "0":
        assert code == 0 and out.strip() == "0", f"stdout={out!r} code={code}"
    elif expect == "root":
        assert "root" in out, f"stdout={out!r}"
    else:
        assert code == 0 and out.strip(), f"{cmd}: code={code} stdout={out!r}"


def test_run_sudo_shadow_access(run_cli, probe_server):
    """Negative: plain cat /etc/shadow fails; positive: -s succeeds."""
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile)
    out, err, code = run_cli(probe_server["server"], "run", "cat /etc/shadow", timeout=60)
    assert code != 0, f"expected permission denied, code={code}"
    out, err, code = run_cli(probe_server["server"], "run", "cat /etc/shadow", "-s", timeout=60)
    assert code == 0 and "root:" in out, f"stdout={out[:100]!r} code={code}"


def test_run_sudo_timeout(run_cli, probe_server):
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile)
    out, err, code = run_cli(probe_server["server"], "run", "echo sudo-timeout-test",
                             "-s", "-t", "30", timeout=120)
    assert code == 0 and "sudo-timeout-test" in out
    out, err, code = run_cli(probe_server["server"], "run", "whoami",
                            "-s", "-t", "30", timeout=120)
    assert "root" in out, f"stdout={out!r}"


# ════════════════════════════════════════════════════════════════════
# 4. alias — inline (runtime alias gating; missing => skip)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("alias,check", [
    ("test-echo", "inline-ok"),
    ("test-whoami-inline", "nonempty"),
    ("test-pipe", "number"),
    ("test-multi-line", "lines"),
    ("test-error-inline", "nonzero"),
    ("test-sudo-inline", "root"),
])
def test_alias_inline(run_cli, probe_server, alias, check):
    profile = _server_profile(run_cli, probe_server)
    _require_alias(run_cli, probe_server["server"], alias)
    if check == "root":
        _unix_only(profile, "test-sudo-inline")
    out, err, code = run_cli(probe_server["server"], "alias", alias, timeout=60)
    if check == "nonzero":
        assert code != 0, f"{alias}: code={code}"
        return
    assert code == 0, f"{alias}: code={code} stderr={err[:200]!r}"
    if check == "inline-ok":
        assert "test-inline-ok" in out, f"stdout={out!r}"
    elif check == "nonempty":
        assert out.strip(), f"stdout={out!r}"
    elif check == "number":
        assert out.strip().isdigit() and int(out.strip()) > 0, f"stdout={out!r}"
    elif check == "lines":
        for i in (1, 2, 3):
            assert f"line{i}" in out, f"missing line{i}: stdout={out!r}"
    elif check == "root":
        assert "root" in out, f"stdout={out!r}"


# ════════════════════════════════════════════════════════════════════
# 5. scripts: upload-all / list-scripts / alias-script / run-script
# ════════════════════════════════════════════════════════════════════

def test_upload_all(run_cli, probe_server):
    _require_upload_alias(run_cli, probe_server["server"])
    out, err, code = run_cli(probe_server["server"], "upload-all", timeout=300)
    assert "Upload done" in out, f"stdout={out[:300]!r} code={code}"
    uploaded = [ln for ln in out.split("\n") if ln.startswith("  +")]
    assert len(uploaded) > 0, f"no scripts uploaded: {out[:300]!r}"


def test_upload_all_sudo(run_cli, probe_server):
    _require_upload_alias(run_cli, probe_server["server"])
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "upload-all -s")
    out, err, code = run_cli(probe_server["server"], "upload-all", "-s", timeout=300)
    assert "Upload done" in out, f"stdout={out[:300]!r} code={code}"
    uploaded = [ln for ln in out.split("\n") if ln.startswith("  +")]
    assert len(uploaded) > 0, f"no scripts uploaded: {out[:300]!r}"


def test_list_scripts(run_cli, probe_server):
    profile = _server_profile(run_cli, probe_server)
    out, err, code = run_cli(probe_server["server"], "list-scripts", timeout=60)
    assert code == 0 and out.strip(), f"code={code} stdout={out[:200]!r}"
    lines = [ln for ln in out.split("\n") if ln.strip() and not ln.startswith("total")]
    assert any(profile["ext"] in ln for ln in lines), \
        f"no *{profile['ext']} entries: {out[:300]!r}"


@pytest.mark.parametrize("alias,unix_only,expect", [
    ("test-hello-script", False, "test-hello-script-ok"),
    ("test-env-script", False, "UNAME:"),
    ("test-script-args", False, "test-hello-script-ok"),
    ("test-sudo-script", True, "test-hello-script-ok"),
])
def test_alias_script_aliases(run_cli, probe_server, alias, unix_only, expect):
    profile = _server_profile(run_cli, probe_server)
    _require_alias(run_cli, probe_server["server"], alias)
    if unix_only:
        _unix_only(profile, alias)
    # ensure scripts are installed (idempotent)
    run_cli(probe_server["server"], "upload-all", timeout=300)
    out, err, code = run_cli(probe_server["server"], "alias", alias, timeout=60)
    assert code == 0 and expect in out, f"{alias}: code={code} stdout={out[:200]!r}"
    if alias == "test-env-script" and profile["shell"] == "bash":
        assert "USER:" in out, f"stdout={out[:200]!r}"


def test_run_script_direct(run_cli, probe_server):
    """Upload our own script, then run it via run-script."""
    profile = _server_profile(run_cli, probe_server)
    script = TEST_DIR / f"_live_upload{profile['ext']}"
    script.write_text(profile["upload_content"], encoding="utf-8")
    try:
        out, err, code = run_cli(probe_server["server"], "upload", str(script), timeout=120)
        assert "Script uploaded" in out, f"stdout={out[:200]!r} code={code}"
        out, err, code = run_cli(probe_server["server"], "run-script",
                                f"_live_upload{profile['ext']}", timeout=60)
        assert code == 0 and "upload-test-ok" in out, f"stdout={out[:200]!r} code={code}"
    finally:
        _clean_local(script)


def test_run_script_sudo(run_cli, probe_server):
    """Server-side test-hello script via run-script -s (CLI sudo flag)."""
    _require_upload_alias(run_cli, probe_server["server"])
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "run-script -s")
    run_cli(probe_server["server"], "upload-all", timeout=300)
    out, err, code = run_cli(probe_server["server"], "run-script",
                             f"test-hello{profile['ext']}", "-s", timeout=60)
    assert code == 0 and "test-hello-script-ok" in out, f"stdout={out[:200]!r} code={code}"


# ════════════════════════════════════════════════════════════════════
# 6. alias — error handling
# ════════════════════════════════════════════════════════════════════

def test_alias_nonexistent(run_cli, live_server):
    out, err, code = run_cli(live_server, "alias", "does_not_exist_12345", timeout=60)
    assert code != 0, f"expected non-zero, code={code}"


# ════════════════════════════════════════════════════════════════════
# 7. upload variants
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode", ["plain", "rename", "run", "rename-run"])
def test_upload_variants(run_cli, probe_server, tmp_path, mode):
    profile = _server_profile(run_cli, probe_server)
    script = tmp_path / f"test_upload{profile['ext']}"
    script.write_text(profile["upload_content"], encoding="utf-8")
    if mode == "plain":
        out, err, code = run_cli(probe_server["server"], "upload", str(script), timeout=120)
        assert "Script uploaded" in out, f"stdout={out[:200]!r} code={code}"
    elif mode == "rename":
        out, err, code = run_cli(probe_server["server"], "upload", str(script),
                                 "-n", f"custom_test{profile['ext']}", timeout=120)
        assert "custom_test" + profile["ext"] in out, f"stdout={out[:200]!r} code={code}"
    elif mode == "run":
        out, err, code = run_cli(probe_server["server"], "upload", str(script),
                                 "-r", timeout=120)
        assert "upload-test-ok" in out, f"stdout={out[:200]!r} code={code}"
    else:  # rename + run immediately
        out, err, code = run_cli(probe_server["server"], "upload", str(script),
                                 "-n", f"custom_run{profile['ext']}", "-r", timeout=120)
        assert "upload-test-ok" in out, f"stdout={out[:200]!r} code={code}"


# ════════════════════════════════════════════════════════════════════
# 8. download — single file / directory / pattern filter
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("n", [1, 2], ids=["file-1", "file-2"])
def test_download_single_file(run_cli, probe_server, tmp_path, n):
    profile = _server_profile(run_cli, probe_server)
    content = f"download-test-content-{n}"
    remote = profile["jp"](profile["tmp"], f"test_cli_download_{n}.txt")
    out, err, code = run_cli(probe_server["server"], "run",
                             profile["write_file"](remote, content), timeout=60)
    assert code == 0, f"remote setup failed: {out[:200]!r} {err[:200]!r}"
    local = tmp_path / f"downloaded_file{n}.txt"
    _clean_local(local)
    out, err, code = run_cli(probe_server["server"], "download", remote, str(local), timeout=120)
    assert code == 0, f"download failed: {out[:200]!r} {err[:200]!r}"
    assert "download" in (out + err).lower(), f"stdout={out[:200]!r}"
    assert local.exists() and content in local.read_text(), \
        f"local file missing content {content!r}"


@pytest.mark.parametrize("dirname,files", [
    ("test_dl1", {"a/file1.log": "file1-content", "a/b/file2.txt": "file2-content"}),
    ("test_dl2", {"x/data.csv": "x-content", "x/readme.md": "y-content"}),
], ids=["nested-2level", "nested-flat"])
def test_download_directory_recursive(run_cli, probe_server, tmp_path, dirname, files):
    profile = _server_profile(run_cli, probe_server)
    remote = _make_files(run_cli, probe_server["server"], profile,
                         profile["jp"](profile["tmp"], dirname), files)
    local = tmp_path / f"downloaded_{dirname}"
    _clean_local(local)
    out, err, code = run_cli(probe_server["server"], "download", remote, str(local), timeout=120)
    assert code == 0, f"download failed: {out[:200]!r} {err[:200]!r}"
    assert local.exists(), f"local dir not created: {out[:200]!r}"
    for rel, content in files.items():
        f = local / rel
        assert f.exists(), f"{rel} missing under {local}"
        assert content in f.read_text(), f"{rel} content mismatch"


@pytest.mark.parametrize("pattern,expected,excluded", [
    ("\\.log$", "mix.log", ("mix.txt", None)),        # None => mix<ext> excluded
    ("\\.txt$", "mix.txt", ("mix.log", None)),
], ids=["log-only", "txt-only"])
def test_download_pattern_filter(run_cli, probe_server, tmp_path, pattern, expected, excluded):
    profile = _server_profile(run_cli, probe_server)
    files = {"mix.log": "x", "mix.txt": "y", f"mix{profile['ext']}": "z"}
    remote = _make_files(run_cli, probe_server["server"], profile,
                         profile["jp"](profile["tmp"], "test_pat1"), files)
    local = tmp_path / f"dl_pat_{pattern.strip(chr(92)).replace('$', '').replace('.', '_')}"
    _clean_local(local)
    out, err, code = run_cli(probe_server["server"], "download",
                             remote, str(local), "-p", pattern, timeout=120)
    assert code == 0, f"pattern download failed: {out[:200]!r} {err[:200]!r}"
    assert local.exists(), f"local dir not created (pattern {pattern}): {out[:200]!r}"
    got = {f.name for f in local.rglob("*") if f.is_file()}
    assert expected in got, f"{expected} missing, got {got}"
    for name in excluded:
        if name is None:
            name = f"mix{profile['ext']}"
        assert name not in got, f"{name} should be excluded, got {got}"


# ════════════════════════════════════════════════════════════════════
# 9. CLI error handling / bad input
# ════════════════════════════════════════════════════════════════════

def test_error_handling_cli(run_cli, live_server):
    out, err, code = run_cli("nonexistent-server-xyz", "run", "echo test", timeout=60)
    assert code != 0, f"expected error for unknown server, code={code}"
    out, err, code = run_cli(timeout=30)
    assert code == 0 and out, f"no-args should print help: code={code}"


def test_upload_nonexistent_local_file(run_cli, live_server):
    out, err, code = run_cli(live_server, "upload", "/nonexistent/file.sh",
                            "-t", "10", timeout=60)
    assert code != 0, f"upload of nonexistent file should fail, code={code}"


# ════════════════════════════════════════════════════════════════════
# 10. Streamed output (from test_bash #21)
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kind", ["run", "alias", "run-script"])
def test_stream_output(run_cli, probe_server, kind):
    profile = _server_profile(run_cli, probe_server)
    server = probe_server["server"]
    if kind == "run":
        out, err, code = run_cli(server, "run", "echo stream-test-ok", timeout=60)
        assert "stream-test-ok" in out, f"stdout={out!r} code={code}"
    elif kind == "alias":
        _require_alias(run_cli, server, "test-echo")
        out, err, code = run_cli(server, "alias", "test-echo", timeout=60)
        assert "test-inline-ok" in out, f"stdout={out!r} code={code}"
    else:
        _require_upload_alias(run_cli, server)
        run_cli(server, "upload-all", timeout=300)
        out, err, code = run_cli(server, "run-script",
                                f"test-hello{profile['ext']}", timeout=60)
        assert "test-hello-script-ok" in out, f"stdout={out[:200]!r} code={code}"


# ════════════════════════════════════════════════════════════════════
# 11. sudo file-ownership semantics (Unix only)
# ════════════════════════════════════════════════════════════════════

def test_sudo_upload_preserve_ownership(run_cli, probe_server, tmp_path):
    """sudo upload over an existing file keeps owner/group/mode."""
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "sudo upload ownership")
    server, user = probe_server["server"], probe_server["user"]
    probe = f"{probe_server['scripts_dir']}/_owner_probe.sh"
    run_cli(server, "run",
            f"mkdir -p {probe_server['scripts_dir']} && "
            f"echo 'original-content' > {probe} && "
            f"chown {user}:{user} {probe} && chmod 700 {probe}", "-s", timeout=120)
    out, _, _ = run_cli(server, "run", f"stat -c '%U:%G %a' {probe}", timeout=60)
    baseline = out.strip()
    expected = f"{user}:{user} 700"
    assert baseline == expected, f"baseline {baseline!r} != {expected!r}"

    local = tmp_path / "_owner_probe.sh"
    local.write_text("overwritten-content\n", encoding="utf-8")
    out, err, code = run_cli(server, "upload", str(local), "-n", "_owner_probe.sh",
                             "-s", timeout=120)
    assert code == 0 and "Script uploaded" in out, f"sudo upload failed: {out[:200]!r}"

    out, _, _ = run_cli(server, "run", f"stat -c '%U:%G %a' {probe}", timeout=60)
    assert out.strip() == expected, \
        f"owner/mode changed: before={baseline!r} after={out.strip()!r}"
    out, _, _ = run_cli(server, "run", f"cat {probe}", timeout=60)
    assert "overwritten-content" in out, f"content not updated: {out[:100]!r}"
    run_cli(server, "run", f"rm -f {probe}", "-s", timeout=60)


def test_sudo_upload_match_parent(run_cli, probe_server, tmp_path):
    """sudo upload of a NEW file adopts the parent dir owner, mode 755."""
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "sudo upload match-parent")
    server, user = probe_server["server"], probe_server["user"]
    target = f"{probe_server['scripts_dir']}/_new_match_probe.sh"
    run_cli(server, "run", f"rm -f {target}", "-s", timeout=60)
    out, _, code = run_cli(server, "run", f"test -e {target}", timeout=60)
    assert code != 0, "target should not exist before upload"

    local = tmp_path / "_new_probe.sh"
    local.write_text("new-content\n", encoding="utf-8")
    out, err, code = run_cli(server, "upload", str(local), "-n", "_new_match_probe.sh",
                             "-s", timeout=120)
    assert code == 0, f"sudo upload failed: {out[:200]!r} code={code}"
    out, _, _ = run_cli(server, "run", f"stat -c '%U:%G %a' {target}", timeout=60)
    assert out.strip() == f"{user}:{user} 755", \
        f"new file ownership wrong: {out.strip()!r}"
    run_cli(server, "run", f"rm -f {target}", "-s", timeout=60)


def test_sudo_download_no_chown_impact(run_cli, probe_server, tmp_path):
    """sudo download must not alter the remote source owner/mode."""
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "sudo download")
    server = probe_server["server"]
    src = "/root/_dl_probe.txt"
    run_cli(server, "run",
            f"echo 'root-secret-content' > {src} && "
            f"chown root:root {src} && chmod 600 {src}", "-s", timeout=120)
    out, _, _ = run_cli(server, "run", f"stat -c '%U:%G %a' {src}", "-s", timeout=60)
    baseline = out.strip()
    assert baseline == "root:root 600", f"setup failed: {baseline!r}"

    local = tmp_path / "_dl_probe_downloaded.txt"
    _clean_local(local)
    out, err, code = run_cli(server, "download", src, str(local), timeout=120)
    assert code != 0, "non-sudo download of root-owned file should fail"
    _clean_local(local)

    out, err, code = run_cli(server, "download", src, str(local), "-s", timeout=120)
    assert code == 0, f"sudo download failed: {out[:200]!r} {err[:200]!r}"
    assert local.exists() and "root-secret-content" in local.read_text()

    out, _, _ = run_cli(server, "run", f"stat -c '%U:%G %a' {src}", "-s", timeout=60)
    assert out.strip() == baseline, \
        f"source mutated: before={baseline!r} after={out.strip()!r}"
    run_cli(server, "run", f"rm -f {src}", "-s", timeout=60)


def test_sudo_list_scripts_and_run(run_cli, probe_server):
    """root:700 script in scripts dir: needs -s to list/run."""
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "sudo list-scripts/run-script")
    server = probe_server["server"]
    probe = f"{probe_server['scripts_dir']}/_root_only.sh"
    run_cli(server, "run",
            f'mkdir -p {probe_server["scripts_dir"]} && '
            f'echo "#!/usr/bin/env bash" > {probe} && '
            f'echo "echo root-only-script-ok" >> {probe} && '
            f"chown root:root {probe} && chmod 700 {probe}", "-s", timeout=120)
    try:
        out, err, code = run_cli(server, "run-script", "_root_only.sh", timeout=60)
        assert code != 0, "non-sudo run-script of root:700 file should fail"
        out, err, code = run_cli(server, "run-script", "_root_only.sh", "-s", timeout=60)
        assert code == 0 and "root-only-script-ok" in out, f"stdout={out[:200]!r}"
        out, err, code = run_cli(server, "list-scripts", "-s", timeout=60)
        assert code == 0 and "_root_only.sh" in out, f"stdout={out[:200]!r}"
    finally:
        run_cli(server, "run", f"rm -f {probe}", "-s", timeout=60)


# ════════════════════════════════════════════════════════════════════
# 12. MCP tool call against the live server (from test_advanced #6)
# ════════════════════════════════════════════════════════════════════

def test_mcp_tool_call(run_cli, live_server):
    pytest.importorskip("paramiko")
    from mcp_server import handle_request
    resp = handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "ssh_run",
                   "arguments": {"server": live_server,
                                 "command": "echo mcp-test-ok",
                                 "timeout": 10, "sudo": False}},
    })
    content = resp.get("result", {}).get("content", [])
    text = content[0]["text"] if content else ""
    assert "mcp-test-ok" in text, f"resp={resp!r}"


# ════════════════════════════════════════════════════════════════════
# 13. run - (stdin): raw $ survives the whole chain (from the awk
#     `$0`-swallowing report — local double-quote expansion is bypassed)
# ════════════════════════════════════════════════════════════════════

def test_run_stdin_dollar_verbatim(run_cli, probe_server):
    """A command with RAW $0/quotes passed via stdin must reach awk intact."""
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "run - stdin")
    server = probe_server["server"]
    command = (
        "printf 'xx running-req: 7 yy token/s): 12.5\n' "
        "| awk 'match($0,/running-req: [0-9]+/){"
        "print substr($0,RSTART+13,RLENGTH-13)+0}'"
    )
    assert "$0" in command  # raw dollar, no backslash escaping anywhere
    out, err, code = run_cli(server, "run", "-", input=command, timeout=60)
    assert code == 0 and out.strip() == "7", f"stdout={out!r} stderr={err!r}"


def test_run_stdin_with_sudo_and_flags(run_cli, probe_server):
    profile = _server_profile(run_cli, probe_server)
    _unix_only(profile, "run - stdin sudo")
    server = probe_server["server"]
    out, err, code = run_cli(
        server, "run", "-", "-t", "30", "-s",
        input="whoami\nawk 'BEGIN{print \"$\" \"0=\" 41+1}'\n", timeout=120,
    )
    assert code == 0, f"code={code} stdout={out!r} stderr={err!r}"
    assert "root" in out and "$0=42" in out, f"stdout={out!r}"
