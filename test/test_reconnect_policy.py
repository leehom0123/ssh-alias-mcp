#!/usr/bin/env python3
"""Regression tests for SSH reconnect throttling and request wake-up (pytest)."""
import time

import pytest
from unittest.mock import patch

import ssh_client
from ssh_client import SSHConnection


def _failing_conn(**cfg_extra):
    """Connection whose direct connect always fails, host-key probe neutralised.

    _connect_once probes the host key before _connect_direct; the probe would
    hit DNS for unreachable.test, so it is stubbed out and the mocked
    _connect_direct is what actually runs and gets counted.
    """
    conn = SSHConnection({
        "host": "unreachable.test",
        "user": "deploy",
        **cfg_extra,
    })
    attempts = []

    def fail_connect():
        attempts.append(time.monotonic())
        raise OSError("unreachable")

    conn._probe_host_key = lambda proxy: None
    conn._connect_direct = fail_connect
    return conn, attempts


def test_five_fast_failures_then_slow_retry_and_request_retries_now():
    conn, attempts = _failing_conn(auto_reconnect=True, reconnect_interval=0.01)
    conn.reconnect_slow_interval = 0.2
    try:
        with pytest.raises(OSError):
            conn.connect()

        deadline = time.monotonic() + 1
        while len(attempts) < 5 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(attempts) == 5

        time.sleep(0.05)
        assert len(attempts) == 5, "retry should be in slow cooldown"

        with pytest.raises(OSError):
            conn.connect()
        assert len(attempts) == 6, (
            "an explicit request must retry immediately during cooldown"
        )
    finally:
        conn.close()


def test_auto_reconnect_false_disables_background_attempts():
    conn, attempts = _failing_conn(auto_reconnect=False, reconnect_interval=0.01)
    try:
        with pytest.raises(OSError):
            conn.connect()
        time.sleep(0.05)
        assert len(attempts) == 1
    finally:
        conn.close()


def test_global_auto_reconnect_and_interval_are_inherited():
    with patch.object(ssh_client, "global_config", {
        "server": {
            "auto_reconnect": False,
            "reconnect_interval": 17,
        }
    }):
        conn = SSHConnection({
            "host": "example.test",
            "user": "deploy",
        })
    try:
        assert conn.auto_reconnect is False
        assert conn.reconnect_interval == 17
    finally:
        conn.close()
