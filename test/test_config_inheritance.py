"""Offline coverage ported from test_advanced.py: extends inheritance,
ConnectionPool key normalization / list_servers ordering, and error paths
(FileNotFoundError, sudo password missing, nonexistent server/alias).

All tests run against temporary servers directories or the local config only —
no SSH connection is ever opened. The live-only cases that stayed behind in
test_advanced.py (real-server field checks, MCP ssh_run tool call, CLI long
flags, upload of a nonexistent file) require a reachable server and are
carried by the live E2E suite.
"""
import json

import pytest
import yaml

import ssh_client
from ssh_client import ConnectionPool, SSHConnection


@pytest.fixture
def servers_dir(tmp_path, monkeypatch):
    """Point ssh_client.SERVERS_DIR at an empty temp directory."""
    dir_path = tmp_path / "servers"
    dir_path.mkdir()
    monkeypatch.setattr(ssh_client, "SERVERS_DIR", dir_path)
    return dir_path


@pytest.fixture
def pool(servers_dir):
    conn_pool = ConnectionPool()
    yield conn_pool
    conn_pool.close_all()


def _write(dir_path, name, config):
    (dir_path / f"{name}.yml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ────────────────────────────────────────────────────────────────────
# ConnectionPool — key normalization (test_advanced §2)
# ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("test-server", "test-server"),        # lowercase passthrough
        ("Test-Server", "test-server"),        # uppercased input
        ("test-server.yml", "test-server"),    # .yml stripped
        ("test-server.yaml", "test-server"),   # .yaml stripped
        (" test-server ", "test-server"),      # surrounding whitespace stripped
    ],
)
def test_pool_key_normalization(raw, expected):
    # Original assertion semantics kept verbatim from test_advanced.py §2.
    from ssh_client import pool
    assert pool._to_key(raw) == expected


# ────────────────────────────────────────────────────────────────────
# ConnectionPool — list_servers sorting and index (test_advanced §3)
# ────────────────────────────────────────────────────────────────────

def test_list_servers_sorting_and_index(pool, servers_dir):
    _write(servers_dir, "two", {"server": {"host": "h.test", "user": "u", "group": "alpha"}})
    _write(servers_dir, "three", {"server": {"host": "h.test", "user": "u", "group": "alpha"}})
    _write(servers_dir, "one", {"server": {"host": "h.test", "user": "u", "group": "zeta"}})

    servers = pool.list_servers()
    names = [s["name"] for s in servers]

    assert servers[0]["index"] == 1
    assert servers[1]["index"] == 2
    assert servers[2]["index"] == 3
    # group 'alpha' before group 'zeta'; within a group, by name.
    assert names == ["three", "two", "one"]


def test_list_servers_cli_returns_valid_json(run_cli):
    out, err, code = run_cli("list-servers")
    assert code == 0, f"stderr={err.strip()!r}"
    data = json.loads(out)
    assert "servers" in data


# ────────────────────────────────────────────────────────────────────
# extends inheritance — server fields
# ────────────────────────────────────────────────────────────────────

def test_extends_merges_server_fields_local_wins(pool, servers_dir):
    _write(servers_dir, "base", {
        "server": {"host": "base.host", "port": 2222, "user": "baseuser", "password": "bp"},
    })
    _write(servers_dir, "child", {
        "extends": ["base.yml"],
        "server": {"user": "childuser", "port": 3333},
    })

    cfg = pool._load_config("child")
    srv = cfg["server"]
    assert srv["host"] == "base.host"          # inherited
    assert srv["password"] == "bp"             # inherited
    assert srv["port"] == 3333                 # local override
    assert srv["user"] == "childuser"          # local override


def test_extends_allows_missing_host_user_in_child(pool, servers_dir):
    # A config with `extends` is not required to carry host/user itself.
    _write(servers_dir, "base", {"server": {"host": "b", "user": "u"}})
    created = pool.create_server("derived", {
        "extends": ["base.yml"],
        "server": {"desc": "inherits host/user"},
    })
    assert created["created"]


# ────────────────────────────────────────────────────────────────────
# extends inheritance — aliases
# ────────────────────────────────────────────────────────────────────

def test_extends_merges_aliases_local_takes_priority(pool, servers_dir):
    _write(servers_dir, "base", {
        "server": {"host": "b", "user": "u"},
        "aliases": [
            {"name": "shared", "inline": "base-version"},
            {"name": "base-only", "inline": "uptime"},
        ],
    })
    _write(servers_dir, "child", {
        "extends": ["base.yml"],
        "server": {"host": "c"},
        "aliases": [{"name": "shared", "inline": "child-version"}],
    })

    cfg = pool._load_config("child")
    aliases = {a["name"]: a["inline"] for a in cfg["aliases"]}
    assert aliases["shared"] == "child-version"   # local wins over inherited
    assert aliases["base-only"] == "uptime"       # inherited
    names = [a["name"] for a in cfg["aliases"]]
    assert names == ["base-only", "shared"]       # inherited first, then local


