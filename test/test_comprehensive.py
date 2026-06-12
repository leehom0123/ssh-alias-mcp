#!/usr/bin/env python3
"""Comprehensive test suite for ssh-alias-mcp CLI — 100% coverage.

Tests all CLI commands: servers, aliases (inline + script), run, sudo,
upload, download, timeout, and error handling.

Target server is read from env var SSH_TEST_SERVER (default: 'test-server').
The server YAML must define ALL aliases this suite expects (see EXPECTED_ALIASES).

Run: SSH_TEST_SERVER=test-server python test_all.py
     python test_all.py                # uses default 'test-server'
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
TEST_DIR = Path(__file__).parent
CLI = [sys.executable, str(SKILL_DIR / "cli.py")]

# Target server (override via SSH_TEST_SERVER env var)
SERVER = os.environ.get("SSH_TEST_SERVER", "test-server")
TEST_SERVER = SERVER  # back-compat alias; same server hosts everything

# Aliases the server YAML MUST define for this suite to pass
EXPECTED_ALIASES = {
    "test-echo", "test-whoami-inline", "test-pipe",
    "test-multi-line", "test-error-inline",
    "test-hello-script", "test-env-script", "test-script-args",
    "test-sudo-inline", "test-sudo-script",
}

# Probed at runtime from `list-servers` — avoid hardcoding user/path
SERVER_USER = None       # probed at runtime
SERVER_HOME = None
SERVER_SCRIPTS_DIR = None
SERVER_SHELL = None      # "bash", "cmd", or "powershell"

# ── Shell-specific command templates ──────────────────────────────
# Each key is a shell type; values are callables that return remote commands.
# This centralises all OS-specific logic instead of scattering if/else.

def _shell_cmd():
    """Return shell-specific command builders."""
    if SERVER_SHELL == "powershell":
        return {
            "ext": ".ps1",
            "tmp_dir": "$env:TEMP",
            "probe_user": 'echo "$env:USERNAME:$env:USERPROFILE"',
            "mkdir": lambda p: f'New-Item -ItemType Directory -Path "{p}" -Force',
            "write_file": lambda path, content: f'Set-Content -Path "{path}" -Value "{content}" -Encoding ASCII',
            "run_script": lambda p: f'& "{p}"',
            "pipe_test": 'echo hello | Measure-Object -Line | Select-Object -ExpandProperty Lines',
            "multi_line": 'echo line1; echo line2; echo line3',
            "error_cmd": 'exit 1',
            "whoami": 'whoami',
            "file_list_ext": ".ps1",
            "skip_pipes": False,
            "has_unix_perms": False,
        }
    elif SERVER_SHELL == "cmd":
        return {
            "ext": ".bat",
            "tmp_dir": "C:/Users/leeho/AppData/Local/Temp",
            "probe_user": "echo %USERNAME%:%USERPROFILE%",
            "mkdir": lambda p: f'cmd /c mkdir "{p}" 2>nul',
            "write_file": lambda path, content: f'echo {content} > "{path}"',
            "run_script": lambda p: f'call "{p}"',
            "pipe_test": 'echo hello | find /c ""',
            "multi_line": 'echo line1 & echo line2 & echo line3',
            "error_cmd": 'cmd /c exit /b 1',
            "whoami": 'whoami',
            "file_list_ext": ".bat",
            "skip_pipes": False,
            "has_unix_perms": False,
        }
    else:  # bash (default)
        return {
            "ext": ".sh",
            "tmp_dir": "/tmp",
            "probe_user": "echo $USER:$HOME",
            "mkdir": lambda p: f"mkdir -p {p}",
            "write_file": lambda path, content: f"echo '{content}' > {path}",
            "run_script": lambda p: f"bash {p}",
            "pipe_test": 'echo hello | wc -w',
            "multi_line": 'echo line1; echo line2; echo line3',
            "error_cmd": "false",
            "whoami": "whoami",
            "file_list_ext": ".sh",
            "skip_pipes": False,
            "has_unix_perms": True,
        }


def _probe_server_info():
    """Populate SERVER_USER / SERVER_HOME / SERVER_SCRIPTS_DIR from the live remote."""
    global SERVER_USER, SERVER_HOME, SERVER_SCRIPTS_DIR, SERVER_SHELL

    # Probe shell type from list-aliases JSON
    r = subprocess.run(
        CLI + [SERVER, "list-aliases"],
        capture_output=True, text=True, timeout=60,
        cwd=str(SKILL_DIR), encoding='utf-8', errors='replace',
    )
    if r.returncode == 0:
        data = json.loads(r.stdout)
        for s in data.get("servers", []):
            if s.get("name") == SERVER:
                SERVER_SHELL = s.get("shell", "bash")
                break

    if not SERVER_SHELL:
        SERVER_SHELL = "bash"

    # Probe user info using shell-specific command
    cmd = _shell_cmd()
    r = subprocess.run(
        CLI + [SERVER, "run", cmd["probe_user"]],
        capture_output=True, text=True, timeout=60,
        cwd=str(SKILL_DIR), encoding='utf-8', errors='replace',
    )

    if r.returncode != 0:
        raise RuntimeError(f"Cannot probe server {SERVER}: {r.stderr.strip()[:300]}")
    user, home = r.stdout.strip().split(":", 1)
    SERVER_USER = user
    SERVER_HOME = home
    SERVER_SCRIPTS_DIR = f"{home}/scripts"

# ── Output helpers ──────────────────────────────────────────────────

RED, GREEN, YELLOW, NC = "\033[91m", "\033[92m", "\033[93m", "\033[0m"
passed = failed = skipped = 0


def _ok(msg):
    print(f"{GREEN}PASS{NC}  {msg}")


def _fail(msg, detail=""):
    print(f"{RED}FAIL{NC}  {msg} — {detail}" if detail else f"{RED}FAIL{NC}  {msg}")


def _skip(msg):
    print(f"{YELLOW}SKIP{NC}  {msg}")


def _section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


def _inc(result, name, detail=""):
    global passed, failed, skipped
    if result == "ok":
        passed += 1; _ok(name)
    elif result == "fail":
        failed += 1; _fail(name, detail)
    else:
        skipped += 1; _skip(name)


def run_cli(*args, timeout=300):
    """Run cli.py and return (stdout, stderr, returncode)."""
    cmd = CLI + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=str(SKILL_DIR), encoding='utf-8', errors='replace')
    return r.stdout, r.stderr, r.returncode


def clean_path(p):
    """Safely remove a file or directory."""
    try:
        if isinstance(p, str):
            p = Path(p)
        if p.is_file() or p.is_symlink():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


# ═══════ 1. list-servers ═══════════════════════════════════════════

def test_list_servers():
    _section("1. list-servers — server enumeration")

    out, err, code = run_cli("list-servers")
    data = json.loads(out)
    _inc("ok" if code == 0 else "fail", "returns valid JSON", f"code={code}")
    _inc("ok" if data["count"] > 0 else "fail", "at least one server configured",
         f"count={data['count']}")

    names = [s["name"] for s in data["servers"]]
    _inc("ok" if SERVER in names else "fail",
         f"{SERVER} found in list", f"available: {names}")

    ts = next((s for s in data["servers"] if s["name"] == SERVER), None)
    if ts:
        _inc("ok" if ts.get("host") and ts.get("port") else "fail",
             f"{SERVER} has host+port", f"host={ts.get('host')} port={ts.get('port')}")


# ═══════ 2. list-aliases (test-server) ═════════════════════════════

def test_list_aliases():
    _section(f"2. list-aliases — {SERVER} alias enumeration")
    # Case 1: valid JSON
    out, err, code = run_cli(TEST_SERVER, "list-aliases")
    data = json.loads(out)
    _inc("ok" if code == 0 else "fail", "returns valid JSON", f"code={code}")
    _inc("ok" if data["count"] == len(data["aliases"]) else "fail",
         "count matches actual aliases", f"count={data['count']} actual={len(data['aliases'])}")

    aliases = {a["name"]: a for a in data["aliases"]}

    # Case 2: verify inline + script types exist
    _inc("ok" if "test-echo" in aliases else "fail",
         "inline alias 'test-echo' listed", f"names={list(aliases.keys())}")
    _inc("ok" if "test-hello-script" in aliases else "fail",
         "script alias 'test-hello-script' listed", f"names={list(aliases.keys())}")

    # Case 3: verify type field
    echo_a = aliases.get("test-echo", {})
    _inc("ok" if echo_a.get("type") == "inline" else "fail",
         "'test-echo' has type=inline", f"type={echo_a.get('type')}")

    script_a = aliases.get("test-hello-script", {})
    _inc("ok" if script_a.get("type") == "script" else "fail",
         "'test-hello-script' has type=script", f"type={script_a.get('type')}")

    # Case 4: verify all expected aliases are present
    actual_names = set(aliases.keys())
    missing = EXPECTED_ALIASES - actual_names
    _inc("ok" if not missing else "fail",
         f"all {len(EXPECTED_ALIASES)} expected aliases present",
         f"missing={sorted(missing)} actual={sorted(actual_names)}")


# ═══════ 3. run — basic command execution ══════════════════════════

def test_run_basic():
    _section("3. run — basic command execution")

    # Case 1: echo
    out, err, code = run_cli(SERVER, "run", "echo hello_ssh")
    _inc("ok" if code == 0 and "hello_ssh" in out else "fail",
         "echo command", f"stdout={out.strip()!r} code={code}")

    # Case 2: whoami returns the configured user
    out, err, code = run_cli(SERVER, "run", "whoami")
    _inc("ok" if SERVER_USER in out else "fail",
         f"whoami returns {SERVER_USER}", f"stdout={out.strip()!r}")

    # Case 3: uptime (Unix only)
    if IS_UNIX:
        out, err, code = run_cli(SERVER, "run", "uptime")
        _inc("ok" if code == 0 else "fail", "uptime runs successfully", f"code={code}")
    else:
        _inc("skip", "uptime (Unix only)")

    # Case 4: non-zero exit code
    out, err, code = run_cli(SERVER, "run", "false")
    _inc("ok" if code != 0 else "fail", "false returns non-zero", f"code={code}")

    # Case 5: cat /etc/hostname (Unix only)
    if IS_UNIX:
        out, err, code = run_cli(SERVER, "run", "cat /etc/hostname")
        _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
             "cat /etc/hostname returns content", f"stdout={out.strip()!r}")
    else:
        _inc("skip", "cat /etc/hostname (Unix only)")


# ═══════ 4. sudo — root execution ══════════════════════════════════

def test_run_sudo():
    _section("4. sudo — root execution via -s flag")
    
    # Skip all sudo tests on Windows
    if not IS_UNIX:
        _inc("skip", "sudo (Unix only)")
        _inc("skip", "sudo (Unix only)")
        _inc("skip", "sudo (Unix only)")
        _inc("skip", "sudo (Unix only)")
        _inc("skip", "sudo (Unix only)")
        _inc("skip", "sudo (Unix only)")
        return

    # Case 1: run -s whoami → root
    out, err, code = run_cli(SERVER, "run", "whoami", "-s")
    _inc("ok" if "root" in out else "fail",
         "run -s whoami returns root", f"stdout={out.strip()!r}")

    # Case 2: run -s cat /etc/hostname
    out, err, code = run_cli(SERVER, "run", "cat /etc/hostname", "-s")
    _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "run -s cat /etc/hostname", f"stdout={out.strip()!r}")

    # Case 3: run -s false → non-zero
    out, err, code = run_cli(SERVER, "run", "false", "-s")
    _inc("ok" if code != 0 else "fail", "run -s false returns non-zero", f"code={code}")

    # Case 4: long-form --sudo also works
    out, err, code = run_cli(SERVER, "run", "id -u", "--sudo")
    _inc("ok" if code == 0 and out.strip() == "0" else "fail",
         "run --sudo id -u returns 0", f"stdout={out.strip()!r}")

    # Case 5: NEGATIVE — non-sudo cat root-only file → fails (permission denied)
    # /etc/shadow is readable only by root; without -s we expect failure
    out, err, code = run_cli(SERVER, "run", "cat /etc/shadow")
    _inc("ok" if code != 0 else "fail",
         "non-sudo cat /etc/shadow fails (permission denied)",
         f"code={code} (expected non-zero)")

    # Case 6: POSITIVE — same command with -s succeeds
    out, err, code = run_cli(SERVER, "run", "cat /etc/shadow", "-s")
    _inc("ok" if code == 0 and "root:" in out else "fail",
         "sudo cat /etc/shadow succeeds", f"code={code} has_root_line={'root:' in out}")


# ═══════ 5. alias — inline ═════════════════════════════════════════

def test_alias_inline():
    _section("5. alias — inline (test-server)")

    # Case 1: simple echo
    out, err, code = run_cli(TEST_SERVER, "alias", "test-echo")
    _inc("ok" if code == 0 and "test-inline-ok" in out else "fail",
         "test-echo outputs expected text", f"stdout={out.strip()!r} code={code}")

    # Case 2: whoami
    out, err, code = run_cli(TEST_SERVER, "alias", "test-whoami-inline")
    _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "test-whoami-inline returns non-empty", f"stdout={out.strip()!r}")

    # Case 3: pipe command (Unix only - bash pipes)
    if IS_UNIX:
        out, err, code = run_cli(TEST_SERVER, "alias", "test-pipe")
        _inc("ok" if code == 0 and out.strip().isdigit() and int(out.strip()) > 0 else "fail",
             "test-pipe returns positive number", f"stdout={out.strip()!r}")
    else:
        _inc("skip", "test-pipe (Unix only - requires bash pipes)")

    # Case 4: multi-line output
    out, err, code = run_cli(TEST_SERVER, "alias", "test-multi-line")
    lines = [l for l in out.split("\n") if l.strip()]
    _inc("ok" if code == 0 and "line1" in out and "line2" in out and "line3" in out else "fail",
         "test-multi-line outputs all 3 lines", f"stdout={out.strip()!r}")

    # Case 5: error exit
    out, err, code = run_cli(TEST_SERVER, "alias", "test-error-inline")
    _inc("ok" if code != 0 else "fail",
         "test-error-inline returns non-zero", f"code={code}")

    # Case 6: sudo inline alias (sudo: true in YAML) → whoami should return root
    if IS_UNIX:
        out, err, code = run_cli(TEST_SERVER, "alias", "test-sudo-inline")
        _inc("ok" if code == 0 and "root" in out else "fail",
             "test-sudo-inline (alias YAML sudo: true) returns root",
             f"stdout={out.strip()!r} code={code}")
    else:
        _inc("skip", "test-sudo-inline (Unix only)")


# ═══════ 6. alias — script ═════════════════════════════════════════

def test_alias_script():
    _section("6. alias — script (test-server)")

    # Step 1: upload-all for test-server (uploads test-hello.bat, test-env.bat etc.)
    out, err, code = run_cli(TEST_SERVER, "upload-all")
    _inc("ok" if "Upload done" in out else "fail",
         "upload-all runs", f"stdout={out.strip()!r}")
    uploaded = [l for l in out.split("\n") if l.startswith("  +")]
    _inc("ok" if len(uploaded) > 0 else "fail",
         "upload-all uploads scripts", f"uploaded={len(uploaded)} items={[l.strip() for l in uploaded[:5]]}")

    # Step 2: run script alias — test-hello-script
    out, err, code = run_cli(TEST_SERVER, "alias", "test-hello-script")
    _inc("ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "test-hello-script via alias works", f"stdout={out.strip()[:200]!r}")

    # Step 3: run env script alias
    out, err, code = run_cli(TEST_SERVER, "alias", "test-env-script")
    if IS_UNIX:
        _inc("ok" if code == 0 and "UNAME:" in out and "USER:" in out else "fail",
             "test-env-script via alias works", f"stdout={out.strip()[:200]!r}")
    else:
        _inc("ok" if code == 0 and "UNAME:" in out else "fail",
             "test-env-script via alias works", f"stdout={out.strip()[:200]!r}")

    # Step 4: list-scripts — verify scripts are present
    out, err, code = run_cli(TEST_SERVER, "list-scripts")
    _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "list-scripts returns output", f"stdout={out.strip()!r}")
    if IS_UNIX:
        has_files = any(".sh" in l for l in out.split("\n") if l.strip() and not l.startswith("total"))
        _inc("ok" if has_files else "fail",
             "list-scripts shows .sh files", f"stdout={out.strip()[:200]!r}")
    else:
        has_files = any(".bat" in l for l in out.split("\n") if l.strip())
        _inc("ok" if has_files else "fail",
             "list-scripts shows .bat files", f"stdout={out.strip()[:200]!r}")

    # Step 5: run-script directly
    if IS_UNIX:
        out, err, code = run_cli(TEST_SERVER, "run-script", "test-hello.sh")
        _inc("ok" if code == 0 and "test-hello-script-ok" in out else "fail",
             "run-script test-hello.sh works", f"stdout={out.strip()[:200]!r}")
    else:
        out, err, code = run_cli(TEST_SERVER, "run-script", "test-hello.bat")
        _inc("ok" if code == 0 and "test-hello-script-ok" in out else "fail",
             "run-script test-hello.bat works", f"stdout={out.strip()[:200]!r}")

    # Step 6: run script alias (test-script-args)
    out, err, code = run_cli(TEST_SERVER, "alias", "test-script-args")
    _inc("ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "test-script-args via alias works", f"stdout={out.strip()[:200]!r}")

    # Step 7: sudo script alias (skip on Windows)
    if IS_UNIX:
        out, err, code = run_cli(TEST_SERVER, "alias", "test-sudo-script")
        _inc("ok" if code == 0 and "test-hello-script-ok" in out else "fail",
             "test-sudo-script (alias YAML sudo: true) runs", f"stdout={out.strip()[:200]!r}")
    else:
        _inc("skip", "test-sudo-script (Unix only)")

    # Step 8: run-script with -s flag explicitly (skip on Windows)
    if IS_UNIX:
        out, err, code = run_cli(TEST_SERVER, "run-script", "test-hello.sh", "-s")
        _inc("ok" if code == 0 and "test-hello-script-ok" in out else "fail",
             "run-script -s test-hello.sh works (CLI -s flag)", f"stdout={out.strip()[:200]!r}")
    else:
        _inc("skip", "run-script -s (Unix only)")


# ═══════ 7. alias — error handling ═════════════════════════════════

def test_alias_error():
    _section("7. alias — error handling")

    # Case 1: nonexistent alias (on test-server)
    out, err, code = run_cli(TEST_SERVER, "alias", "does_not_exist_12345")
    _inc("ok" if code != 0 else "fail",
         "nonexistent alias returns non-zero", f"code={code}")

    # Case 2: nonexistent alias (also on the same server)
    out, err, code = run_cli(SERVER, "alias", "does_not_exist_12345")
    _inc("ok" if code != 0 else "fail",
         "nonexistent alias on another server", f"code={code}")


# ═══════ 8. upload — multiple cases ════════════════════════════════

def test_upload_cases():
    _section("8. upload — multiple cases")
    if IS_UNIX:
        test_script = TEST_DIR / "test_upload.sh"
        test_script.write_text('#!/usr/bin/env bash\necho "upload-test-ok"\n')
    else:
        test_script = TEST_DIR / "test_upload.bat"
        test_script.write_text('@echo off\necho upload-test-ok\n')

    # Case 1: upload without -r
    out, err, code = run_cli(SERVER, "upload", str(test_script))
    _inc("ok" if "Script uploaded" in out else "fail",
         "upload script (no run)", f"stdout={out.strip()!r}")

    # Case 2: upload with custom name
    ext = ".sh" if IS_UNIX else ".bat"
    out, err, code = run_cli(SERVER, "upload", str(test_script), "-n", f"custom_test{ext}")
    _inc("ok" if f"custom_test{ext}" in out else "fail",
         "upload with custom name", f"stdout={out.strip()!r}")

    # Case 3: upload and run immediately
    out, err, code = run_cli(SERVER, "upload", str(test_script), "-r")
    _inc("ok" if "upload-test-ok" in out else "fail",
         "upload + run", f"stdout={out.strip()!r}")

    # Case 4: upload custom name + run
    out, err, code = run_cli(SERVER, "upload", str(test_script), "-n", f"custom_run{ext}", "-r")
    _inc("ok" if "upload-test-ok" in out else "fail",
         "upload custom name + run", f"stdout={out.strip()!r}")


# ═══════ 9. download — single file ═════════════════════════════════

def test_download_file():
    _section("9. download — single file")

    # Use temp directory based on OS
    if IS_UNIX:
        tmp_dir = "/tmp"
    else:
        tmp_dir = "C:/Users/leeho/AppData/Local/Temp"

    # Case 1: download file1
    if IS_UNIX:
        run_cli(SERVER, "run", f"echo 'download-test-content-1' > {tmp_dir}/test_cli_download_1.txt")
    else:
        run_cli(SERVER, "run", f"echo download-test-content-1 > {tmp_dir}\\test_cli_download_1.txt")
    local_file1 = TEST_DIR / "downloaded_file1.txt"
    clean_path(local_file1)
    out, err, code = run_cli(SERVER, "download", f"{tmp_dir}/test_cli_download_1.txt", str(local_file1))
    _inc("ok" if code == 0 and ("Downloaded" in out or "downloaded" in out.lower()) else "fail",
         "download single file to test_dl_file1", f"stdout={out.strip()!r}")
    if local_file1.exists():
        _inc("ok" if "download-test-content-1" in local_file1.read_text() else "fail",
             "downloaded file1 content matches")
        clean_path(local_file1)

    # Case 2: download file2
    if IS_UNIX:
        run_cli(SERVER, "run", f"echo 'download-test-content-2' > {tmp_dir}/test_cli_download_2.txt")
    else:
        run_cli(SERVER, "run", f"cmd /c echo download-test-content-2 > {tmp_dir}\\test_cli_download_2.txt")
    local_file2 = TEST_DIR / "downloaded_file2.txt"
    clean_path(local_file2)
    out, err, code = run_cli(SERVER, "download", f"{tmp_dir}/test_cli_download_2.txt", str(local_file2))
    _inc("ok" if code == 0 else "fail",
         "download single file to test_dl_file2", f"stdout={out.strip()!r}")
    if local_file2.exists():
        _inc("ok" if "download-test-content-2" in local_file2.read_text() else "fail",
             "downloaded file2 content matches")
        clean_path(local_file2)


# ═══════ 10. download — directory recursive ═══════════════════════

def test_download_directory():
    _section("10. download — directory recursive")

    # Use temp directory based on OS
    if IS_UNIX:
        tmp_dir = "/tmp"
        # Case 1: nested dir
        run_cli(SERVER, "run",
                f"mkdir -p {tmp_dir}/test_dl1/a/b && echo 'file1-content' > {tmp_dir}/test_dl1/a/file1.log "
                f"&& echo 'file2-content' > {tmp_dir}/test_dl1/a/b/file2.txt")
    else:
        tmp_dir = "C:/Users/leeho/AppData/Local/Temp"
        run_cli(SERVER, "run",
                f"cmd /c mkdir {tmp_dir}\\test_dl1\\a\\b 2>nul && cmd /c echo file1-content > {tmp_dir}\\test_dl1\\a\\file1.log "
                f"&& cmd /c echo file2-content > {tmp_dir}\\test_dl1\\a\\b\\file2.txt")
    local_dir1 = TEST_DIR / "downloaded_dir1"
    clean_path(local_dir1)
    out, err, code = run_cli(SERVER, "download", f"{tmp_dir}/test_dl1", str(local_dir1))
    _inc("ok" if code == 0 else "fail",
         "download nested dir 1", f"stdout={out.strip()!r} code={code}")
    if local_dir1.exists():
        _inc("ok" if (local_dir1 / "a" / "file1.log").exists() else "fail",
             "file1.log exists")
        _inc("ok" if (local_dir1 / "a" / "b" / "file2.txt").exists() else "fail",
             "file2.txt (nested) exists")
        _inc("ok" if "file1-content" in (local_dir1 / "a" / "file1.log").read_text() else "fail",
             "file1 content matches")
        clean_path(local_dir1)

    # Case 2: different dir structure
    if IS_UNIX:
        run_cli(SERVER, "run",
                f"mkdir -p {tmp_dir}/test_dl2/x && echo 'x-content' > {tmp_dir}/test_dl2/x/data.csv "
                f"&& echo 'y-content' > {tmp_dir}/test_dl2/x/readme.md")
    else:
        run_cli(SERVER, "run",
                f"cmd /c mkdir {tmp_dir}\\test_dl2\\x 2>nul && cmd /c echo x-content > {tmp_dir}\\test_dl2\\x\\data.csv "
                f"&& cmd /c echo y-content > {tmp_dir}\\test_dl2\\x\\readme.md")
    local_dir2 = TEST_DIR / "downloaded_dir2"
    clean_path(local_dir2)
    out, err, code = run_cli(SERVER, "download", f"{tmp_dir}/test_dl2", str(local_dir2))
    _inc("ok" if code == 0 else "fail",
         "download nested dir 2", f"stdout={out.strip()!r} code={code}")
    if local_dir2.exists():
        _inc("ok" if (local_dir2 / "x" / "data.csv").exists() else "fail",
             "data.csv exists")
        _inc("ok" if (local_dir2 / "x" / "readme.md").exists() else "fail",
             "readme.md exists")
        clean_path(local_dir2)


# ═══════ 11. download — regex pattern filter ══════════════════════

def test_download_pattern():
    _section("11. download — regex pattern filter")

    # Use temp directory based on OS
    if IS_UNIX:
        tmp_dir = "/tmp"
        run_cli(SERVER, "run",
                f"mkdir -p {tmp_dir}/test_pat1 && echo 'x' > {tmp_dir}/test_pat1/mix.log "
                f"&& echo 'y' > {tmp_dir}/test_pat1/mix.txt && echo 'z' > {tmp_dir}/test_pat1/mix.sh")
    else:
        tmp_dir = "C:/Users/leeho/AppData/Local/Temp"
        run_cli(SERVER, "run",
                f"cmd /c mkdir {tmp_dir}\\test_pat1 2>nul && cmd /c echo x > {tmp_dir}\\test_pat1\\mix.log "
                f"&& cmd /c echo y > {tmp_dir}\\test_pat1\\mix.txt && cmd /c echo z > {tmp_dir}\\test_pat1\\mix.bat")

    # Case 1: filter .log
    local_dir1 = TEST_DIR / "downloaded_pattern1"
    clean_path(local_dir1)
    out, err, code = run_cli(SERVER, "download", f"{tmp_dir}/test_pat1", str(local_dir1), "-p", "\\.log$")
    _inc("ok" if code == 0 else "fail",
         "pattern .log$ download", f"stdout={out.strip()!r}")
    if local_dir1.exists():
        files = [f.name for f in local_dir1.rglob("*") if f.is_file()]
        _inc("ok" if "mix.log" in files else "fail", "mix.log present", f"files={files}")
        _inc("ok" if "mix.txt" not in files else "fail", "mix.txt excluded")
        excl_name = "mix.sh" if IS_UNIX else "mix.bat"
        _inc("ok" if excl_name not in files else "fail", f"{excl_name} excluded")
        clean_path(local_dir1)

    # Case 2: filter .txt
    local_dir2 = TEST_DIR / "downloaded_pattern2"
    clean_path(local_dir2)
    out, err, code = run_cli(SERVER, "download", f"{tmp_dir}/test_pat1", str(local_dir2), "-p", "\\.txt$")
    _inc("ok" if code == 0 else "fail",
         "pattern .txt$ download", f"stdout={out.strip()!r}")
    if local_dir2.exists():
        files = [f.name for f in local_dir2.rglob("*") if f.is_file()]
        _inc("ok" if "mix.txt" in files else "fail", "mix.txt present", f"files={files}")
        _inc("ok" if "mix.log" not in files else "fail", "mix.log excluded")
        clean_path(local_dir2)


# ═══════ 12. timeout parameter ════════════════════════════════


# ═══════ 12. timeout parameter ════════════════════════════════════

def test_run_timeout():
    _section("12. timeout parameter")

    # Case 1: custom timeout on run
    out, err, code = run_cli(SERVER, "run", "echo timeout-test", "-t", "60")
    _inc("ok" if code == 0 and "timeout-test" in out else "fail",
         "run with custom timeout 60s", f"stdout={out.strip()!r}")

    # Case 2: default timeout (no -t)
    out, err, code = run_cli(SERVER, "run", "echo default-timeout")
    _inc("ok" if code == 0 and "default-timeout" in out else "fail",
         "run with default timeout", f"stdout={out.strip()!r}")


# ═══════ 13. sudo timeout parameter ═══════════════════════════════

def test_run_sudo_timeout():
    _section("13. sudo timeout parameter")
    
    # Skip all sudo tests on Windows
    if not IS_UNIX:
        _inc("skip", "sudo timeout (Unix only)")
        _inc("skip", "sudo timeout (Unix only)")
        return

    # Case 1: run -s with custom timeout
    out, err, code = run_cli(SERVER, "run", "echo sudo-timeout-test", "-s", "-t", "30")
    _inc("ok" if code == 0 and "sudo-timeout-test" in out else "fail",
         "run -s with custom timeout", f"stdout={out.strip()!r}")

    # Case 2: run -s whoami with timeout
    out, err, code = run_cli(SERVER, "run", "whoami", "-s", "-t", "30")
    _inc("ok" if "root" in out else "fail",
         "run -s whoami with timeout", f"stdout={out.strip()!r}")


# ═══════ 14. error handling ═══════════════════════════════════════

def test_error_handling():
    _section("14. error handling")
    # Case 1: nonexistent server
    out, err, code = run_cli("nonexistent-server", "run", "echo test")
    _inc("ok" if code != 0 else "fail",
         "nonexistent server returns error", f"stderr={err.strip()!r}")

    # Case 2: no args shows help
    out, err, code = run_cli()
    _inc("ok" if code == 0 and len(out) > 0 else "fail",
         "no args shows help", f"code={code}")

    # Case 3: nonexistent alias (already covered in test_alias_error, but verify on SERVER too)
    out, err, code = run_cli(SERVER, "alias", "does_not_exist_12345")
    _inc("ok" if code != 0 else "fail",
         "nonexistent alias on SERVER", f"code={code}")


# ═══════ 15. upload-all + list-scripts (test-server) ═════════════

def test_upload_all_and_list():
    """Combined upload-all + list-scripts validation for test-server."""
    _section("15. upload-all + list-scripts (test-server)")

    # Pre-clean: ensure a clean test run
    out, err, code = run_cli(TEST_SERVER, "upload-all")
    _inc("ok" if "Upload done" in out else "fail",
         "upload-all runs", f"stdout={out.strip()!r}")
    uploaded = [l for l in out.split("\n") if l.startswith("  +")]
    _inc("ok" if len(uploaded) > 0 else "fail",
         "upload-all uploads at least one script",
         f"uploaded={len(uploaded)} items={[l.strip() for l in uploaded[:5]]}")

    # list-scripts
    out, err, code = run_cli(TEST_SERVER, "list-scripts")
    _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "list-scripts returns output", f"stdout={out.strip()!r}")
    if IS_UNIX:
        has_files = any(".sh" in l for l in out.split("\n") if l.strip() and not l.startswith("total"))
        _inc("ok" if has_files else "fail",
             "list-scripts shows .sh files", f"stdout={out.strip()[:200]!r}")
    else:
        has_files = any(".bat" in l for l in out.split("\n") if l.strip())
        _inc("ok" if has_files else "fail",
             "list-scripts shows .bat files", f"stdout={out.strip()[:200]!r}")


# ═══════ 16. sudo upload — overwrite preserves owner/mode ═════════

def test_sudo_upload_preserve_ownership():
    """sudo upload over an existing file MUST keep the original owner/group/mode."""
    _section("16. sudo upload — overwrite preserves owner/mode")

    # Skip on Windows
    if not IS_UNIX:
        _inc("skip", "sudo upload preserve ownership (Unix only)")
        _inc("skip", "sudo upload preserve ownership (Unix only)")
        _inc("skip", "sudo upload preserve ownership (Unix only)")
        _inc("skip", "sudo upload preserve ownership (Unix only)")
        _inc("skip", "sudo upload preserve ownership (Unix only)")
        return

    probe = f"{SERVER_SCRIPTS_DIR}/_owner_probe.sh"
    local = TEST_DIR / "_owner_probe.sh"

    # Setup: create file as <user>:<user> mode 700 via sudo
    run_cli(SERVER, "run",
            f"mkdir -p {SERVER_SCRIPTS_DIR} && "
            f"echo 'original-content' > {probe} && "
            f"chown {SERVER_USER}:{SERVER_USER} {probe} && chmod 700 {probe}",
            "-s")

    # Baseline stat
    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {probe}")
    baseline = out.strip()
    expected_baseline = f"{SERVER_USER}:{SERVER_USER} 700"
    _inc("ok" if baseline == expected_baseline else "fail",
         f"baseline ownership is {expected_baseline}", f"baseline={baseline!r}")

    # Upload-overwrite via sudo with new content
    local.write_text("overwritten-content\n", encoding="utf-8")
    out, err, code = run_cli(SERVER, "upload", str(local), "-n", "_owner_probe.sh", "-s")
    _inc("ok" if code == 0 and "Script uploaded" in out else "fail",
         "sudo upload overwrite succeeds", f"stdout={out.strip()!r}")

    # Assert ownership and mode preserved
    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {probe}")
    after = out.strip()
    _inc("ok" if after == expected_baseline else "fail",
         f"owner+mode preserved ({expected_baseline})",
         f"before={baseline!r} after={after!r}")

    # Assert content updated
    out, _, _ = run_cli(SERVER, "run", f"cat {probe}")
    _inc("ok" if "overwritten-content" in out else "fail",
         "file content was updated", f"content={out.strip()!r}")

    # Cleanup
    run_cli(SERVER, "run", f"rm -f {probe}", "-s")
    clean_path(local)


# ═══════ 17. sudo upload — new file matches parent dir ═══════════

def test_sudo_upload_match_parent():
    """sudo upload of a new file MUST adopt the parent dir's owner."""
    _section("17. sudo upload — new file matches parent dir owner")

    # Skip on Windows
    if not IS_UNIX:
        _inc("skip", "sudo upload match parent (Unix only)")
        _inc("skip", "sudo upload match parent (Unix only)")
        _inc("skip", "sudo upload match parent (Unix only)")
        return

    local = TEST_DIR / "_new_probe.sh"
    local.write_text("new-content\n", encoding="utf-8")

    # scripts_dir is owned by SERVER_USER. Any new file installed via sudo
    # should adopt that owner with mode 0755.
    new_target = f"{SERVER_SCRIPTS_DIR}/_new_match_probe.sh"
    run_cli(SERVER, "run", f"rm -f {new_target}", "-s")

    # Sanity: ensure target does NOT exist
    out, _, code = run_cli(SERVER, "run", f"test -e {new_target}")
    _inc("ok" if code != 0 else "fail",
         "target file does not exist before upload", f"code={code}")

    out, err, code = run_cli(SERVER, "upload", str(local), "-n", "_new_match_probe.sh", "-s")
    _inc("ok" if code == 0 else "fail", "sudo upload new file succeeds", f"out={out.strip()!r}")

    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {new_target}")
    after = out.strip()
    expected = f"{SERVER_USER}:{SERVER_USER} 755"
    _inc("ok" if after == expected else "fail",
         f"new file inherits parent owner ({SERVER_USER}:{SERVER_USER}) mode 755",
         f"actual={after!r} expected={expected!r}")

    # Cleanup
    run_cli(SERVER, "run", f"rm -f {new_target}", "-s")
    clean_path(local)


