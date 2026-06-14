#!/usr/bin/env python3
"""Test suite for powershell shell servers."""
import json
import os
import subprocess
import sys
from pathlib import Path

from test_common import (
    CLI, SKILL_DIR, TEST_DIR, RED, GREEN, YELLOW, NC,
    _reset_counter, _get_counter, _inc, _section, run_cli, clean_path,
    probe_server_info, get_total,
)

SERVER = os.environ.get("SSH_TEST_SERVER", "local-ssh").strip()
TEST_SERVER = SERVER

EXPECTED_ALIASES = {
    "test-echo", "test-whoami-inline", "test-pipe",
    "test-multi-line", "test-error-inline",
    "test-hello-script", "test-env-script", "test-script-args",
}

# ── Globals set by probe ──
SERVER_USER = None
SERVER_HOME = None
SERVER_SCRIPTS_DIR = None


def _probe():
    global SERVER_USER, SERVER_HOME, SERVER_SCRIPTS_DIR
    info = probe_server_info(SERVER)
    SERVER_USER = info["user"]
    SERVER_HOME = info["home"]
    SERVER_SCRIPTS_DIR = info["scripts_dir"]
    shell = info["shell"]
    if shell != "powershell":
        raise RuntimeError(f"Expected powershell shell, got {shell}")


# ── Shell-specific constants ──
EXT = ".ps1"
TMP_DIR = "$env:TEMP"


def _mkdir(p):
    return f'New-Item -ItemType Directory -Path "{p}" -Force'


def _write(path, content):
    return f'Set-Content -Path "{path}" -Value "{content}" -Encoding ASCII'


# ── 1. list-servers ──
def test_list_servers():
    _section("1. list-servers — server enumeration")
    c = _get_counter()

    out, err, code = run_cli("list-servers")
    data = json.loads(out)
    _inc(c, "ok" if code == 0 else "fail", "returns valid JSON", f"code={code}")
    _inc(c, "ok" if data["count"] > 0 else "fail", "at least one server configured",
         f"count={data['count']}")

    names = [s["name"] for s in data["servers"]]
    _inc(c, "ok" if SERVER in names else "fail",
         f"{SERVER} found in list", f"available: {names}")

    ts = next((s for s in data["servers"] if s["name"] == SERVER), None)
    if ts:
        _inc(c, "ok" if ts.get("host") and ts.get("port") else "fail",
             f"{SERVER} has host+port", f"host={ts.get('host')} port={ts.get('port')}")



# ── 2. list-aliases ──
def test_list_aliases():
    _section(f"2. list-aliases — {SERVER} alias enumeration")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "list-aliases")
    data = json.loads(out)
    _inc(c, "ok" if code == 0 else "fail", "returns valid JSON", f"code={code}")
    _inc(c, "ok" if data["count"] == len(data["aliases"]) else "fail",
         "count matches actual aliases", f"count={data['count']} actual={len(data['aliases'])}")

    aliases = {a["name"]: a for a in data["aliases"]}

    _inc(c, "ok" if "test-echo" in aliases else "fail",
         "inline alias 'test-echo' listed", f"names={list(aliases.keys())}")
    _inc(c, "ok" if "test-hello-script" in aliases else "fail",
         "script alias 'test-hello-script' listed", f"names={list(aliases.keys())}")

    echo_a = aliases.get("test-echo", {})
    _inc(c, "ok" if echo_a.get("type") == "inline" else "fail",
         "'test-echo' has type=inline", f"type={echo_a.get('type')}")

    script_a = aliases.get("test-hello-script", {})
    _inc(c, "ok" if script_a.get("type") == "script" else "fail",
         "'test-hello-script' has type=script", f"type={script_a.get('type')}")

    actual_names = set(aliases.keys())
    missing = EXPECTED_ALIASES - actual_names
    _inc(c, "ok" if not missing else "fail",
         f"all {len(EXPECTED_ALIASES)} expected aliases present",
         f"missing={sorted(missing)} actual={sorted(actual_names)}")



# ── 3. run basic ──
def test_run_basic():
    _section("3. run — basic command execution")
    c = _get_counter()

    out, err, code = run_cli(SERVER, "run", "echo hello_ssh")
    _inc(c, "ok" if code == 0 and "hello_ssh" in out else "fail",
         "echo command", f"stdout={out.strip()!r} code={code}")

    out, err, code = run_cli(SERVER, "run", "whoami")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
          "whoami returns non-empty", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "exit 1")
    _inc(c, "ok" if code != 0 else "fail", "exit 1 returns non-zero", f"code={code}")



