#!/usr/bin/env python3
"""Test suite for bash shell servers."""
import json
import os
import sys
from pathlib import Path

from test_common import (
    CLI, SKILL_DIR, TEST_DIR, RED, GREEN, YELLOW, NC,
    _reset_counter, _get_counter, _inc, _section, run_cli, clean_path,
    probe_server_info, get_total,
)

SERVER = os.environ.get("SSH_TEST_SERVER", "test-server")
TEST_SERVER = SERVER

EXPECTED_ALIASES = {
    "test-echo", "test-whoami-inline", "test-pipe",
    "test-multi-line", "test-error-inline",
    "test-hello-script", "test-env-script", "test-script-args",
    "test-sudo-inline", "test-sudo-script",
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
    if shell != "bash":
        raise RuntimeError(f"Expected bash shell, got {shell}")


# ── Shell-specific constants ──
EXT = ".sh"
TMP_DIR = "/tmp"


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

    out, err, code = run_cli(SERVER, "run", "uptime")
    _inc(c, "ok" if code == 0 else "fail", "uptime runs successfully", f"code={code}")

    out, err, code = run_cli(SERVER, "run", "false")
    _inc(c, "ok" if code != 0 else "fail", "false returns non-zero", f"code={code}")

    out, err, code = run_cli(SERVER, "run", "cat /etc/hostname")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "cat /etc/hostname returns content", f"stdout={out.strip()!r}")



# ── 4. sudo ──
def test_run_sudo():
    _section("4. sudo — root execution via -s flag")
    c = _get_counter()

    out, err, code = run_cli(SERVER, "run", "whoami", "-s")
    _inc(c, "ok" if "root" in out else "fail",
         "run -s whoami returns root", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "cat /etc/hostname", "-s")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "run -s cat /etc/hostname", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "false", "-s")
    _inc(c, "ok" if code != 0 else "fail", "run -s false returns non-zero", f"code={code}")

    out, err, code = run_cli(SERVER, "run", "id -u", "--sudo")
    _inc(c, "ok" if code == 0 and out.strip() == "0" else "fail",
         "run --sudo id -u returns 0", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "cat /etc/shadow")
    _inc(c, "ok" if code != 0 else "fail",
         "non-sudo cat /etc/shadow fails (permission denied)",
         f"code={code} (expected non-zero)")

    out, err, code = run_cli(SERVER, "run", "cat /etc/shadow", "-s")
    _inc(c, "ok" if code == 0 and "root:" in out else "fail",
         "sudo cat /etc/shadow succeeds", f"code={code} has_root_line={'root:' in out}")



# ── 5. alias inline ──
def test_alias_inline():
    _section("5. alias — inline")
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

    out, err, code = run_cli(TEST_SERVER, "alias", "test-sudo-inline")
    _inc(c, "ok" if code == 0 and "root" in out else "fail",
         "test-sudo-inline (sudo: true) returns root",
         f"stdout={out.strip()!r} code={code}")



# ── 6. alias script ──
def test_alias_script():
    _section("6. alias — script")
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
    _inc(c, "ok" if code == 0 and "UNAME:" in out and "USER:" in out else "fail",
         "test-env-script via alias works", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "list-scripts")
    _inc(c, "ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "list-scripts returns output", f"stdout={out.strip()!r}")
    has_files = any(".sh" in l for l in out.split("\n") if l.strip() and not l.startswith("total"))
    _inc(c, "ok" if has_files else "fail",
         "list-scripts shows .sh files", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "run-script", "test-hello.sh")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "run-script test-hello.sh works", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-script-args")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "test-script-args via alias works", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "alias", "test-sudo-script")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "test-sudo-script (sudo: true) runs", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(TEST_SERVER, "run-script", "test-hello.sh", "-s")
    _inc(c, "ok" if code == 0 and "test-hello-script-ok" in out else "fail",
         "run-script -s test-hello.sh works", f"stdout={out.strip()[:200]!r}")



# ── 7. alias error ──
def test_alias_error():
    _section("7. alias — error handling")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "alias", "does_not_exist_12345")
    _inc(c, "ok" if code != 0 else "fail",
         "nonexistent alias returns non-zero", f"code={code}")

    out, err, code = run_cli(SERVER, "alias", "does_not_exist_12345")
    _inc(c, "ok" if code != 0 else "fail",
         "nonexistent alias on SERVER", f"code={code}")



