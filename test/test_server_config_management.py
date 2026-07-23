#!/usr/bin/env python3
"""Unit tests for local server configuration CRUD and MCP exposure."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

import ssh_client
from mcp_server import handle_message
from ssh_client import ConnectionPool, load_yaml


class ServerConfigManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.servers_dir = Path(self.temp_dir.name)
        self.servers_patch = patch.object(ssh_client, "SERVERS_DIR", self.servers_dir)
        self.servers_patch.start()
        self.pool = ConnectionPool()
        self.config = {
            "server": {
                "host": "example.test",
                "port": 22,
                "user": "deploy",
                "password": "secret",
            },
            "aliases": [{"name": "health", "inline": "uptime"}],
        }

    def tearDown(self):
        self.pool.close_all()
        self.servers_patch.stop()
        self.temp_dir.cleanup()

    def test_create_update_delete_round_trip(self):
        created = self.pool.create_server("Demo-Server", self.config)
        path = self.servers_dir / "demo-server.yml"
        self.assertTrue(created["created"])
        self.assertTrue(path.exists())

        updated = self.pool.update_server(
            "demo-server",
            {"server": {"port": 22022}, "aliases": []},
        )
        saved = load_yaml(str(path))
        self.assertTrue(updated["updated"])
        self.assertEqual(saved["server"]["host"], "example.test")
        self.assertEqual(saved["server"]["port"], 22022)
        self.assertEqual(saved["aliases"], [])

        deleted = self.pool.delete_server("demo-server")
        self.assertTrue(deleted["deleted"])
        self.assertFalse(path.exists())

    def test_create_refuses_overwrite_and_path_traversal(self):
        self.pool.create_server("demo", self.config)
        with self.assertRaises(FileExistsError):
            self.pool.create_server("demo", self.config)
        with self.assertRaises(ValueError):
            self.pool.create_server("../outside", self.config)

    def test_copy_to_new_name_refuses_overwrite(self):
        self.pool.create_server("source", self.config)
        copied = self.pool.copy_server("source", "new-name")
        self.assertTrue(copied["copied"])
        self.assertEqual(copied["source"], "source")
        self.assertEqual(
            (self.servers_dir / "new-name.yml").read_bytes(),
            (self.servers_dir / "source.yml").read_bytes(),
        )
        self.assertEqual(
            load_yaml(str(self.servers_dir / "new-name.yml")),
            self.config,
        )
        with self.assertRaises(FileExistsError):
            self.pool.copy_server("source", "new-name")

    def test_update_replace_and_validation(self):
        self.pool.create_server("demo", self.config)
        replacement = {"server": {"host": "new.test", "user": "root"}}
        self.pool.update_server("demo", replacement, replace=True)
        self.assertEqual(load_yaml(str(self.servers_dir / "demo.yml")), replacement)
        with self.assertRaises(ValueError):
            self.pool.update_server("demo", {"server": {"host": ""}}, replace=True)
        with self.assertRaises(ValueError):
            self.pool.update_server(
                "demo",
                {"server": {"host": "ok", "user": "root"}, "aliases": ["bad"]},
                replace=True,
            )

    def test_mcp_create_update_delete_tools(self):
        create = self._mcp_call(
            1, "ssh_create_server", {"server": "mcp-demo", "config": self.config}
        )
        self.assertTrue(create["created"])
        update = self._mcp_call(
            2,
            "ssh_update_server",
            {"server": "mcp-demo", "config": {"server": {"port": 2222}}},
        )
        self.assertTrue(update["updated"])
        copy = self._mcp_call(
            3,
            "ssh_copy_server",
            {"source_server": "mcp-demo", "target_server": "mcp-copy"},
        )
        self.assertTrue(copy["copied"])
        self._mcp_call(4, "ssh_delete_server", {"server": "mcp-copy"})
        delete = self._mcp_call(5, "ssh_delete_server", {"server": "mcp-demo"})
        self.assertTrue(delete["deleted"])

    @staticmethod
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


if __name__ == "__main__":
    unittest.main()