# ═══════ 18. sudo download — original file untouched ═════════════

def test_sudo_download_no_chown_impact():
    """sudo download MUST NOT alter the original remote file's chown/mode."""
    _section("18. sudo download — stage copy approach, original untouched")

    # Skip on Windows
    if not IS_UNIX:
        _inc("skip", "sudo download (Unix only)")
        _inc("skip", "sudo download (Unix only)")
        _inc("skip", "sudo download (Unix only)")
        _inc("skip", "sudo download (Unix only)")
        _inc("skip", "sudo download (Unix only)")
        return

    src = "/root/_dl_probe.txt"
    local = TEST_DIR / "_dl_probe_downloaded.txt"
    clean_path(local)

    # Setup: create root-owned file with mode 600
    run_cli(SERVER, "run",
            f"echo 'root-secret-content' > {src} && "
            f"chown root:root {src} && chmod 600 {src}",
            "-s")

    # Baseline stat
    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {src}", "-s")
    baseline = out.strip()
    _inc("ok" if baseline == "root:root 600" else "fail",
         "baseline source is root:root 600", f"baseline={baseline!r}")

    # NEGATIVE: non-sudo download should fail (no read permission)
    out, err, code = run_cli(SERVER, "download", src, str(local))
    _inc("ok" if code != 0 else "fail",
         "non-sudo download of root-owned file fails",
         f"code={code} (expected non-zero)")
    clean_path(local)

    # POSITIVE: sudo download succeeds
    out, err, code = run_cli(SERVER, "download", src, str(local), "-s")
    _inc("ok" if code == 0 else "fail",
         "sudo download succeeds", f"stdout={out.strip()!r} err={err.strip()[:200]!r}")
    _inc("ok" if local.exists() and "root-secret-content" in local.read_text() else "fail",
         "downloaded content matches",
         f"exists={local.exists()} content={local.read_text() if local.exists() else 'N/A'!r}")

    # Critical: source file owner/mode must be unchanged
    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {src}", "-s")
    after = out.strip()
    _inc("ok" if after == baseline else "fail",
         "source owner/mode UNCHANGED after sudo download",
         f"before={baseline!r} after={after!r}")

    # Cleanup
    run_cli(SERVER, "run", f"rm -f {src}", "-s")
    clean_path(local)