# ── 8. upload ──
def test_upload_cases():
    _section("8. upload — multiple cases")
    c = _get_counter()

    test_script = TEST_DIR / "test_upload.sh"
    test_script.write_text('#!/usr/bin/env bash\necho "upload-test-ok"\n')

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



# ── 9. download file ──
def test_download_file():
    _section("9. download — single file")
    c = _get_counter()

    run_cli(SERVER, "run", f"echo 'download-test-content-1' > {TMP_DIR}/test_cli_download_1.txt")
    local_file1 = TEST_DIR / "downloaded_file1.txt"
    clean_path(local_file1)
    out, err, code = run_cli(SERVER, "download", f"{TMP_DIR}/test_cli_download_1.txt", str(local_file1))
    _inc(c, "ok" if code == 0 and ("Downloaded" in out or "downloaded" in out.lower()) else "fail",
         "download single file", f"stdout={out.strip()!r}")
    if local_file1.exists():
        _inc(c, "ok" if "download-test-content-1" in local_file1.read_text() else "fail",
             "downloaded file1 content matches")
        clean_path(local_file1)

    run_cli(SERVER, "run", f"echo 'download-test-content-2' > {TMP_DIR}/test_cli_download_2.txt")
    local_file2 = TEST_DIR / "downloaded_file2.txt"
    clean_path(local_file2)
    out, err, code = run_cli(SERVER, "download", f"{TMP_DIR}/test_cli_download_2.txt", str(local_file2))
    _inc(c, "ok" if code == 0 else "fail",
         "download single file 2", f"stdout={out.strip()!r}")
    if local_file2.exists():
        _inc(c, "ok" if "download-test-content-2" in local_file2.read_text() else "fail",
             "downloaded file2 content matches")
        clean_path(local_file2)



# ── 10. download directory ──
def test_download_directory():
    _section("10. download — directory recursive")
    c = _get_counter()

    run_cli(SERVER, "run",
            f"mkdir -p {TMP_DIR}/test_dl1/a/b && echo 'file1-content' > {TMP_DIR}/test_dl1/a/file1.log "
            f"&& echo 'file2-content' > {TMP_DIR}/test_dl1/a/b/file2.txt")
    local_dir1 = TEST_DIR / "downloaded_dir1"
    clean_path(local_dir1)
    out, err, code = run_cli(SERVER, "download", f"{TMP_DIR}/test_dl1", str(local_dir1))
    _inc(c, "ok" if code == 0 else "fail",
         "download nested dir 1", f"stdout={out.strip()!r} code={code}")
    if local_dir1.exists():
        _inc(c, "ok" if (local_dir1 / "a" / "file1.log").exists() else "fail", "file1.log exists")
        _inc(c, "ok" if (local_dir1 / "a" / "b" / "file2.txt").exists() else "fail", "file2.txt exists")
        _inc(c, "ok" if "file1-content" in (local_dir1 / "a" / "file1.log").read_text() else "fail",
             "file1 content matches")
        clean_path(local_dir1)

    run_cli(SERVER, "run",
            f"mkdir -p {TMP_DIR}/test_dl2/x && echo 'x-content' > {TMP_DIR}/test_dl2/x/data.csv "
            f"&& echo 'y-content' > {TMP_DIR}/test_dl2/x/readme.md")
    local_dir2 = TEST_DIR / "downloaded_dir2"
    clean_path(local_dir2)
    out, err, code = run_cli(SERVER, "download", f"{TMP_DIR}/test_dl2", str(local_dir2))
    _inc(c, "ok" if code == 0 else "fail",
         "download nested dir 2", f"stdout={out.strip()!r} code={code}")
    if local_dir2.exists():
        _inc(c, "ok" if (local_dir2 / "x" / "data.csv").exists() else "fail", "data.csv exists")
        _inc(c, "ok" if (local_dir2 / "x" / "readme.md").exists() else "fail", "readme.md exists")
        clean_path(local_dir2)