# ── 4. alias inline ──
def test_alias_inline():
    _section("4. alias — inline")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "alias", "test-echo")
    _inc(c, "ok" if code == 0 and "test-inline-ok" in out else "fail",
         "test-echo outputs expected text", f"stdout={out.strip()!r} code={code}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-whoami-inline")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "test-whoami-inline returns non-empty", f"stdout={out.strip()!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-pipe")
    _inc(c, "ok" if code == 0 and out.strip().isdigit() and int(out.strip()) > 0 else "fail",
         "test-pipe returns positive number", f"stdout={out.strip()!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-multi-line")
    _inc(c, "ok" if code == 0 and "line1" in out and "line2" in out and "line3" in out else "fail",
         "test-multi-line outputs all 3 lines", f"stdout={out.strip()!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-error-inline")
    _inc(c, "ok" if code != 0 else "fail",
         "test-error-inline returns non-zero", f"code={code}")



# ── 5. alias script ──
def test_alias_script():
    _section("5. alias — script")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "upload-all")
    _inc(c, "ok" if "Upload done" in out else "fail",
         "upload-all runs", f"stdout={out.strip()!r}")
    uploaded = [l for l in out.split("\n") if l.startswith("  +")]
    _inc(c, "ok" if len(uploaded) > 0 else "fail",
         "upload-all uploads scripts", f"uploaded={len(uploaded)}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-hello-script")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "test-hello-script via alias works", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-env-script")
    _inc(c, "ok" if code == 0 and "UNAME:" in out else "fail",
         "test-env-script via alias works", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "list-scripts")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "list-scripts returns output", f"stdout={out.strip()!r}")
    has_files = any(".ps1" in l for l in out.split("\n") if l.strip())
    _inc(c, "ok" if has_files else "fail",
         "list-scripts shows .ps1 files", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "run-script", "test-hello.ps1")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "run-script test-hello.ps1 works", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-script-args")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "test-script-args via alias works", f"stdout={out.strip()[:200]!r}")



# ── 6. alias error ──
def test_alias_error():
    _section("6. alias — error handling")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "alias", "does_not_exist_12345")
    _inc(c, "ok" if code != 0 else "fail",
         "nonexistent alias returns non-zero", f"code={code}")

    out, err, code = run_cli(SERVER, "alias", "does_not_exist_12345")
    _inc(c, "ok" if code != 0 else "fail",
         "nonexistent alias on SERVER", f"code={code}")



# ── 7. upload ──
def test_upload_cases():
    _section("7. upload — multiple cases")
    c = _get_counter()

    test_script = TEST_DIR / "test_upload.ps1"
    test_script.write_text('Write-Output "upload-test-ok"\n')

    out, err, code = run_cli(SERVER, "upload", str(test_script))
    _inc(c, "ok" if "Script uploaded" in out else "fail",
         "upload script (no run)", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "upload", str(test_script), "-n", f"custom_test{EXT}")
    _inc(c, "ok" if f"custom_test{EXT}" in out else "fail",
         "upload with custom name", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "upload", str(test_script), "-r")
    _inc(c, "ok" if "upload-test-ok" in out else "fail",
         "upload + run", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "upload", str(test_script), "-n", f"custom_run{EXT}", "-r")
    _inc(c, "ok" if "upload-test-ok" in out else "fail",
         "upload custom name + run", f"stdout={out.strip()!r}")



# ── 8. download file ──
def test_download_file():
    _section("8. download — single file")
    c = _get_counter()

    # Get expanded temp path for SFTP
    out, _, _ = run_cli(SERVER, "run", "$env:TEMP")
    tmp = out.strip()

    run_cli(SERVER, "run",
            f'Set-Content -Path "{tmp}\\test_cli_download_1.txt" -Value "download-test-content-1" -Encoding ASCII')
    local_file1 = TEST_DIR / "downloaded_file1.txt"
    clean_path(local_file1)
    out, err, code = run_cli(SERVER, "download", f"{tmp}\\test_cli_download_1.txt", str(local_file1))
    _inc(c, "ok" if code == 0 and ("Downloaded" in out or "downloaded" in out.lower()) else "fail",
         "download single file", f"stdout={out.strip()!r}")
    if local_file1.exists():
        _inc(c, "ok" if "download-test-content-1" in local_file1.read_text() else "fail",
             "downloaded file1 content matches")
        clean_path(local_file1)

    run_cli(SERVER, "run",
            f'Set-Content -Path "{tmp}\\test_cli_download_2.txt" -Value "download-test-content-2" -Encoding ASCII')
    local_file2 = TEST_DIR / "downloaded_file2.txt"
    clean_path(local_file2)
    out, err, code = run_cli(SERVER, "download", f"{tmp}\\test_cli_download_2.txt", str(local_file2))
    _inc(c, "ok" if code == 0 else "fail",
         "download single file 2", f"stdout={out.strip()!r}")
    if local_file2.exists():
        _inc(c, "ok" if "download-test-content-2" in local_file2.read_text() else "fail",
             "downloaded file2 content matches")
        clean_path(local_file2)