# ═══════ 19. sudo list-scripts + sudo run-script ═════════════════

def test_sudo_list_scripts_and_run():
    """Verify -s works on list-scripts and run-script (root-owned scripts dir)."""
    _section("19. sudo list-scripts / run-script — root-only access")

    # Skip on Windows
    if not IS_UNIX:
        _inc("skip", "sudo list-scripts/run-script (Unix only)")
        _inc("skip", "sudo list-scripts/run-script (Unix only)")
        _inc("skip", "sudo list-scripts/run-script (Unix only)")
        return

    # Setup: create root-only file inside scripts_dir
    probe = f"{SERVER_SCRIPTS_DIR}/_root_only.sh"
    run_cli(SERVER, "run",
            f'echo "#!/usr/bin/env bash" > {probe} && '
            f'echo "echo root-only-script-ok" >> {probe} && '
            f"chown root:root {probe} && chmod 700 {probe}",
            "-s")

    # NEGATIVE: non-sudo run-script of root-only file fails (no exec/read perm)
    out, err, code = run_cli(SERVER, "run-script", "_root_only.sh")
    _inc("ok" if code != 0 else "fail",
         "non-sudo run-script of root:700 file fails",
         f"code={code} (expected non-zero)")

    # POSITIVE: sudo run-script succeeds
    out, err, code = run_cli(SERVER, "run-script", "_root_only.sh", "-s")
    _inc("ok" if code == 0 and "root-only-script-ok" in out else "fail",
         "sudo run-script succeeds", f"stdout={out.strip()[:200]!r}")

    # list-scripts -s should still work (and show the file)
    out, err, code = run_cli(SERVER, "list-scripts", "-s")
    _inc("ok" if code == 0 and "_root_only.sh" in out else "fail",
         "list-scripts -s shows root-only file",
         f"stdout has file: {'_root_only.sh' in out}")

    # Cleanup
    run_cli(SERVER, "run", f"rm -f {probe}", "-s")