# ── 11. download pattern ──
def test_download_pattern():
    _section("11. download — regex pattern filter")
    c = _get_counter()

    run_cli(SERVER, "run",
            f"mkdir -p {TMP_DIR}/test_pat1 && echo 'x' > {TMP_DIR}/test_pat1/mix.log "
            f"&& echo 'y' > {TMP_DIR}/test_pat1/mix.txt && echo 'z' > {TMP_DIR}/test_pat1/mix.sh")

    local_dir1 = TEST_DIR / "downloaded_pattern1"
    clean_path(local_dir1)
    out, err, code = run_cli(SERVER, "download", f"{TMP_DIR}/test_pat1", str(local_dir1), "-p", "\\.log$")
    _inc(c, "ok" if code == 0 else "fail",
         "pattern .log$ download", f"stdout={out.strip()!r}")
    if local_dir1.exists():
        files = [f.name for f in local_dir1.rglob("*") if f.is_file()]
        _inc(c, "ok" if "mix.log" in files else "fail", "mix.log present", f"files={files}")
        _inc(c, "ok" if "mix.txt" not in files else "fail", "mix.txt excluded")
        _inc(c, "ok" if "mix.sh" not in files else "fail", "mix.sh excluded")
        clean_path(local_dir1)

    local_dir2 = TEST_DIR / "downloaded_pattern2"
    clean_path(local_dir2)
    out, err, code = run_cli(SERVER, "download", f"{TMP_DIR}/test_pat1", str(local_dir2), "-p", "\\.txt$")
    _inc(c, "ok" if code == 0 else "fail",
         "pattern .txt$ download", f"stdout={out.strip()!r}")
    if local_dir2.exists():
        files = [f.name for f in local_dir2.rglob("*") if f.is_file()]
        _inc(c, "ok" if "mix.txt" in files else "fail", "mix.txt present", f"files={files}")
        _inc(c, "ok" if "mix.log" not in files else "fail", "mix.log excluded")
        clean_path(local_dir2)



# ── 12. timeout ──
def test_run_timeout():
    _section("12. timeout parameter")
    c = _get_counter()

    out, err, code = run_cli(SERVER, "run", "echo timeout-test", "-t", "60")
    _inc(c, "ok" if code == 0 and "timeout-test" in out else "fail",
         "run with custom timeout 60s", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "echo default-timeout")
    _inc(c, "ok" if code == 0 and "default-timeout" in out else "fail",
         "run with default timeout", f"stdout={out.strip()!r}")



# ── 13. sudo timeout ──
def test_run_sudo_timeout():
    _section("13. sudo timeout parameter")
    c = _get_counter()

    out, err, code = run_cli(SERVER, "run", "echo sudo-timeout-test", "-s", "-t", "30")
    _inc(c, "ok" if code == 0 and "sudo-timeout-test" in out else "fail",
         "run -s with custom timeout", f"stdout={out.strip()!r}")

    out, err, code = run_cli(SERVER, "run", "whoami", "-s", "-t", "30")
    _inc(c, "ok" if "root" in out else "fail",
         "run -s whoami with timeout", f"stdout={out.strip()!r}")



# ── 14. error handling ──
def test_error_handling():
    _section("14. error handling")
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



# ── 15. upload-all + list-scripts ──
def test_upload_all_and_list():
    _section("15. upload-all + list-scripts")
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
    has_sh = any(".sh" in l for l in out.split("\n") if l.strip() and not l.startswith("total"))
    _inc(c, "ok" if has_sh else "fail",
         "list-scripts shows .sh files", f"stdout={out.strip()[:200]!r}")



