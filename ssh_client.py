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
    claude mcp add server-management python <path>/mcp_server.py
"""
import io
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


def load_yaml(path: str) -> dict:
    """Load a YAML config file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    text = p.read_text(encoding="utf-8-sig")
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error: {e}")


def load_global_config() -> dict:
    """Load global configuration (proxy, timeout, etc.)"""
    if not CONFIG_PATH.exists():
        return {"proxy": {"enabled": False}, "server": {"timeout": 30}}
    try:
        return load_yaml(str(CONFIG_PATH))
    except Exception:
        return {"proxy": {"enabled": False}, "server": {"timeout": 30}}


global_config: dict = load_global_config()

# servers_dir can be configured in config.yaml (absolute or relative to config file)
_servers_dir_cfg = global_config.get("servers_dir", "servers")
SERVERS_DIR = (CONFIG_PATH.parent / _servers_dir_cfg).resolve() if _servers_dir_cfg else SCRIPT_DIR / "servers"


class SSHConnection:
    """SSH connection wrapper — proxy/direct auto-fallback, script upload & execution, command execution"""

    def __init__(self, cfg: dict, aliases: list = None,
                 yml_path: str = None):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 22))
        self.user = cfg["user"]
        self.password = cfg.get("password")
        self.key_path = cfg.get("key")
        self.key_password = cfg.get("key_password")
        self.sudo_password = cfg.get("sudo_password") or cfg.get("password")
        self.timeout = int(cfg.get("timeout",
                                   global_config.get("server", {}).get("timeout", 30)))
        self.scripts_dir = cfg.get("scripts_dir",
                                   f"/home/{cfg.get('user', 'root')}/scripts")
        self.aliases = aliases or []
        self._yml_path = yml_path

        # Proxy config: per-server takes priority, then global fallback
        self.proxy = cfg.get("proxy")
        if not self.proxy:
            proxy_cfg = global_config.get("proxy", {})
            if proxy_cfg.get("enabled"):
                self.proxy = {
                    "host": proxy_cfg.get("host", "127.0.0.1"),
                    "port": proxy_cfg.get("port", 1080)
                }

        self._client: Optional[paramiko.SSHClient] = None
        self._last_used = 0

    def connect(self):
        """Establish connection (reuses active connection if alive)"""
        # Dynamically reload proxy config
        current_proxy_cfg = load_global_config().get("proxy", {})
        if current_proxy_cfg.get("enabled"):
            proxy = {
                "host": current_proxy_cfg.get("host", "127.0.0.1"),
                "port": current_proxy_cfg.get("port", 1080)
            }
            if self.proxy:
                proxy = self.proxy
        elif self.proxy:
            proxy = self.proxy
        else:
            proxy = None

        if self._client and self._is_alive():
            self._last_used = time.time()
            return
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        # Check pysocks availability
        if proxy:
            try:
                import socks  # noqa: F401
            except ImportError:
                raise RuntimeError("Proxy enabled but pysocks not installed. Run: pip install pysocks")

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

    def _connect_proxy_with(self, proxy: dict):
        """Connect via SOCKS5 proxy"""
        import socks
        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, proxy["host"], int(proxy["port"]))
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))

        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        t = paramiko.Transport(sock)
        kw = self._get_auth()
        t.connect(username=self.user, **kw)
        t.set_keepalive(60)
        c._transport = t
        self._client = c
        self._last_used = time.time()

    def _connect_direct(self):
        """Connect directly (no proxy)"""
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw = self._get_auth()
        c.connect(self.host, port=self.port, username=self.user, timeout=self.timeout, **kw)
        c.get_transport().set_keepalive(60)
        self._client = c
        self._last_used = time.time()

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
            return bool(self._client and self._client.get_transport().is_active())
        except Exception:
            return False

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

    def run(self, cmd: str, timeout: int = 300) -> dict:
        """Execute a remote command, returns {stdout, stderr, code}"""
        self.connect()
        ch = self._client.get_transport().open_session()
        ch.settimeout(timeout)
        ch.exec_command(cmd)

        stdout_data, stderr_data = [], []
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ch.recv_ready():
                stdout_data.append(ch.recv(65536))
            if ch.recv_stderr_ready():
                stderr_data.append(ch.recv_stderr(65536))
            if ch.exit_status_ready() and not ch.recv_ready() and not ch.recv_stderr_ready():
                break
            time.sleep(0.05)

        while ch.recv_ready():
            stdout_data.append(ch.recv(65536))
        while ch.recv_stderr_ready():
            stderr_data.append(ch.recv_stderr(65536))

        rc = ch.recv_exit_status() if ch.exit_status_ready() else -1
        return {
            "stdout": b"".join(stdout_data).decode(errors="replace"),
            "stderr": b"".join(stderr_data).decode(errors="replace"),
            "code": rc,
        }

    def run_sudo(self, cmd: str, timeout: int = 300) -> dict:
        """Execute a command as root (auto-pipes sudo password)"""
        if not self.sudo_password:
            raise ValueError("sudo_password not configured for this server")
        # Use echo | sudo -S to avoid interactive password prompt
        safe_cmd = cmd.replace("'", "'\\''")
        sudo_cmd = f"echo '{self.sudo_password}' | sudo -S bash -c '{safe_cmd}'"
        result = self.run(sudo_cmd, timeout=timeout)
        # Strip sudo password prompts from output (may appear in stdout or stderr)
        result["stdout"] = "\n".join(
            l for l in result["stdout"].split("\n")
            if "Password:" not in l and "[sudo:" not in l
        ).strip("\n")
        result["stderr"] = "\n".join(
            l for l in result["stderr"].split("\n")
            if "Password:" not in l and "[sudo:" not in l
        ).strip("\n")
        return result

    def upload_and_run(self, local_path: str, remote_dir: str = "/tmp",
                       shell: str = "bash", timeout: int = 300) -> dict:
        """Upload a local script, execute it on the remote, then auto-cleanup"""
        self.connect()
        script_name = f".remote_script_{uuid.uuid4().hex[:8]}.sh"
        remote_path = f"{remote_dir}/{script_name}"

        try:
            if not Path(local_path).exists():
                raise FileNotFoundError(f"Local script not found: {local_path}")
            script_content = Path(local_path).read_bytes()

            sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
            sftp.putfo(io.BytesIO(script_content), remote_path)
            sftp.chmod(remote_path, 0o755)
            sftp.close()

            cmd = f"{shell} {remote_path}"
            result = self.run(cmd, timeout=timeout)
            return result
        finally:
            try:
                self.run(f"rm -f {remote_path}", timeout=10)
            except Exception:
                pass

    def upload_script(self, local_path: str, script_name: str = None,
                      run_immediately: bool = False, timeout: int = 300) -> dict:
        """Upload a local script to the remote scripts_dir, optionally run immediately"""
        self.connect()

        if not Path(local_path).exists():
            raise FileNotFoundError(f"Local script not found: {local_path}")
        script_content = Path(local_path).read_bytes()

        if not script_name:
            script_name = Path(local_path).name
        remote_path = f"{self.scripts_dir}/{script_name}"

        self.run(f"mkdir -p {self.scripts_dir}", timeout=10)

        sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
        sftp.putfo(io.BytesIO(script_content), remote_path)
        sftp.chmod(remote_path, 0o755)
        sftp.close()

        result = {
            "stdout": f"Script uploaded to {remote_path}\n",
            "stderr": "",
            "code": 0,
            "remote_path": remote_path
        }

        if run_immediately:
            run_result = self.run(f"bash {remote_path}", timeout=timeout)
            result["run_stdout"] = run_result["stdout"]
            result["run_stderr"] = run_result["stderr"]
            result["run_code"] = run_result["code"]
            result["code"] = run_result["code"]

        return result

    def list_scripts(self) -> dict:
        """List uploaded scripts on the remote server"""
        self.connect()
        return self.run(f"ls -la {self.scripts_dir}/", timeout=30)

    def run_script(self, script_name: str, timeout: int = 300) -> dict:
        """Run an already-uploaded script"""
        self.connect()
        remote_path = f"{self.scripts_dir}/{script_name}"
        return self.run(f"bash {remote_path}", timeout=timeout)

    def upload_all_scripts(self) -> dict:
        """Upload all .sh files referenced by alias 'script' fields"""
        script_aliases = [a for a in self.aliases if a.get("script")]
        if not script_aliases:
            return {"stdout": "No alias with 'script' field found.\n", "stderr": "", "code": 0}

        self.connect()
        self.run(f"mkdir -p {self.scripts_dir}", timeout=10)

        sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
        msg, fail = [], []
        for a in script_aliases:
            src = Path(a["script"])
            remote_name = src.name  # script filename goes directly under scripts_dir
            remote = f"{self.scripts_dir}/{remote_name}"
            try:
                if not src.is_file():
                    fail.append(f"  x {a['script']}: file not found")
                    continue
                sftp.put(str(src), remote)
                sftp.chmod(remote, 0o755)
                msg.append(f"  + {remote_name}")
            except Exception as e:
                fail.append(f"  x {a['script']}: {e}")
        sftp.close()

        out = f"Upload done ({len(msg)}/{len(script_aliases)})\n"
        out += "\n".join(msg + fail) + "\n"
        return {"stdout": out, "stderr": "", "code": 0 if not fail else 1}

    def run_alias(self, name: str) -> dict:
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
            cmd = target["inline"]
            if sudo:
                return self.run_sudo(cmd, timeout=timeout)
            return self.run(cmd, timeout=timeout)

        # script type: upload local .sh first, then execute remotely
        local_file = Path(target["script"])
        if not local_file.is_file():
            raise FileNotFoundError(f"Script not found: {local_file}")
        remote_path = f"{self.scripts_dir}/{local_file.name}"
        cmd = f"bash {remote_path}"

        # Check if already uploaded (skip re-upload)
        try:
            self.run(f"test -f {remote_path}", timeout=5)
        except Exception:
            # Not uploaded yet, upload now
            self.upload_script(str(local_file), script_name=local_file.name, timeout=30)

        if sudo:
            return self.run_sudo(cmd, timeout=timeout)
        return self.run(cmd, timeout=timeout)

    def list_aliases(self) -> list:
        """List all configured aliases"""
        return [{"name": a.get("name", ""), "type": "script" if "script" in a else "inline",
                 "desc": a.get("desc", ""), "timeout": a.get("timeout", 300)}
                for a in self.aliases]

    def close(self):
        """Close the SSH connection"""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