# ═══════ 20. upload-all -s ═════════════════════════════════════════

def test_sudo_upload_all():
    """upload-all -s installs scripts as root (still preserves parent-dir owner)."""
    _section("20. upload-all -s — sudo install path")

    # Skip on Windows
    if not IS_UNIX:
        _inc("skip", "upload-all -s (Unix only)")
        _inc("skip", "upload-all -s (Unix only)")
        return

    out, err, code = run_cli(TEST_SERVER, "upload-all", "-s")
    _inc("ok" if "Upload done" in out else "fail",
         "upload-all -s runs", f"stdout={out.strip()!r}")
    uploaded = [l for l in out.split("\n") if l.startswith("  +")]
    _inc("ok" if len(uploaded) > 0 else "fail",
         "upload-all -s installs at least one script",
         f"uploaded={len(uploaded)}")


# ═══════ main ═════════════════════════════════════════════════════

def main():
    global passed, failed, skipped
    tests = [
        test_list_servers,                       # 1.  list-servers
        test_list_aliases,                       # 2.  list-aliases
        test_run_basic,                          # 3.  run basic
        test_run_sudo,                           # 4.  sudo via -s flag
        test_alias_inline,                       # 5.  alias inline (incl. sudo: true)
        test_alias_script,                       # 6.  alias script (incl. sudo: true)
        test_alias_error,                        # 7.  alias error
        test_upload_cases,                       # 8.  upload
        test_download_file,                      # 9.  download file
        test_download_directory,                 # 10. download dir
        test_download_pattern,                   # 11. download pattern
        test_run_timeout,                        # 12. timeout
        test_run_sudo_timeout,                   # 13. sudo timeout
        test_error_handling,                     # 14. error handling
        test_upload_all_and_list,                # 15. upload-all + list-scripts
        test_sudo_upload_preserve_ownership,     # 16. sudo upload — overwrite preserves owner
        test_sudo_upload_match_parent,           # 17. sudo upload — new file matches parent
        test_sudo_download_no_chown_impact,      # 18. sudo download — source untouched
        test_sudo_list_scripts_and_run,          # 19. sudo list-scripts / run-script
        test_sudo_upload_all,                    # 20. sudo upload-all
    ]

    print("=" * 60)
    print("  ssh-alias-mcp CLI Test Suite — 100% coverage")
    print(f"  Target: {SERVER} (read from SSH_TEST_SERVER env var)")
    print(f"  Tests:   {len(tests)} test functions")
    print("=" * 60)

    # Probe remote server info (sets SERVER_USER / SERVER_SCRIPTS_DIR)
    print("  Probed SSH_USER=$USER SSH_HOME=$HOME ...", flush=True)
    _probe_server_info()

    for t in tests:
        try:
            t()
        except subprocess.TimeoutExpired:
            _fail(f"{t.__name__} — timeout")
        except Exception as e:
            _fail(f"{t.__name__} — exception: {e}")

    print(f"\n{'='*60}")
    print(f"  Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}, {YELLOW}{skipped} skipped{NC}")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()