# ── 9. download directory ──
def test_download_directory():
    _section("9. download — directory recursive")
    c = _get_counter()

    # Get expanded temp path for SFTP
    out, _, _ = run_cli(SERVER, "run", "$env:TEMP")
    tmp = out.strip()

    run_cli(SERVER, "run",
            f'New-Item -ItemType Directory -Path "{tmp}\\test_dl1\\a\\b" -Force; '
            f'Set-Content -Path "{tmp}\\test_dl1\\a\\file1.log" -Value "file1-content" -Encoding ASCII; '
            f'Set-Content -Path "{tmp}\\test_dl1\\a\\b\\file2.txt" -Value "file2-content" -Encoding ASCII')
    local_dir1 = TEST_DIR / "downloaded_dir1"
    clean_path(local_dir1)
    out, err, code = run_cli(SERVER, "download", f"{tmp}\\test_dl1", str(local_dir1))
    _inc(c, "ok" if code == 0 else "fail",
         "download nested dir 1", f"stdout={out.strip()!r} code={code}")
    if local_dir1.exists():
        _inc(c, "ok" if (local_dir1 / "a" / "file1.log").exists() else "fail", "file1.log exists")
        _inc(c, "ok" if (local_dir1 / "a" / "b" / "file2.txt").exists() else "fail", "file2.txt exists")
        _inc(c, "ok" if "file1-content" in (local_dir1 / "a" / "file1.log").read_text() else "fail",
             "file1 content matches")
        clean_path(local_dir1)

    run_cli(SERVER, "run",
            f'New-Item -ItemType Directory -Path "{tmp}\\test_dl2\\x" -Force; '
            f'Set-Content -Path "{tmp}\\test_dl2\\x\\data.csv" -Value "x-content" -Encoding ASCII; '
            f'Set-Content -Path "{tmp}\\test_dl2\\x\\readme.md" -Value "y-content" -Encoding ASCII')
    local_dir2 = TEST_DIR / "downloaded_dir2"
    clean_path(local_dir2)
    out, err, code = run_cli(SERVER, "download", f"{tmp}\\test_dl2", str(local_dir2))
    _inc(c, "ok" if code == 0 else "fail",
         "download nested dir 2", f"stdout={out.strip()!r} code={code}")
    if local_dir2.exists():
        _inc(c, "ok" if (local_dir2 / "x" / "data.csv").exists() else "fail", "data.csv exists")
        _inc(c, "ok" if (local_dir2 / "x" / "readme.md").exists() else "fail", "readme.md exists")
        clean_path(local_dir2)



# ── 10. download pattern ──
def test_download_pattern():
    _section("10. download — regex pattern filter")
    c = _get_counter()

    # Get expanded temp path for SFTP
    out, _, _ = run_cli(SERVER, "run", "$env:TEMP")
    tmp = out.strip()

    run_cli(SERVER, "run",
            f'New-Item -ItemType Directory -Path "{tmp}\\test_pat1" -Force; '
            f'Set-Content -Path "{tmp}\\test_pat1\\mix.log" -Value "x" -Encoding ASCII; '
            f'Set-Content -Path "{tmp}\\test_pat1\\mix.txt" -Value "y" -Encoding ASCII; '
            f'Set-Content -Path "{tmp}\\test_pat1\\mix.ps1" -Value "z" -Encoding ASCII')

    local_dir1 = TEST_DIR / "downloaded_pattern1"
    clean_path(local_dir1)
    out, err, code = run_cli(SERVER, "download", f"{tmp}\\test_pat1", str(local_dir1), "-p", "\\.log$")
    _inc(c, "ok" if code == 0 else "fail",
         "pattern .log$ download", f"stdout={out.strip()!r}")
    if local_dir1.exists():
        files = [f.name for f in local_dir1.rglob("*") if f.is_file()]
        _inc(c, "ok" if "mix.log" in files else "fail", "mix.log present", f"files={files}")
        _inc(c, "ok" if "mix.txt" not in files else "fail", "mix.txt excluded")
        _inc(c, "ok" if "mix.ps1" not in files else "fail", "mix.ps1 excluded")
        clean_path(local_dir1)

    local_dir2 = TEST_DIR / "downloaded_pattern2"
    clean_path(local_dir2)
    out, err, code = run_cli(SERVER, "download", f"{tmp}\\test_pat1", str(local_dir2), "-p", "\\.txt$")
    _inc(c, "ok" if code == 0 else "fail",
         "pattern .txt$ download", f"stdout={out.strip()!r}")
    if local_dir2.exists():
        files = [f.name for f in local_dir2.rglob("*") if f.is_file()]
        _inc(c, "ok" if "mix.txt" in files else "fail", "mix.txt present", f"files={files}")
        _inc(c, "ok" if "mix.log" not in files else "fail", "mix.log excluded")
        clean_path(local_dir2)