# ── 16. sudo upload preserve ownership ──
def test_sudo_upload_preserve_ownership():
    _section("16. sudo upload — overwrite preserves owner/mode")
    c = _get_counter()

    probe = f"{SERVER_SCRIPTS_DIR}/_owner_probe.sh"
    local = TEST_DIR / "_owner_probe.sh"

    run_cli(SERVER, "run",
            f"mkdir -p {SERVER_SCRIPTS_DIR} && "
            f"echo 'original-content' > {probe} && "
            f"chown {SERVER_USER}:{SERVER_USER} {probe} && chmod 700 {probe}",
            "-s")

    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {probe}")
    baseline = out.strip()
    expected_baseline = f"{SERVER_USER}:{SERVER_USER} 700"
    _inc(c, "ok" if baseline == expected_baseline else "fail",
         f"baseline ownership is {expected_baseline}", f"baseline={baseline!r}")

    local.write_text("overwritten-content\n", encoding="utf-8")
    out, err, code = run_cli(SERVER, "upload", str(local), "-n", "_owner_probe.sh", "-s")
    _inc(c, "ok" if code == 0 and "Script uploaded" in out else "fail",
         "sudo upload overwrite succeeds", f"stdout={out.strip()!r}")

    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {probe}")
    after = out.strip()
    _inc(c, "ok" if after == expected_baseline else "fail",
         f"owner+mode preserved ({expected_baseline})",
         f"before={baseline!r} after={after!r}")

    out, _, _ = run_cli(SERVER, "run", f"cat {probe}")
    _inc(c, "ok" if "overwritten-content" in out else "fail",
         "file content was updated", f"content={out.strip()!r}")

    run_cli(SERVER, "run", f"rm -f {probe}", "-s")
    clean_path(local)



# ── 17. sudo upload match parent ──
def test_sudo_upload_match_parent():
    _section("17. sudo upload — new file matches parent dir owner")
    c = _get_counter()

    local = TEST_DIR / "_new_probe.sh"
    local.write_text("new-content\n", encoding="utf-8")

    new_target = f"{SERVER_SCRIPTS_DIR}/_new_match_probe.sh"
    run_cli(SERVER, "run", f"rm -f {new_target}", "-s")

    out, _, code = run_cli(SERVER, "run", f"test -e {new_target}")
    _inc(c, "ok" if code != 0 else "fail",
         "target file does not exist before upload", f"code={code}")

    out, err, code = run_cli(SERVER, "upload", str(local), "-n", "_new_match_probe.sh", "-s")
    _inc(c, "ok" if code == 0 else "fail", "sudo upload new file succeeds", f"out={out.strip()!r}")

    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {new_target}")
    after = out.strip()
    expected = f"{SERVER_USER}:{SERVER_USER} 755"
    _inc(c, "ok" if after == expected else "fail",
         f"new file inherits parent owner ({SERVER_USER}:{SERVER_USER}) mode 755",
         f"actual={after!r} expected={expected!r}")

    run_cli(SERVER, "run", f"rm -f {new_target}", "-s")
    clean_path(local)



