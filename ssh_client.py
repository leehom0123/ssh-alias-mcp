#!/usr/bin/env python3
"""SSH Client - Core SSH connection module

Supports direct connection, SOCKS5 proxy with auto-fallback, connection pool, and script upload/run.

Usage:

    # Direct Python import
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from ssh_client import pool

    conn = pool.get("my-server")
    result = conn.run("ls -la /opt")
    print(result["stdout"])

    # MCP mode (via mcp_server.py)
    claude mcp add ssh-alias-mcp python <path>/mcp_server.py
"""
import hashlib
import io
import os
import posixpath
import re
import shlex
import socket
import sys
import time
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional, Any

import paramiko
import yaml

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
KNOWN_HOSTS_PATH = SCRIPT_DIR / "known_hosts"
_KNOWN_HOSTS_LOCK = threading.Lock()

HOST_KEY_MODES = ("tofu", "strict", "insecure")

# Control characters that must never appear in a generated remote command.
_FORBIDDEN_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _safe_relpath(raw, field: str) -> str:
    """Validate a remote-relative path fragment (e.g. a script name).

    Rejects absolute paths, backslashes, parent-directory traversal,
    empty segments and control characters. Returns the normalised
    slash-separated path.
    """
    s = str(raw)
    if not s or "\\" in s or s.startswith("/") or _FORBIDDEN_CHARS_RE.search(s):
        raise ValueError(f"Invalid {field}: {raw!r}")
    parts = s.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise ValueError(f"Invalid {field}: {raw!r}")
    return "/".join(parts)


def _resolve_proxy(server_proxy: Optional[dict],
                   global_proxy_cfg: dict) -> Optional[dict]:
    """Resolve the effective proxy for a server.

    Per-server proxy wins; otherwise use the global SOCKS5 proxy when it is
    enabled. Shared by ``SSHConnection.__init__`` and ``_connect_once`` so
    both agree on the precedence rules.
    """
    if server_proxy:
        return dict(server_proxy)
    if global_proxy_cfg.get("enabled"):
        return {
            "host": global_proxy_cfg.get("host", "127.0.0.1"),
            "port": global_proxy_cfg.get("port", 1080),
        }
    return None


class SSHOperationError(OSError):
    """A remote operation failed, with enough context for an MCP response."""


def _operation_failure(operation: str, target: str, timeout: float,
                       exc: Exception) -> SSHOperationError:
    """Turn low-level socket/Paramiko exceptions into actionable diagnostics."""
    is_timeout = isinstance(exc, (TimeoutError, socket.timeout))
    kind = "timed out" if is_timeout else "failed"
    detail = str(exc).strip() or type(exc).__name__
    return SSHOperationError(
        f"SSH {operation} {kind} for {target} after {timeout} seconds "
        f"({type(exc).__name__}: {detail})"
    )


def _repair_surrogateescaped_command(command: str) -> str:
    """Restore UTF-8 text that a Windows code page decoded with surrogateescape.

    MCP arguments should already be valid Unicode. Some Windows stdio bridges,
    however, decode UTF-8 command bytes with the active ANSI code page and use
    ``surrogateescape`` for undecodable bytes. The resulting string contains
    low surrogates and Paramiko cannot encode it as UTF-8. Re-encoding with the
    same Windows code page reconstructs the original bytes.

    Valid Unicode commands are returned unchanged. A command containing low
    surrogates that cannot be repaired is rejected instead of being silently
    altered or sent to the wrong remote shell.
    """
    if not any(0xDC80 <= ord(char) <= 0xDCFF for char in command):
        return command

    encodings = []
    if sys.platform == "win32":
        import locale
        encodings.append(locale.getpreferredencoding(False))
    encodings.extend(("gbk", "cp936"))

    seen = set()
    for encoding in encodings:
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            repaired = command.encode(encoding, errors="surrogateescape").decode("utf-8")
            repaired.encode("utf-8")
            return repaired
        except (LookupError, UnicodeError):
            continue

    raise ValueError(
        "Command contains invalid surrogate-escaped text and could not be "
        "restored as UTF-8"
    )


def _normalize_path(raw: str) -> str:
    """Normalize a path string for the current OS.

    Converts Windows-style drive paths (e.g. 'D:\\agents\\servers') to WSL
    /mnt/<drive>/... form when running under Linux. No-op elsewhere.
    """
    if not raw:
        return raw
    s = str(raw).replace("\\", "/")
    
    # Convert Windows path to WSL path when running under Linux
    if sys.platform == "linux":
        # Match "X:/..." pattern
        if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
            drive = s[0].lower()
            rest = s[2:].lstrip("/")
            return f"/mnt/{drive}/{rest}"
        return s
    
    # Convert WSL path to Windows path when running under Windows
    if sys.platform == "win32":
        # Match "/mnt/X/..." pattern
        if s.startswith("/mnt/"):
            parts = s.split("/", 3)
            if len(parts) >= 4:
                drive = parts[2].upper()
                rest = parts[3]
                return f"{drive}:/{rest}"
        return s
    
    return s