# ────────────────────────────────────────────────────────────────────
# extends inheritance — security & paths, proxy
# ────────────────────────────────────────────────────────────────────

def test_extends_inherits_security_fields(pool, servers_dir):
    _write(servers_dir, "base", {
        "server": {"host": "b", "user": "u"},
        "whitelist": ["whoami.*"],
        "blacklist": ["rm.*"],
        "command_template": "cd /srv && <command>",
        "allowed_local_paths": ["./scripts"],
        "allowed_remote_paths": ["/opt/app"],
        "scripts_dir": "/srv/scripts",
    })
    _write(servers_dir, "child", {"extends": ["base.yml"], "server": {"host": "c"}})

    cfg = pool._load_config("child")
    assert cfg["whitelist"] == ["whoami.*"]
    assert cfg["blacklist"] == ["rm.*"]
    assert cfg["command_template"] == "cd /srv && <command>"
    assert cfg["allowed_local_paths"] == ["./scripts"]
    assert cfg["allowed_remote_paths"] == ["/opt/app"]
    assert cfg["scripts_dir"] == "/srv/scripts"


def test_extends_keeps_local_security_fields(pool, servers_dir):
    _write(servers_dir, "base", {
        "server": {"host": "b", "user": "u"},
        "whitelist": ["whoami.*"],
        "blacklist": ["rm.*"],
    })
    _write(servers_dir, "child", {
        "extends": ["base.yml"],
        "server": {"host": "c"},
        "whitelist": [],          # explicitly unrestricted locally
        "blacklist": ["mkfs.*"],  # overridden locally
    })

    cfg = pool._load_config("child")
    assert cfg["whitelist"] == []
    assert cfg["blacklist"] == ["mkfs.*"]


def test_extends_inherits_and_overrides_proxy(pool, servers_dir):
    _write(servers_dir, "base", {
        "server": {"host": "b", "user": "u"},
        "proxy": {"host": "127.0.0.1", "port": 1080},
    })
    _write(servers_dir, "child", {"extends": ["base.yml"], "server": {"host": "c"}})
    _write(servers_dir, "child-local-proxy", {
        "extends": ["base.yml"],
        "server": {"host": "c"},
        "proxy": {"host": "10.0.0.1", "port": 9050},
    })

    assert pool._load_config("child")["proxy"] == {"host": "127.0.0.1", "port": 1080}
    assert pool._load_config("child-local-proxy")["proxy"] == {
        "host": "10.0.0.1", "port": 9050,
    }


def test_extends_missing_file_warns_and_continues(pool, servers_dir, capsys):
    _write(servers_dir, "child", {
        "extends": ["does-not-exist.yml"],
        "server": {"host": "c", "user": "u"},
    })

    cfg = pool._load_config("child")   # must not raise
    captured = capsys.readouterr()
    assert "extends file not found" in captured.err
    assert cfg["server"]["host"] == "c"
    assert "extends" not in cfg        # key is consumed, not leaked to callers


# ────────────────────────────────────────────────────────────────────
# ConnectionPool error paths (test_advanced §8 & §10)
# ────────────────────────────────────────────────────────────────────

def test_pool_get_nonexistent_raises_filenotfound(pool, servers_dir):
    with pytest.raises(FileNotFoundError) as exc_info:
        pool.get("this_server_does_not_exist_xyz")
    assert "Server config not found" in str(exc_info.value)


def test_cli_nonexistent_server_returns_error(run_cli):
    # No network needed: the server file is looked up locally first.
    out, err, code = run_cli("nonexistent-server-xyz-987", "run", "echo test", "-t", "10")
    assert code != 0, f"nonexistent server should error; code={code} stdout={out!r}"


def test_cli_nonexistent_alias_returns_error(run_cli):
    out, err, code = run_cli("test-server", "alias", "does_not_exist_xyz123", "-t", "10")
    assert code != 0, f"nonexistent alias should error; code={code} stdout={out!r}"


def test_run_alias_nonexistent_raises_valueerror():
    conn = SSHConnection(
        {"host": "example.test", "user": "tester"},
        aliases=[{"name": "known", "inline": "true"}],
    )
    with pytest.raises(ValueError, match="Alias not found"):
        conn.run_alias("does_not_exist_xyz123")


# ────────────────────────────────────────────────────────────────────
# sudo password missing → ValueError (no connection is attempted)
# ────────────────────────────────────────────────────────────────────

def test_sudo_without_password_raises_valueerror():
    conn = SSHConnection({"host": "example.test", "user": "tester"})
    assert conn.sudo_password is None
    with pytest.raises(ValueError, match="sudo_password not configured"):
        conn.run("whoami", sudo=True, timeout=5)