# ── 11. timeout ──
def test_run_timeout():
    _section("11. timeout parameter")
    c = _get_counter()

    out, err, code = run_cli(SERVER, "run", "echo timeout-test", "-t", "60")
    _inc(c, "ok" if code == 0 and "timeout-test" in out else "fail",
         "run with custom timeout 60s", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "echo default-timeout")
    _inc(c, "ok" if code == 0 and "default-timeout" in out else "fail",
         "run with default timeout", f"stdout={out.strip()!r}")



# ── 12. error handling ──
def test_error_handling():
    _section("12. error handling")
    c = _get_counter()

    out, err, code = run_cli("nonexistent-server", "run", "echo test")
    _inc(c, "ok" if code != 0 else "fail",
         "nonexistent server returns error", f"stderr={err.strip()!r}")

    out, err, code = run_cli()
    _inc(c, "ok" if code == 0 and len(out) > 0 else "fail",
         "no args shows help", f"code={code}")

    out, err, code = run_cli(SERVER, "alias", "does_not_exist_12345")
    _inc(c, "ok" if code != 0 else "fail",
         "nonexistent alias on SERVER", f"code={code}")



# ── 13. upload-all + list-scripts ──
def test_upload_all_and_list():
    _section("13. upload-all + list-scripts")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "upload-all")
    _inc(c, "ok" if "Upload done" in out else "fail",
         "upload-all runs", f"stdout={out.strip()!r}")
    uploaded = [l for l in out.split("\n") if l.startswith("  +")]
    _inc(c, "ok" if len(uploaded) > 0 else "fail",
         "upload-all uploads at least one script", f"uploaded={len(uploaded)}")

    out, err, code = run_cli(TEST_SERVER, "list-scripts")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "list-scripts returns output", f"stdout={out.strip()!r}")
    has_ps1 = any(".ps1" in l for l in out.split("\n") if l.strip())
    _inc(c, "ok" if has_ps1 else "fail",
         "list-scripts shows .ps1 files", f"stdout={out.strip()[:200]!r}")



# ── main ──
def main():
    tests = [
        test_list_servers,
        test_list_aliases,
        test_run_basic,
        test_alias_inline,
        test_alias_script,
        test_alias_error,
        test_upload_cases,
        test_download_file,
        test_download_directory,
        test_download_pattern,
        test_run_timeout,
        test_error_handling,
        test_upload_all_and_list,
    ]

    print("=" * 60)
    print("  ssh-alias-mcp CLI Test Suite — powershell")
    print(f"  Target: {SERVER} (read from SSH_TEST_SERVER env var)")
    print(f"  Tests:   {len(tests)} test functions")
    print("=" * 60)

    _probe()
    _reset_counter()
    total = {"passed": 0, "failed": 0, "skipped": 0}

    for t in tests:
        try:
            t()
        except subprocess.TimeoutExpired:
            total["failed"] += 1
            print(f"{RED}FAIL{NC}  {t.__name__} — timeout")
        except Exception as e:
            total["failed"] += 1
            print(f"{RED}FAIL{NC}  {t.__name__} — exception: {e}")

    total = get_total()
    print(f"\n{'='*60}")
    print(f"  Results: {GREEN}{total['passed']} passed{NC}, {RED}{total['failed']} failed{NC}, {YELLOW}{total['skipped']} skipped{NC}")
    print(f"{'='*60}")

    if total["failed"] > 0:
        sys.exit(1)
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