def load_yaml(path: str) -> dict:
    """Load a YAML config file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    text = p.read_text(encoding="utf-8-sig")
    return yaml.safe_load(text) or {}


def load_global_config() -> dict:
    """Load global configuration (proxy, timeout, etc.)"""
    if not CONFIG_PATH.exists():
        return {"proxy": {"enabled": False}, "server": {"timeout": 30}}
    return load_yaml(str(CONFIG_PATH))


global_config: dict = load_global_config()

# servers_dir can be configured in config.yaml (absolute or relative to config file)
_servers_dir_cfg = _normalize_path(global_config.get("servers_dir", "servers"))
SERVERS_DIR = (CONFIG_PATH.parent / _servers_dir_cfg).resolve() if _servers_dir_cfg else SCRIPT_DIR / "servers"


# Command templates keyed by shell type.
# Available placeholders: path, src, dst, tmp, target, mode, user
# All placeholder values are shell-quoted centrally by SSHConnection._cmd;
# templates must NOT add their own quotes around placeholders.
CMD_TEMPLATES = {
    "bash": {
        "mkdir":        "mkdir -p {path}",
        "file_exists":  "test -f {path}",
        "run_script":   "bash {path}",
        "chmod":        "chmod {mode} {path}",
        "stat_owner":   "stat -c '%U:%G' {path}",
        "stat_mode":    "stat -c '%a' {path}",
        "rm_dir":       "rm -rf {path}",
        "cp_r":         "cp -r {src} {dst}",
        "move":         "mv {tmp} {target}",
        "chown":        "chown {user} {path}",
        "chown_r":      "chown -R {user}:{user} {path}",
        "list_dir":     "ls -la {path}",
        "install":      (
            "if [ -e {target} ]; then "
            "OWN=$(stat -c '%u:%g' {target}); "
            "MODE=$(stat -c '%a' {target}); "
            "mv {tmp} {target} && "
            "chown $OWN {target} && "
            "chmod $MODE {target}; "
            "else "
            "PARENT=$(dirname {target}); "
            "OWN=$(stat -c '%u:%g' \"$PARENT\"); "
            "mv {tmp} {target} && "
            "chown $OWN {target} && "
            "chmod 755 {target}; "
            "fi"
        ),
        "tmp_prefix":   "/tmp",
    },
    "cmd": {
        "mkdir":        "mkdir {path} 2>nul",
        "file_exists":  "if exist {path} (echo exists) else exit 1",
        "run_script":   "call {path}",
        "chmod":        "",  # no-op on Windows cmd
        "stat_owner":   "echo n/a",
        "stat_mode":    "echo n/a",
        "rm_dir":       "rmdir /S /Q {path}",
        "cp_r":         "xcopy /Y {src} {dst} /E /I",
        "move":         "move /y {tmp} {target}",
        "chown":        "",  # no-op
        "chown_r":      "",  # no-op
        "list_dir":     "dir {path} /Q",
        "install":      "move /y {tmp} {target}",
        "tmp_prefix":   "%TEMP%",
    },
    "powershell": {
        "mkdir":        "New-Item -ItemType Directory -Path {path} -Force",
        "file_exists":  "if (Test-Path {path}) { exit 0 } else { exit 1 }",
        "run_script":   "& {path}",
        "chmod":        "",  # no-op
        "stat_owner":   "echo n/a",
        "stat_mode":    "echo n/a",
        "rm_dir":       "Remove-Item -Recurse -Force {path}",
        "cp_r":         "Copy-Item -Recurse {src} {dst}",
        "move":         "Move-Item -Force {tmp} {target}",
        "chown":        "",  # no-op
        "chown_r":      "",  # no-op
        "list_dir":     "Get-ChildItem -Path {path} | Format-List",
        "install":      "Move-Item -Force {tmp} {target}",
        "tmp_prefix":   "$env:TEMP",
    },
}


class SSHConnection:
    """SSH connection wrapper — proxy/direct auto-fallback, script upload & execution, command execution"""

    def __init__(self, cfg: dict, aliases: list = None,
                 yml_path: str = None):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 22))
        self.user = cfg["user"]
        self.password = cfg.get("password")
        self.key_path = _normalize_path(cfg.get("key", "")) or None
        self.key_password = cfg.get("key_password")
        self.sudo_password = cfg.get("sudo_password") or cfg.get("password")
        self.timeout = int(cfg.get("timeout",
                                   global_config.get("server", {}).get("timeout", 30)))
        server_defaults = global_config.get("server", {})
        self.auto_reconnect = bool(
            cfg.get("auto_reconnect", server_defaults.get("auto_reconnect", True))
        )
        self.reconnect_interval = max(
            0.0,
            float(cfg.get(
                "reconnect_interval",
                server_defaults.get("reconnect_interval", 5),
            )),
        )
        self.reconnect_fast_attempts = 5
        self.reconnect_slow_interval = 60.0
        self.keepalive_interval = max(
            0,
            int(cfg.get("keepalive_interval", server_defaults.get("keepalive_interval", 15))),
        )
        self.scripts_dir = cfg.get("scripts_dir",
                                     f"/home/{cfg.get('user', 'root')}/scripts")
        self.scripts_local_dir = cfg.get("scripts_local_dir")
        self.shell = cfg.get("shell", "bash")  # "bash", "cmd", "powershell"
        self._tpl = CMD_TEMPLATES[self.shell]
        self.aliases = aliases or []
        self._yml_path = yml_path

        # Security: command filtering (per-server overrides the global
        # 'security' section from config.yaml).
        sec_cfg = global_config.get("security", {}) or {}
        wl = cfg.get("whitelist")
        if wl is None:
            wl = sec_cfg.get("whitelist") or []
        bl = cfg.get("blacklist")
        if bl is None:
            bl = sec_cfg.get("blacklist") or []
        template = cfg.get("command_template")
        if not template:
            template = sec_cfg.get("command_template") or ""
        self.whitelist = [re.compile(p) for p in wl]
        self.blacklist = [re.compile(p) for p in bl]
        self.command_template = template

        # Host key verification: "tofu" (trust on first use, persist),
        # "strict" (known_hosts must contain the key), "insecure" (no check).
        host_key_mode = str(
            cfg.get("host_key_checking",
                    server_defaults.get("host_key_checking", "tofu"))
        ).lower()
        if host_key_mode not in HOST_KEY_MODES:
            raise ValueError(
                f"Invalid host_key_checking: {host_key_mode!r}; "
                f"expected one of {HOST_KEY_MODES}"
            )
        self.host_key_checking = host_key_mode

        # Security: path restrictions for upload/download
        self._allowed_paths = {
            "local": [str(Path(p).resolve()) for p in cfg.get("allowed_local_paths", [])],
            "remote": [str(p) for p in cfg.get("allowed_remote_paths", [])],
        }

        # Proxy config: per-server takes priority, then global fallback
        self.proxy = _resolve_proxy(cfg.get("proxy"),
                                    global_config.get("proxy", {}) or {})

        self._client: Optional[paramiko.SSHClient] = None
        self._last_used = 0
        self._connect_lock = threading.Lock()
        self._connect_failures = 0
        self._reconnect_wakeup = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._closed = False

    def connect(self):
        """Connect for an explicit request, bypassing any reconnect cooldown."""
        with self._connect_lock:
            self._closed = False
            if self._client and self._is_alive():
                self._last_used = time.time()
                return
            try:
                self._connect_once()
            except Exception as exc:
                self._record_connect_failure()
                raise _operation_failure(
                    "connection", f"{self.user}@{self.host}:{self.port}",
                    self.timeout, exc,
                ) from exc
            self._connect_failures = 0
            self._reconnect_wakeup.set()

    def _connect_once(self):
        """Perform one proxy/direct connection cycle."""
        # Per-server proxy wins; global proxy config is re-read live so
        # toggling proxy.enabled in config.yaml applies to new connections.
        proxy = _resolve_proxy(self.proxy,
                               load_global_config().get("proxy", {}) or {})

        if self._client:
            self._client.close()
            self._client = None

        # Check pysocks availability
        if proxy:
            import socks  # noqa: F401

        # Verify the server host key before authenticating, so a MITM never
        # sees the password or key passphrase.
        self._probe_host_key(proxy)

        # Try proxy connection first
        if proxy:
            try:
                self._connect_proxy_with(proxy)
                return
            except Exception as e:
                error_msg = f"Proxy connection failed ({type(e).__name__}: {e}), fallback to direct..."
                print(error_msg, file=sys.stderr)
                self._log_proxy_error(proxy, e)

        # Direct connection (fallback after proxy failure)
        self._connect_direct()

    def _make_client(self) -> paramiko.SSHClient:
        """Create an SSHClient preloaded with trusted host keys."""
        c = paramiko.SSHClient()
        c.load_system_host_keys()
        if KNOWN_HOSTS_PATH.exists():
            try:
                c.load_host_keys(str(KNOWN_HOSTS_PATH))
            except Exception as e:
                print(f"Warning: could not load {KNOWN_HOSTS_PATH}: {e}",
                      file=sys.stderr)
        if self.host_key_checking == "insecure":
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            c.set_missing_host_key_policy(paramiko.RejectPolicy())
        return c

    def _make_socket(self, proxy: Optional[dict]):
        """Open a TCP (optionally SOCKS5-proxied) socket to the server."""
        if proxy:
            import socks
            sock = socks.socksocket()
            sock.set_proxy(socks.SOCKS5, proxy["host"], int(proxy["port"]))
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            return sock
        return socket.create_connection((self.host, self.port),
                                        timeout=self.timeout)

    def _probe_host_key(self, proxy: Optional[dict]):
        """Handshake without authenticating and verify/persist the host key."""
        if self.host_key_checking == "insecure":
            return
        sock = None
        transport = None
        try:
            sock = self._make_socket(proxy)
            transport = paramiko.Transport(sock)
            transport.banner_timeout = self.timeout
            transport.start_client()
            key = transport.get_remote_server_key()
        except paramiko.SSHException:
            raise
        except Exception as e:
            raise paramiko.SSHException(
                f"Host key probe to {self.host}:{self.port} failed: "
                f"{type(e).__name__}: {e}"
            ) from e
        finally:
            if transport is not None:
                transport.close()
            elif sock is not None:
                sock.close()
        self._check_or_record_host_key(key)

    def _host_key_display(self) -> str:
        if self.port == 22:
            return self.host
        return f"[{self.host}]:{self.port}"

    def _check_or_record_host_key(self, key):
        display = self._host_key_display()
        with _KNOWN_HOSTS_LOCK:
            known = paramiko.HostKeys()
            if KNOWN_HOSTS_PATH.exists():
                try:
                    known.load(str(KNOWN_HOSTS_PATH))
                except Exception:
                    pass
            entry = known.lookup(display)
            stored = entry.get(key.get_name()) if entry else None
            if stored is not None:
                if stored.get_fingerprint() != key.get_fingerprint():
                    raise paramiko.SSHException(
                        f"Host key mismatch for {display}: the {key.get_name()} "
                        f"key changed (stored fingerprint "
                        f"{stored.get_fingerprint().hex()}, server presented "
                        f"{key.get_fingerprint().hex()}). Possible MITM; "
                        "refusing to connect."
                    )
                return
            if self.host_key_checking == "strict":
                raise paramiko.SSHException(
                    f"Unknown host key for {display} ({key.get_name()} "
                    f"{key.get_fingerprint().hex()}); host_key_checking is "
                    "'strict'. Seed known_hosts or use 'tofu'."
                )
            known.add(display, key.get_name(), key)
            try:
                KNOWN_HOSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
                known.save(str(KNOWN_HOSTS_PATH))
            except Exception as e:
                print(f"Warning: could not persist known_hosts: {e}",
                      file=sys.stderr)

    def _record_connect_failure(self):
        """Record a failed cycle and ensure policy-controlled background retries."""
        self._connect_failures += 1
        if not self.auto_reconnect or self._closed:
            return
        if not self._reconnect_thread or not self._reconnect_thread.is_alive():
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                name=f"ssh-reconnect-{self.host}",
                daemon=True,
            )
            self._reconnect_thread.start()
        self._reconnect_wakeup.set()

    def _reconnect_loop(self):
        """Retry quickly for five failures, then once per minute."""
        while True:
            with self._connect_lock:
                if (
                    self._closed
                    or not self.auto_reconnect
                    or (self._client and self._is_alive())
                ):
                    return
                delay = (
                    self.reconnect_interval
                    if self._connect_failures < self.reconnect_fast_attempts
                    else self.reconnect_slow_interval
                )
                self._reconnect_wakeup.clear()

            # Explicit requests set this event after their immediate attempt,
            # restarting the policy delay without causing a duplicate attempt.
            if self._reconnect_wakeup.wait(delay):
                continue

            with self._connect_lock:
                if (
                    self._closed
                    or not self.auto_reconnect
                    or (self._client and self._is_alive())
                ):
                    return
                try:
                    self._connect_once()
                except Exception as exc:
                    self._connect_failures += 1
                    phase = (
                        "fast"
                        if self._connect_failures < self.reconnect_fast_attempts
                        else "60s cooldown"
                    )
                    print(
                        f"SSH reconnect failed "
                        f"(consecutive failures={self._connect_failures}, next={phase}): "
                        f"{type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                self._connect_failures = 0
                return

    def _connect_proxy_with(self, proxy: dict):
        """Connect via SOCKS5 proxy"""
        sock = self._make_socket(proxy)
        transport = None
        try:
            c = self._make_client()
            transport = paramiko.Transport(sock)
            kw = self._get_auth()
            transport.banner_timeout = self.timeout
            transport.auth_timeout = self.timeout
            transport.connect(username=self.user, **kw)
            transport.set_keepalive(self.keepalive_interval)
            c._transport = transport
            self._client = c
            self._last_used = time.time()
        except Exception:
            if transport is not None:
                transport.close()
            else:
                sock.close()
            raise

    def _connect_direct(self):
        """Connect directly (no proxy)"""
        c = self._make_client()
        kw = self._get_auth()
        try:
            c.connect(
                self.host,
                port=self.port,
                username=self.user,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                channel_timeout=self.timeout,
                **kw,
            )
            c.get_transport().set_keepalive(self.keepalive_interval)
            self._client = c
            self._last_used = time.time()
        except Exception:
            c.close()
            raise

    def _get_auth(self) -> dict:
        """Get authentication parameters (key or password)"""
        if self.key_path:
            kw = {"key_filename": self.key_path}
            if self.key_password:
                kw["passphrase"] = self.key_password
            return kw
        return {"password": self.password}

    def _is_alive(self) -> bool:
        try:
            transport = self._client and self._client.get_transport()
            return bool(
                transport
                and transport.is_active()
                and transport.is_authenticated()
            )
        except Exception:
            return False

    def _invalidate(self, client=None):
        """Retire a failed cached client without closing a newer replacement."""
        with self._connect_lock:
            if client is not None and self._client is not client:
                return
            failed = self._client
            self._client = None
            if failed:
                failed.close()

    def _log_proxy_error(self, proxy: dict, exc: Exception):
        """Log proxy connection errors to a log file"""
        log_path = SCRIPT_DIR / "proxy_error.log"
        try:
            import traceback
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {exc}\n")
                f.write(f"  Target: {self.user}@{self.host}:{self.port}\n")
                f.write(f"  Proxy: {proxy}\n")
                f.write(traceback.format_exc())
                f.write("---\n")
        except Exception:
            pass

    def download(self, remote_path: str, local_path: str, timeout: int = 300,
                 pattern: str = None, overwrite: bool = True, sudo: bool = False) -> dict:
        """Download a file or directory from remote to local via SFTP.

        If remote_path is a directory, recursively downloads all files.
        Optionally filter by regex pattern on filenames.

        Args:
            sudo: if True, read root-owned files by staging through /tmp:
                  `sudo cp -r <src> /tmp/<uuid>` -> `sudo chown <user>` -> SFTP get -> remove tmp.
        """
        self.connect()

        if self._path_blocked(local_path, "local"):
            raise ValueError(f"Local path not allowed: {local_path}")

        # When sudo, stage entire source tree to a user-readable temp location first
        actual_remote = remote_path
        tmp_stage = None
        if sudo:
            tmp_prefix = self._cmd("tmp_prefix")
            tmp_stage = f"{tmp_prefix}/.dl_stage_{uuid.uuid4().hex[:8]}"
            stage = self.run(
                self._cmd("cp_r", src=remote_path, dst=tmp_stage),
                timeout=timeout, sudo=True, internal=True,
            )
            if stage["code"] != 0:
                raise RuntimeError(f"sudo stage failed: {stage['stderr'].strip()[:200]}")
            if self._is_unix():
                self.run(self._cmd("chown_r", user=self.user, path=tmp_stage),
                          timeout=10, sudo=True, internal=True)
            actual_remote = tmp_stage

        try:
            # Check if (staged) remote is a directory
            sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
            sftp.get_channel().settimeout(timeout)
            remote_attr = sftp.stat(actual_remote)
            is_dir = (remote_attr.st_mode & 0o170000) == 0o040000

            if not is_dir:
                if self._path_blocked(remote_path, "remote"):
                    raise ValueError(f"Remote path not allowed: {remote_path}")
                local_path_obj = Path(local_path)
                if not overwrite and local_path_obj.exists():
                    raise FileExistsError(f"Local path already exists: {local_path}")
                local_dir = local_path_obj.parent
                if not local_dir.exists():
                    local_dir.mkdir(parents=True, exist_ok=True)
                sftp.get(actual_remote, str(local_path))
                sftp.close()
                return {
                    "stdout": f"Downloaded {remote_path} -> {local_path}\n",
                    "stderr": "",
                    "code": 0,
                    "remote_path": remote_path,
                    "local_path": local_path,
                }

            # Directory download
            if not overwrite and Path(local_path).exists():
                raise FileExistsError(f"Local path already exists: {local_path}")
            return self._download_dir(sftp, actual_remote, local_path, pattern, timeout,
                                      display_remote=remote_path)
        except (TimeoutError, socket.timeout) as exc:
            raise _operation_failure(
                "SFTP download", f"{remote_path} from {self.user}@{self.host}:{self.port}",
                timeout, exc,
            ) from exc
        finally:
            if tmp_stage:
                self.run(self._cmd("rm_dir", path=tmp_stage), timeout=15,
                         sudo=True, internal=True)

    _MAX_RECURSE_DEPTH = 48

    def _download_dir(self, sftp, remote_dir: str, local_dir: str,
                      pattern: str = None, timeout: int = 300,
                      display_remote: str = None) -> dict:
        """Recursively download a remote directory."""
        if pattern:
            pat = re.compile(pattern)
        display = display_remote or remote_dir

        total = 0
        errors = []

        def recurse(remote, local, depth=0):
            nonlocal total
            if depth > self._MAX_RECURSE_DEPTH:
                errors.append(f"  x {remote}: max recursion depth exceeded")
                return
            if not local.exists():
                local.mkdir(parents=True, exist_ok=True)
            entries = sftp.listdir_attr(remote)
            for entry in entries:
                name = entry.filename
                if name in (".", ".."):
                    continue
                rpath = f"{remote}/{name}"
                lpath = local / name
                is_dir = (entry.st_mode & 0o170000) == 0o040000
                if is_dir:
                    recurse(rpath, lpath, depth + 1)
                else:
                    if pattern and not pat.search(name):
                        continue
                    if self._path_blocked(rpath, "remote"):
                        errors.append(f"  x {rpath}: path not allowed")
                        continue
                    lpath.parent.mkdir(parents=True, exist_ok=True)
                    sftp.get(rpath, str(lpath))
                    total += 1

        local_path = Path(local_dir)
        if not local_path.exists():
            local_path.mkdir(parents=True, exist_ok=True)
        recurse(remote_dir, local_path)
        sftp.close()

        lines = [f"Downloaded {total} files from {display} -> {local_dir}\n"]
        if errors:
            lines.append("Errors:\n" + "\n".join(errors))
        return {
            "stdout": "".join(lines),
            "stderr": "",
            "code": 1 if errors else 0,
            "remote_path": display,
            "local_path": str(local_dir),
            "count": total,
        }

    def _path_blocked(self, path: str, kind: str) -> bool:
        """Return True when path falls OUTSIDE the configured allow-list.

        Remote paths are compared lexically with posixpath.normpath (they
        live on the server; resolving them locally would be wrong, e.g.
        '/var/log' -> 'C:\\var\\log' on Windows). Prefix matching is done
        with a directory boundary so '/allowed/dir' does not also permit
        '/allowed/dir-evil'.
        """
        allowed = self._allowed_paths.get(kind, [])
        if not allowed:
            return False
        if kind == "remote":
            resolved = posixpath.normpath(str(path).replace("\\", "/"))
            bases = [posixpath.normpath(str(p).replace("\\", "/")) for p in allowed]
            fold = str.casefold if os.sep == "\\" else (lambda s: s)
        else:
            resolved = str(Path(path).resolve())
            bases = [str(Path(p).resolve()) for p in allowed]
            fold = str.casefold if os.sep == "\\" else (lambda s: s)
        for base in bases:
            r, b = fold(resolved), fold(base)
            if r == b:
                return False
            b = b.rstrip("/")
            if b and (r + "/").startswith(b + "/"):
                return False
        return True

    def _check_command(self, cmd: str) -> str:
        """Check command against whitelist/blacklist, return template if set. Raises on violation."""
        # Check blacklist (blocked substring anywhere in the command)
        for pattern in self.blacklist:
            if pattern.search(cmd):
                raise ValueError(f"Command blocked by blacklist: {pattern.pattern}")
        # Check whitelist (the WHOLE command must match one pattern; search
        # semantics would let "ls; rm -rf /" pass a "^ls" rule)
        if self.whitelist:
            if not any(p.fullmatch(cmd) for p in self.whitelist):
                raise ValueError(f"Command not in whitelist: {cmd}")
        # Apply command template
        if self.command_template:
            cmd = self.command_template.replace("<command>", cmd)
        return cmd

    def _prepare_sudo(self, cmd: str):
        """Wrap a command to run via sudo, returning (command, stdin_secret).

        The password is delivered over the channel stdin (``sudo -S``) so it
        never appears in the remote process argv (no ``ps`` exposure).
        The command always runs through ``bash -c``; on Windows servers this
        requires bash to be available (e.g., Git Bash or WSL).
        """
        if not self.sudo_password:
            raise ValueError("sudo_password not configured for this server")
        return f"sudo -S -p '' bash -c {shlex.quote(cmd)}", self.sudo_password

    def _strip_sudo_noise(self, result: dict) -> dict:
        """Remove '[sudo]' password prompt lines from output."""
        for stream in ("stdout", "stderr"):
            result[stream] = "\n".join(
                l for l in result[stream].split("\n")
                if "Password:" not in l and "[sudo:" not in l
            ).strip("\n")
        return result

    def run(self, cmd: str, timeout: int = None, sudo: bool = False,
            stream_cb: Any = None, internal: bool = False) -> dict:
        """Execute a remote command, returns {stdout, stderr, code}.

        Commands run concurrently on independent SSH channels over the
        shared transport, so a long-running command on this server never
        blocks another thread's command. Reconnect/invalidation races are
        handled per-channel (each command owns its own channel).

        Args:
            cmd: shell command to execute
            timeout: seconds before giving up (defaults to server timeout from YAML)
            sudo: if True, run via ``sudo -S`` with the password fed over stdin
            stream_cb: optional callback(chunk: str, is_stderr: bool) for real-time output
            internal: True for code-generated helper commands (skips
                whitelist/blacklist filtering; never set from external input)
        """
        return self._run(cmd, timeout, sudo, stream_cb, internal)

    def _run(self, cmd: str, timeout: int = None, sudo: bool = False,
             stream_cb: Any = None, internal: bool = False) -> dict:
        if timeout is None or timeout <= 0:
            timeout = self.timeout
        cmd = _repair_surrogateescaped_command(cmd)
        if not internal:
            cmd = self._check_command(cmd)
        sudo_secret = None
        if sudo:
            cmd, sudo_secret = self._prepare_sudo(cmd)
        ch = None
        client = None
        for attempt in range(2):
            self.connect()
            client = self._client
            try:
                transport = client.get_transport()
                if not transport or not transport.is_active() or not transport.is_authenticated():
                    raise paramiko.SSHException("cached SSH transport is not usable")
                ch = transport.open_session(timeout=min(timeout, self.timeout))
                ch.settimeout(timeout)
                ch.exec_command(cmd)
                if sudo_secret is not None:
                    ch.sendall((sudo_secret + "\n").encode("utf-8"))
                    ch.shutdown_write()
                break
            except (EOFError, OSError, paramiko.SSHException):
                if ch:
                    ch.close()
                    ch = None
                self._invalidate(client)
                if attempt or not self.auto_reconnect:
                    self._record_connect_failure()
                    raise

        if ch is None:
            raise paramiko.SSHException("failed to open SSH command channel")

        stdout_data, stderr_data = [], []
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if ch.recv_ready():
                    chunk = ch.recv(65536)
                    stdout_data.append(chunk)
                    if stream_cb:
                        stream_cb(chunk.decode(errors="replace"), False)
                if ch.recv_stderr_ready():
                    chunk = ch.recv_stderr(65536)
                    stderr_data.append(chunk)
                    if stream_cb:
                        stream_cb(chunk.decode(errors="replace"), True)
                if ch.exit_status_ready() and not ch.recv_ready() and not ch.recv_stderr_ready():
                    break
                time.sleep(0.05)
        except (EOFError, OSError, paramiko.SSHException):
            self._invalidate(client)
            raise

        # Give the channel a short grace period to flush its tail, then drain.
        if not ch.exit_status_ready():
            time.sleep(0.2)
        while ch.recv_ready():
            chunk = ch.recv(65536)
            stdout_data.append(chunk)
            if stream_cb:
                stream_cb(chunk.decode(errors="replace"), False)
        while ch.recv_stderr_ready():
            chunk = ch.recv_stderr(65536)
            stderr_data.append(chunk)
            if stream_cb:
                stream_cb(chunk.decode(errors="replace"), True)

        rc = ch.recv_exit_status() if ch.exit_status_ready() else -1
        ch.close()
        result = {
            "stdout": b"".join(stdout_data).decode(errors="replace"),
            "stderr": b"".join(stderr_data).decode(errors="replace"),
            "code": rc,
        }
        if rc == -1:
            result["stderr"] = (
                result["stderr"] +
                f"\n[ssh-alias-mcp] command timed out after {timeout}s; "
                "the channel was closed but the remote process may still be running"
            ).strip("\n")
        return self._strip_sudo_noise(result) if sudo else result

    def upload_file(self, local_path: str, remote_path: str, timeout: int = 300,
                    overwrite: bool = True, sudo: bool = False,
                    executable: bool = False) -> dict:
        """Upload one local file to an explicit remote path via SFTP."""
        self.connect()
        if self._path_blocked(local_path, "local"):
            raise ValueError(f"Local path not allowed: {local_path}")
        if self._path_blocked(remote_path, "remote"):
            raise ValueError(f"Remote path not allowed: {remote_path}")
        source = Path(local_path)
        if not source.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        if not overwrite:
            check = self.run(self._cmd("file_exists", path=remote_path), timeout=5,
                             sudo=sudo, internal=True)
            if check["code"] == 0:
                raise FileExistsError(f"Remote file already exists: {remote_path}")
        remote_parent = str(Path(remote_path).parent)
        self.run(self._cmd("mkdir", path=remote_parent), timeout=10, sudo=sudo,
                 internal=True)
        try:
            sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
            sftp.get_channel().settimeout(timeout)
            if sudo:
                tmp_prefix = self._cmd("tmp_prefix")
                tmp_path = f"{tmp_prefix}/.staging_{uuid.uuid4().hex[:8]}_{source.name}"
                sftp.put(str(source), tmp_path)
                sftp.close()
                installed = self.run(
                    self._cmd("install", tmp=tmp_path, target=remote_path),
                    timeout=15, sudo=True, internal=True,
                )
                if installed["code"] != 0:
                    return installed
                # The ``install`` template already applies the ownership/mode
                # semantics (preserve on overwrite, parent-owner + 755 on
                # create); a follow-up chmod would clobber the preserved mode.
            else:
                sftp.put(str(source), remote_path)
                if executable and self._is_unix():
                    sftp.chmod(remote_path, 0o755)
                sftp.close()
        except (TimeoutError, socket.timeout) as exc:
            raise _operation_failure(
                "SFTP upload", f"{local_path} -> {remote_path}", timeout, exc,
            ) from exc
        return {
            "stdout": f"Uploaded {local_path} -> {remote_path}\n",
            "stderr": "", "code": 0, "remote_path": remote_path,
        }

    def upload_script(self, local_path: str, script_name: str = None,
                      run_immediately: bool = False, timeout: int = 300,
                      overwrite: bool = True, sudo: bool = False) -> dict:
        """Upload a local script to the remote scripts_dir, optionally run immediately.

        Args:
            sudo: if True, write the script as root via stage-via-/tmp
                  (SFTP -> tmp -> sudo install, which sets owner/mode).
                  When run_immediately=True, also execute the script as root.
        """
        if not script_name:
            script_name = Path(local_path).name
        script_name = _safe_relpath(script_name, "script_name")
        remote_path = f"{self.scripts_dir}/{script_name}"
        result = self.upload_file(local_path, remote_path, timeout, overwrite, sudo, executable=True)
        result["stdout"] = f"Script uploaded to {remote_path}\n" if result["code"] == 0 else result["stdout"]

        if run_immediately:
            run_cmd = self._cmd("run_script", path=remote_path)
            run_result = self.run(run_cmd, timeout=timeout, sudo=sudo)
            result["run_stdout"] = run_result["stdout"]
            result["run_stderr"] = run_result["stderr"]
            result["run_code"] = run_result["code"]
            result["code"] = run_result["code"]

        return result

    def download_file(self, remote_path: str, local_path: str, timeout: int = 300,
                      overwrite: bool = True, sudo: bool = False) -> dict:
        """Download one remote file to an explicit local path via SFTP.

        Counterpart of upload_file: same allow-list checks, sudo staging
        and timeout diagnostics, all provided by the download() primitive.
        """
        return self.download(
            remote_path, local_path, timeout=timeout,
            overwrite=overwrite, sudo=sudo,
        )

    def download_script(self, script_name: str, local_path: str, timeout: int = 300,
                        overwrite: bool = True, sudo: bool = False) -> dict:
        """Download a script from scripts_dir, reusing the single-file downloader."""
        script_name = _safe_relpath(script_name, "script_name")
        return self.download_file(
            f"{self.scripts_dir}/{script_name}", local_path, timeout=timeout,
            overwrite=overwrite, sudo=sudo,
        )

    def list_scripts(self, sudo: bool = False) -> dict:
        """List uploaded scripts on the remote server"""
        self.connect()
        return self.run(self._cmd("list_dir", path=self.scripts_dir),
                        timeout=30, sudo=sudo, internal=True)

    def run_script(self, script_name: str, timeout: int = 300, sudo: bool = False, stream_cb: Any = None) -> dict:
        """Run an already-uploaded script.

        Args:
            script_name: file name under scripts_dir
            timeout: command timeout in seconds
            sudo: if True, execute as root (requires sudo_password configured)
            stream_cb: optional callback(chunk: str, is_stderr: bool) for real-time output
        """
        self.connect()
        script_name = _safe_relpath(script_name, "script_name")
        remote_path = f"{self.scripts_dir}/{script_name}"
        return self.run(self._cmd("run_script", path=remote_path),
                        timeout=timeout, sudo=sudo, stream_cb=stream_cb,
                        internal=True)

    def _cmd(self, name: str, **kwargs) -> str:
        """Format a command template, shell-quoting every placeholder value."""
        tpl = self._tpl[name]
        if not tpl:
            return ""
        if not kwargs:
            return tpl
        return tpl.format(**{k: self._quote_arg(v, k) for k, v in kwargs.items()})

    def _quote_arg(self, value: Any, key: str = "") -> str:
        """Quote one command-argument value for the server's shell."""
        if isinstance(value, int):
            return str(value)
        s = str(value)
        if _FORBIDDEN_CHARS_RE.search(s) or "\n" in s or "\r" in s:
            raise ValueError(f"Unsafe character in command argument: {s!r}")
        if self.shell == "bash":
            return shlex.quote(s)
        if self.shell == "cmd":
            if any(c in s for c in '"&|<>^'):
                raise ValueError(f"Unsafe character in cmd argument: {s!r}")
            # '%' allows server-side env expansion; only our own staging
            # paths (built from the configured tmp_prefix, e.g. %TEMP%)
            # may contain it, so user-supplied values cannot smuggle
            # expansion-based injection.
            if "%" in s and not s.startswith(str(self._tpl["tmp_prefix"])):
                raise ValueError(f"Unsafe character in cmd argument: {s!r}")
            return f'"{s}"'
        # powershell
        if "`" in s:
            raise ValueError(f"Unsafe character in powershell argument: {s!r}")
        return "'" + s.replace("'", "''") + "'"

    def _scripts_base(self) -> Path:
        """Get the base directory for resolving relative script paths.
        Resolution:
          1. If scripts_local_dir is set, use {yml_dir}/{scripts_local_dir}
          2. Otherwise fall back to yml file's directory.
        """
        base = Path(self._yml_path).parent.resolve() if self._yml_path else Path.cwd()
        if self.scripts_local_dir:
            return (base / self.scripts_local_dir).resolve()
        return base

    def _is_unix(self) -> bool:
        """Check if shell is Unix-like."""
        return self.shell == "bash"

    def upload_all_scripts(self, sudo: bool = False, overwrite: bool = True) -> dict:
        """Upload all scripts referenced by alias 'script' fields.

        Args:
            sudo: if True, uploads via stage-via-/tmp + sudo mv (for root-owned dirs).
            overwrite: if False, skip files that already exist on remote (default True).
        """
        script_aliases = [a for a in self.aliases if a.get("script")]
        if not script_aliases:
            return {"stdout": "No alias with 'script' field found.\n", "stderr": "", "code": 0}

        msg, fail = [], []
        seen_hashes = {}
        base = self._scripts_base()
        for a in script_aliases:
            src = (base / a["script"]).resolve()
            if not src.is_file():
                fail.append(f"  x {a['script']}: file not found at {src}")
                continue
            content_hash = hashlib.md5(src.read_bytes()).hexdigest()
            if content_hash in seen_hashes:
                prev_alias = seen_hashes[content_hash]
                fail.append(f"  x {a['script']}: duplicate content of {prev_alias}")
                continue
            seen_hashes[content_hash] = a["name"]
            remote_name = src.name
            if src.is_relative_to(base):
                rel = src.relative_to(base).as_posix()
                remote = f"{self.scripts_dir}/{_safe_relpath(rel, 'script')}"
            else:
                remote = f"{self.scripts_dir}/external/{_safe_relpath(remote_name, 'script')}"
            try:
                result = self.upload_file(
                    str(src), remote, timeout=300, overwrite=overwrite,
                    sudo=sudo, executable=True,
                )
                if result["code"] == 0:
                    msg.append(f"  + {remote}")
                else:
                    fail.append(
                        f"  x {a['script']}: upload failed: "
                        f"{result.get('stderr', result.get('stdout', 'unknown error'))[:80]}"
                    )
            except Exception as exc:
                fail.append(f"  x {a['script']}: {exc}")

        out = f"Upload done ({len(msg)}/{len(script_aliases)})\n"
        out += "\n".join(msg + fail) + "\n"
        return {"stdout": out, "stderr": "", "code": 0 if not fail else 1}

    def run_alias(self, name: str, stream_cb: Any = None) -> dict:
        """Run an alias-defined quick command"""
        target = None
        for a in self.aliases:
            if a.get("name") == name:
                target = a
                break
        if not target:
            raise ValueError(f"Alias not found: {name}")
        timeout = int(target.get("timeout", 300))
        sudo = target.get("sudo", False)

        # inline type: execute command directly on remote
        if "inline" in target:
            return self.run(target["inline"], timeout=timeout, sudo=sudo, stream_cb=stream_cb)

        # script type: upload local .sh first, then execute remotely
        local_file = (self._scripts_base() / _safe_relpath(target["script"], "script")).resolve()
        if not local_file.is_file():
            raise FileNotFoundError(f"Script not found: {local_file}")
        base = self._scripts_base()
        if local_file.is_relative_to(base):
            rel = local_file.relative_to(base).as_posix()
            remote_path = f"{self.scripts_dir}/{_safe_relpath(rel, 'script')}"
        else:
            remote_path = (
                f"{self.scripts_dir}/external/"
                f"{_safe_relpath(local_file.name, 'script')}"
            )

        # Check if already uploaded (skip re-upload). Use sudo=sudo so we can stat
        # files owned by root when the alias targets a root-owned scripts_dir.
        result = self.run(self._cmd("file_exists", path=remote_path),
                         timeout=5, sudo=sudo, internal=True)
        if result["code"] != 0:
            self.upload_script(str(local_file), script_name=local_file.name,
                               timeout=30, sudo=sudo)

        return self.run(self._cmd("run_script", path=remote_path), timeout=timeout, sudo=sudo, stream_cb=stream_cb)

    def list_aliases(self) -> list:
        """List all configured aliases"""
        aliases = []
        for a in self.aliases:
            item = {
                "name": a.get("name", ""),
                "type": "script" if "script" in a else "inline",
                "desc": a.get("desc", ""),
                "timeout": a.get("timeout", 300),
                "sudo": a.get("sudo", False),
            }
            if "inline" in a:
                item["inline"] = a["inline"]
            if "script" in a:
                item["script"] = a["script"]
            aliases.append(item)
        return aliases

    def close(self):
        """Close the SSH connection"""
        with self._connect_lock:
            self._closed = True
            self._reconnect_wakeup.set()
            if self._client:
                self._client.close()
                self._client = None


