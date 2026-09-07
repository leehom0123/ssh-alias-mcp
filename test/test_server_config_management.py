"""Unit tests for local server configuration CRUD and MCP exposure.

Offline: every test runs against an isolated temporary servers directory.
"""
import json

import pytest
from unittest.mock import patch

import ssh_client
from mcp_server import handle_message
from ssh_client import ConnectionPool, load_yaml


@pytest.fixture
def servers_dir(tmp_path, monkeypatch):
    """Point ssh_client.SERVERS_DIR at an empty temp directory."""
    dir_path = tmp_path / "servers"
    dir_path.mkdir()
    monkeypatch.setattr(ssh_client, "SERVERS_DIR", dir_path)
    return dir_path


@pytest.fixture
def pool(servers_dir):
    """A fresh ConnectionPool bound to the temp servers directory."""
    conn_pool = ConnectionPool()
    yield conn_pool
    conn_pool.close_all()


@pytest.fixture
def config():
    return {
        "server": {
            "host": "example.test",
            "port": 22,
            "user": "deploy",
            "password": "secret",
        },
        "aliases": [{"name": "health", "inline": "uptime"}],
    }


def _mcp_call(request_id, name, arguments):
    response = handle_message({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    result = response["result"]
    if result.get("isError"):
        raise AssertionError(result["content"][0]["text"])
    return json.loads(result["content"][0]["text"])


def test_create_update_delete_round_trip(pool, servers_dir, config):
    created = pool.create_server("Demo-Server", config)
    path = servers_dir / "demo-server.yml"
    assert created["created"]
    assert path.exists()

    updated = pool.update_server("demo-server", {"server": {"port": 22022}, "aliases": []})
    saved = load_yaml(str(path))
    assert updated["updated"]
    assert saved["server"]["host"] == "example.test"
    assert saved["server"]["port"] == 22022
    assert saved["aliases"] == []

    deleted = pool.delete_server("demo-server")
    assert deleted["deleted"]
    assert not path.exists()


def test_create_refuses_overwrite_and_path_traversal(pool, config):
    pool.create_server("demo", config)
    with pytest.raises(FileExistsError):
        pool.create_server("demo", config)
    with pytest.raises(ValueError):
        pool.create_server("../outside", config)


def test_copy_to_new_name_refuses_overwrite(pool, servers_dir, config):
    pool.create_server("source", config)
    copied = pool.copy_server("source", "new-name")
    assert copied["copied"]
    assert copied["source"] == "source"
    assert (servers_dir / "new-name.yml").read_bytes() == (servers_dir / "source.yml").read_bytes()
    assert load_yaml(str(servers_dir / "new-name.yml")) == config
    with pytest.raises(FileExistsError):
        pool.copy_server("source", "new-name")


def test_update_replace_and_validation(pool, servers_dir, config):
    pool.create_server("demo", config)
    replacement = {"server": {"host": "new.test", "user": "root"}}
    pool.update_server("demo", replacement, replace=True)
    assert load_yaml(str(servers_dir / "demo.yml")) == replacement
    with pytest.raises(ValueError):
        pool.update_server("demo", {"server": {"host": ""}}, replace=True)
    with pytest.raises(ValueError):
        pool.update_server(
            "demo",
            {"server": {"host": "ok", "user": "root"}, "aliases": ["bad"]},
            replace=True,
        )


def test_mcp_create_update_delete_tools(pool, config):
    create = _mcp_call(1, "ssh_create_server", {"server": "mcp-demo", "config": config})
    assert create["created"]
    update = _mcp_call(
        2, "ssh_update_server", {"server": "mcp-demo", "config": {"server": {"port": 2222}}}
    )
    assert update["updated"]
    copy = _mcp_call(
        3, "ssh_copy_server", {"source_server": "mcp-demo", "target_server": "mcp-copy"}
    )
    assert copy["copied"]
    _mcp_call(4, "ssh_delete_server", {"server": "mcp-copy"})
    delete = _mcp_call(5, "ssh_delete_server", {"server": "mcp-demo"})
    assert delete["deleted"]
