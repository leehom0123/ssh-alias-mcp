#!/usr/bin/env python3
"""Common utilities for ssh-alias-mcp test suites."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
TEST_DIR = Path(__file__).parent
CLI = [sys.executable, str(SKILL_DIR / "cli.py")]

RED, GREEN, YELLOW, NC = "\033[91m", "\033[92m", "\033[93m", "\033[0m"


# Global counter shared across all tests (avoids pytest ReturnNotNoneWarning)
_COUNTER = {"passed": 0, "failed": 0, "skipped": 0}


def _reset_counter():
    """Reset global counter (call once per test suite run)."""
    _COUNTER["passed"] = 0
    _COUNTER["failed"] = 0
    _COUNTER["skipped"] = 0


def _get_counter():
    return _COUNTER


def _ok(c, msg):
    print(f"{GREEN}PASS{NC}  {msg}")
    c["passed"] += 1


def _fail(c, msg, detail=""):
    print(f"{RED}FAIL{NC}  {msg} — {detail}" if detail else f"{RED}FAIL{NC}  {msg}")
    c["failed"] += 1


def _skip(c, msg):
    print(f"{YELLOW}SKIP{NC}  {msg}")
    c["skipped"] += 1


def _inc(c, result, name, detail=""):
    if result == "ok":
        _ok(c, name)
    elif result == "fail":
        _fail(c, name, detail)
    else:
        _skip(c, name)


def get_total():
    """Return a copy of the global counter."""
    return dict(_COUNTER)


def _section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


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


def probe_server_info(server):
    """Probe server info and return dict with user, home, scripts_dir, shell."""
    # Get shell from list-servers
    r = subprocess.run(
        CLI + ["list-servers"],
        capture_output=True, text=True, timeout=60,
        cwd=str(SKILL_DIR), encoding='utf-8', errors='replace',
    )
    shell = "bash"
    if r.returncode == 0:
        data = json.loads(r.stdout)
        for s in data.get("servers", []):
            if s.get("name") == server:
                shell = s.get("shell", "bash")
                break

    r = subprocess.run(
        CLI + [server, "run", "whoami"],
        capture_output=True, text=True, timeout=60,
        cwd=str(SKILL_DIR), encoding='utf-8', errors='replace',
    )
    if r.returncode != 0:
        raise RuntimeError(f"Cannot probe server {server}: {r.stderr.strip()[:300]}")
    user = r.stdout.strip()

    # Get home dir
    r = subprocess.run(
        CLI + [server, "run", "cd && pwd"],
        capture_output=True, text=True, timeout=60,
        cwd=str(SKILL_DIR), encoding='utf-8', errors='replace',
    )
    home = r.stdout.strip() if r.returncode == 0 else f"/home/{user}"

    return {
        "user": user,
        "home": home,
        "scripts_dir": f"{home}/scripts",
        "shell": shell,
    }