# ── 18. sudo download ──
def test_sudo_download_no_chown_impact():
    _section("18. sudo download — original file untouched")
    c = _get_counter()

    src = "/root/_dl_probe.txt"
    local = TEST_DIR / "_dl_probe_downloaded.txt"
    clean_path(local)

    run_cli(SERVER, "run",
            f"echo 'root-secret-content' > {src} && "
            f"chown root:root {src} && chmod 600 {src}",
            "-s")

    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {src}", "-s")
    baseline = out.strip()
    _inc(c, "ok" if baseline == "root:root 600" else "fail",
         "baseline source is root:root 600", f"baseline={baseline!r}")

    out, err, code = run_cli(SERVER, "download", src, str(local))
    _inc(c, "ok" if code != 0 else "fail",
         "non-sudo download of root-owned file fails",
         f"code={code} (expected non-zero)")
    clean_path(local)

    out, err, code = run_cli(SERVER, "download", src, str(local), "-s")
    _inc(c, "ok" if code == 0 else "fail",
         "sudo download succeeds", f"stdout={out.strip()!r} err={err.strip()[:200]!r}")
    _inc(c, "ok" if local.exists() and "root-secret-content" in local.read_text() else "fail",
         "downloaded content matches",
         f"exists={local.exists()} content={local.read_text() if local.exists() else 'N/A'!r}")

    out, _, _ = run_cli(SERVER, "run", f"stat -c '%U:%G %a' {src}", "-s")
    after = out.strip()
    _inc(c, "ok" if after == baseline else "fail",
         "source owner/mode UNCHANGED after sudo download",
         f"before={baseline!r} after={after!r}")

    run_cli(SERVER, "run", f"rm -f {src}", "-s")
    clean_path(local)



# ── 19. sudo list-scripts + run-script ──
def test_sudo_list_scripts_and_run():
    _section("19. sudo list-scripts / run-script")
    c = _get_counter()

    probe = f"{SERVER_SCRIPTS_DIR}/_root_only.sh"
    run_cli(SERVER, "run",
            f'echo "#!/usr/bin/env bash" > {probe} && '
            f'echo "echo root-only-script-ok" >> {probe} && '
            f"chown root:root {probe} && chmod 700 {probe}",
            "-s")

    out, err, code = run_cli(SERVER, "run-script", "_root_only.sh")
    _inc(c, "ok" if code != 0 else "fail",
         "non-sudo run-script of root:700 file fails",
         f"code={code} (expected non-zero)")

    out, err, code = run_cli(SERVER, "run-script", "_root_only.sh", "-s")
    _inc(c, "ok" if code == 0 and "root-only-script-ok" in out else "fail",
         "sudo run-script succeeds", f"stdout={out.strip()[:200]!r}")

    out, err, code = run_cli(SERVER, "list-scripts", "-s")
    _inc(c, "ok" if code == 0 and "_root_only.sh" in out else "fail",
         "list-scripts -s shows root-only file",
         f"stdout has file: {'_root_only.sh' in out}")

    run_cli(SERVER, "run", f"rm -f {probe}", "-s")



# ── 20. upload-all -s ──
def test_sudo_upload_all():
    _section("20. upload-all -s")
    c = _get_counter()

    out, err, code = run_cli(TEST_SERVER, "upload-all", "-s")
    _inc(c, "ok" if "Upload done" in out else "fail",
         "upload-all -s runs", f"stdout={out.strip()!r}")
    uploaded = [l for l in out.split("\n") if l.startswith("  +")]
    _inc(c, "ok" if len(uploaded) > 0 else "fail",
         "upload-all -s installs at least one script",
         f"uploaded={len(uploaded)}")



# ── main ──
def main():
    tests = [
        test_list_servers,
        test_list_aliases,
        test_run_basic,
        test_run_sudo,
        test_alias_inline,
        test_alias_script,
        test_alias_error,
        test_upload_cases,
        test_download_file,
        test_download_directory,
        test_download_pattern,
        test_run_timeout,
        test_run_sudo_timeout,
        test_error_handling,
        test_upload_all_and_list,
        test_sudo_upload_preserve_ownership,
        test_sudo_upload_match_parent,
        test_sudo_download_no_chown_impact,
        test_sudo_list_scripts_and_run,
        test_sudo_upload_all,
    ]

    print("=" * 60)
    print("  ssh-alias-mcp CLI Test Suite — bash")
    print(f"  Target: {SERVER} (read from SSH_TEST_SERVER env var)")
    print(f"  Tests:   {len(tests)} test functions")
    print("=" * 60)

    _probe()
    _reset_counter()

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