class ConnectionPool:
    """SSH connection pool — manages connection reuse by server name"""
    _lock = threading.Lock()

    def __init__(self):
        self._pool: Dict[str, SSHConnection] = {}

    def get(self, server_name: str) -> SSHConnection:
        """Get or create an SSHConnection (reuses active connection)"""
        key = self._to_key(server_name)
        yml_path = self._resolve_config_path(key)
        data = self._load_config(key)
        cfg = data["server"]
        aliases = data.get("aliases", [])
        with self._lock:
            if key in self._pool:
                conn = self._pool[key]
                conn.connect()
                return conn
            conn = SSHConnection(cfg, aliases, yml_path)
            conn.connect()
            self._pool[key] = conn
            return conn

    def _resolve_config_path(self, name: str) -> str:
        config_path = SERVERS_DIR / f"{name}.yml"
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
            for ext_file in extends:
                ext_path = config_path.parent / ext_file
                if not ext_path.exists():
                    print(f"Warning: extends file not found: {ext_path}", file=sys.stderr)
                    continue
                ext_cfg = load_yaml(str(ext_path))
                # Merge inherited aliases (local takes priority)
                ext_aliases = ext_cfg.get("aliases", [])
                if ext_aliases:
                    existing_names = {a["name"] for a in cfg.get("aliases", [])}
                    cfg["aliases"] = [
                        a for a in ext_aliases if a.get("name") not in existing_names
                    ] + cfg.get("aliases", [])
                # Non-aliases fields: use local file values

        return cfg

    def list_servers(self) -> list:
        """List all configured servers"""
        if not SERVERS_DIR.exists():
            return []
        servers = []
        for f in sorted(SERVERS_DIR.glob("*.yml")):
            try:
                cfg = load_yaml(str(f))
                srv = cfg["server"]
                info = {
                    "name": f.stem,
                    "host": srv["host"],
                    "port": srv.get("port", 22),
                    "user": srv["user"],
                    "display": srv.get("name", f.stem),
                    "desc": srv.get("desc", ""),
                    "system": srv.get("system", ""),
                }
                aliases = cfg.get("aliases", [])
                if aliases:
                    info["aliases"] = len(aliases)
                servers.append(info)
            except Exception as e:
                servers.append({"name": f.stem, "error": str(e)})
        return servers

    @staticmethod
    def _to_key(name: str) -> str:
        return name.replace(".yml", "").replace(".yaml", "").strip().lower()


# Global connection pool instance
pool = ConnectionPool()