class ConnectionPool:
    """SSH connection pool — manages connection reuse by server name"""

    def __init__(self):
        self._pool: Dict[str, SSHConnection] = {}
        self._lock = threading.Lock()

    def get(self, server_name: str) -> SSHConnection:
        """Get or create an SSHConnection (lazy — call conn.connect() on first use).

        The whole build is serialised so concurrent callers for the same
        server can never create two SSHConnection objects (each would hold
        its own transport). The returned connection supports concurrent
        commands on independent channels.
        """
        key, _ = self._config_target(server_name)
        with self._lock:
            if key in self._pool:
                return self._pool[key]
            yml_path = self._resolve_config_path(key)
            data = self._load_config(key)
            conn = SSHConnection(data["server"], data.get("aliases", []), yml_path)
            self._pool[key] = conn
            return conn

    def create_server(self, server_name: str, config: dict) -> dict:
        """Create a server YAML file without overwriting an existing config."""
        key, config_path = self._config_target(server_name)
        if config_path.exists():
            raise FileExistsError(f"Server config already exists: {config_path}")
        self._validate_server_config(config)
        self._write_config(config_path, config)
        self._discard(key)
        return {"name": key, "path": str(config_path), "created": True}

    def update_server(self, server_name: str, config: dict, replace: bool = False) -> dict:
        """Recursively merge a server config patch, or replace the whole config."""
        key, config_path = self._config_target(server_name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        current = load_yaml(str(config_path))
        updated = config if replace else self._deep_merge(current, config)
        self._validate_server_config(updated)
        self._write_config(config_path, updated)
        self._discard(key)
        return {
            "name": key,
            "path": str(config_path),
            "updated": True,
            "replace": replace,
        }

    def copy_server(self, source_server: str, target_server: str) -> dict:
        """Copy a server YAML file to a new name without overwriting."""
        source_key, source_path = self._config_target(source_server)
        target_key, target_path = self._config_target(target_server)
        if not source_path.exists():
            raise FileNotFoundError(f"Server config not found: {source_path}")
        if target_path.exists():
            raise FileExistsError(f"Server config already exists: {target_path}")
        config = load_yaml(str(source_path))
        self._validate_server_config(config)
        self._copy_config_file(source_path, target_path)
        self._discard(target_key)
        return {
            "source": source_key,
            "name": target_key,
            "path": str(target_path),
            "copied": True,
        }

    def delete_server(self, server_name: str) -> dict:
        """Delete one server YAML file."""
        key, config_path = self._config_target(server_name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        config_path.unlink()
        self._discard(key)
        return {"name": key, "path": str(config_path), "deleted": True}

    @staticmethod
    def _config_target(server_name: str):
        if not isinstance(server_name, str):
            raise ValueError("Server name must be a string")
        name = server_name.strip()
        if name.lower().endswith((".yml", ".yaml")):
            name = name.rsplit(".", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(
                "Invalid server name; use letters, numbers, dot, underscore, or hyphen"
            )
        key = name.lower()
        return key, SERVERS_DIR / f"{key}.yml"

    @staticmethod
    def _validate_server_config(config: dict):
        if not isinstance(config, dict):
            raise ValueError("Server config must be an object")
        server = config.get("server")
        if not isinstance(server, dict):
            raise ValueError("Server config must contain a 'server' object")
        if not config.get("extends"):
            for field in ("host", "user"):
                if not isinstance(server.get(field), str) or not server[field].strip():
                    raise ValueError(
                        f"Server config must contain a non-empty server.{field}"
                    )
        aliases = config.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            raise ValueError("Server config 'aliases' must be an array")
        if aliases is not None:
            for index, alias in enumerate(aliases):
                if not isinstance(alias, dict):
                    raise ValueError(f"Alias at index {index} must be an object")
                if not isinstance(alias.get("name"), str) or not alias["name"].strip():
                    raise ValueError(f"Alias at index {index} must have a non-empty name")

    @staticmethod
    def _deep_merge(current: dict, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Server config patch must be an object")
        merged = dict(current)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = ConnectionPool._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _atomic_write(target_path: Path, write_fn, mode: int):
        """Write through a temp file and atomically replace ``target_path``."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            write_fn(temp_path)
            temp_path.chmod(mode)
            os.replace(temp_path, target_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @classmethod
    def _write_config(cls, config_path: Path, config: dict, mode: int = None):
        text = yaml.safe_dump(
            config,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        file_mode = mode
        if file_mode is None:
            file_mode = (
                config_path.stat().st_mode & 0o777
                if config_path.exists()
                else 0o600
            )
        cls._atomic_write(
            config_path,
            lambda temp: temp.write_text(text, encoding="utf-8"),
            file_mode,
        )

    @classmethod
    def _copy_config_file(cls, source_path: Path, target_path: Path):
        data = source_path.read_bytes()
        cls._atomic_write(
            target_path,
            lambda temp: temp.write_bytes(data),
            source_path.stat().st_mode & 0o777,
        )

    def _discard(self, key: str):
        with self._lock:
            conn = self._pool.pop(key, None)
        if conn is not None:
            conn.close()

    def close_all(self):
        """Close and remove all cached connections."""
        with self._lock:
            connections = list(self._pool.values())
            self._pool.clear()
        for conn in connections:
            conn.close()

    def _resolve_config_path(self, name: str) -> str:
        _, config_path = self._config_target(name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        return str(config_path)

    def _load_config(self, name: str) -> dict:
        config_path = SERVERS_DIR / f"{name}.yml"
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        cfg = load_yaml(str(config_path))

        # Handle 'extends' inheritance
        extends = cfg.pop("extends", [])
        if extends:
            base_cfg = dict(cfg)
            base_aliases = base_cfg.get("aliases", [])
            for ext_file in extends:
                ext_path = config_path.parent / ext_file
                if not ext_path.exists():
                    print(f"Warning: extends file not found: {ext_path}", file=sys.stderr)
                    continue
                ext_cfg = load_yaml(str(ext_path))
                # Merge server fields: base + local overrides
                ext_server = ext_cfg.get("server", {})
                if ext_server:
                    merged_server = {**ext_server, **base_cfg.get("server", {})}
                    base_cfg["server"] = merged_server
                # Merge aliases: local takes priority
                ext_aliases = ext_cfg.get("aliases", [])
                if ext_aliases:
                    existing_names = {a["name"] for a in base_cfg.get("aliases", [])}
                    base_cfg["aliases"] = [
                        a for a in ext_aliases if a.get("name") not in existing_names
                    ] + base_cfg.get("aliases", [])
                # Merge security fields
                for key in ["whitelist", "blacklist", "command_template",
                            "allowed_local_paths", "allowed_remote_paths", "scripts_dir"]:
                    if key not in base_cfg and key in ext_cfg:
                        base_cfg[key] = ext_cfg[key]
                # Merge proxy config
                if "proxy" in ext_cfg and "proxy" not in base_cfg:
                    base_cfg["proxy"] = ext_cfg["proxy"]
            cfg = base_cfg

        return cfg

    def list_servers(self) -> list:
        """List all configured servers"""
        if not SERVERS_DIR.exists():
            return []
        servers = []
        for f in sorted(SERVERS_DIR.glob("*.yml")):
            try:
                name = f.stem
                cfg = self._load_config(name)
                srv = cfg["server"]
                info = {
                    "name": name,
                    "host": srv["host"],
                    "port": srv.get("port", 22),
                    "user": srv["user"],
                    "display": srv.get("name", name),
                    "desc": srv.get("desc", ""),
                    "system": srv.get("system", ""),
                    "group": srv.get("group", name),
                    "shell": srv.get("shell", "bash"),
                }
                aliases = cfg.get("aliases", [])
                if aliases:
                    info["aliases"] = len(aliases)
                servers.append(info)
            except Exception as e:
                servers.append({"name": f.stem, "error": str(e)})
        servers.sort(key=lambda s: (s.get("group", ""), s.get("name", "")))
        for i, srv in enumerate(servers, start=1):
            srv["index"] = i
        return servers

    @classmethod
    def _to_key(cls, name: str) -> str:
        """Normalise a server name to its pool key (see ``_config_target``)."""
        return cls._config_target(name)[0]


# Global connection pool instance
pool = ConnectionPool()
