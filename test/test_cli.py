#!/usr/bin/env python3
"""Offline regression tests for cli.py.

Everything is driven in-process via cli.main() with a stub connection
injected into the global pool (no network), plus one subprocess smoke
test for the top-level error handling and one tmp_path-backed test for
the server-config commands.
"""
import json
import sys

import pytest
import yaml

import cli
import ssh_client


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class StubConn:
    """Records every call made by cli._dispatch; never touches the network."""

    def __init__(self):
        self.timeout = 42
        self.calls = []

    def _record(self, name, args, kwargs, result=None):
        self.calls.append((name, args, kwargs))
        return dict({"stdout": "", "stderr": "", "code": 0}, **(result or {}))

    def run(self, cmd, timeout=None, sudo=False, stream_cb=None, internal=False):
        return self._record("run", (cmd,), {
            "timeout": timeout, "sudo": sudo, "stream_cb": stream_cb,
        })

    def run_alias(self, name, stream_cb=None):
        return self._record("run_alias", (name,), {"stream_cb": stream_cb})

    def run_script(self, name, timeout=None, sudo=False, stream_cb=None):
        return self._record("run_script", (name,), {
            "timeout": timeout, "sudo": sudo, "stream_cb": stream_cb,
        })

    def upload_file(self, *args, **kwargs):
        return self._record("upload_file", args, kwargs)

    def download(self, *args, **kwargs):
        return self._record("download", args, kwargs)

    def download_file(self, *args, **kwargs):
        return self._record("download_file", args, kwargs)

    def download_script(self, *args, **kwargs):
        return self._record("download_script", args, kwargs)

    def call(self, name):
        calls = [c for c in self.calls if c[0] == name]
        assert len(calls) == 1, f"expected exactly one {name}() call, got {self.calls}"
        return calls[0]


@pytest.fixture
def stub_conn(monkeypatch):
    """Patch ssh_client.pool.get (the same singleton cli imports) to return
    a call-recording stub."""
    stub = StubConn()
    monkeypatch.setattr(ssh_client.pool, "get", lambda name: stub)
    return stub


def _main(monkeypatch, *args):
    """Invoke cli.main() with the given argv tail (after 'cli.py')."""
    monkeypatch.setattr(sys, "argv", ["cli.py", *(str(a) for a in args)])
    cli.main()


def _main_ex(monkeypatch, *args):
    """Run main() on an exiting branch; return the SystemExit code."""
    with pytest.raises(SystemExit) as excinfo:
        _main(monkeypatch, *args)
    return excinfo.value.code


# ---------------------------------------------------------------------------
# a. _pop_tail_flags: tail consumption vs. verbatim payload
# ---------------------------------------------------------------------------

def test_pop_tail_flags_consumes_trailing_bool_flags():
    tokens = ["uptime", "-s"]
    flags = cli._pop_tail_flags(tokens, cli._BOOL_FLAGS, cli._OPT_FLAGS)
    assert flags == {"-s": True}
    assert tokens == ["uptime"]


def test_pop_tail_flags_consumes_trailing_opt_and_bool_runs():
    tokens = ["top", "-x", "-t", "30", "-s"]
    flags = cli._pop_tail_flags(tokens, cli._BOOL_FLAGS, cli._OPT_FLAGS)
    assert flags == {"-x": True, "-t": "30", "-s": True}
    assert tokens == ["top"]


def test_pop_tail_flags_keeps_flags_inside_command_text():
    # '-s' sits in the middle of the payload -> must survive verbatim
    tokens = ["grep", "-s", "foo"]
    flags = cli._pop_tail_flags(tokens, cli._BOOL_FLAGS, cli._OPT_FLAGS)
    assert flags == {}
    assert tokens == ["grep", "-s", "foo"]


def test_pop_tail_flags_only_consumes_tail_block_around_payload_flags():
    tokens = ["grep", "-s", "foo", "-t", "5"]
    flags = cli._pop_tail_flags(tokens, cli._BOOL_FLAGS, cli._OPT_FLAGS)
    assert flags == {"-t": "5"}
    assert tokens == ["grep", "-s", "foo"]


def test_pop_tail_flags_leaves_dangling_opt_flag_alone():
    tokens = ["ls", "-t"]
    flags = cli._pop_tail_flags(tokens, cli._BOOL_FLAGS, cli._OPT_FLAGS)
    assert flags == {}
    assert tokens == ["ls", "-t"]


def test_pop_tail_flags_alternate_spellings_are_distinct_keys():
    tokens = ["cmd", "--sudo"]
    flags = cli._pop_tail_flags(tokens, cli._BOOL_FLAGS, cli._OPT_FLAGS)
    assert flags == {"--sudo": True}
    assert cli._flag_value(flags, "-s", "--sudo") is True


# ---------------------------------------------------------------------------
# b. --no-overwrite reaches the transfer primitives (regression lock)
# ---------------------------------------------------------------------------

def test_upload_file_no_overwrite_reaches_primitive(monkeypatch, stub_conn):
    code = _main_ex(monkeypatch, "srv", "upload-file", "./local.json",
                    "/srv/app/config.json", "--no-overwrite")
    assert code == 0
    _, args, kwargs = stub_conn.call("upload_file")
    assert args[0] == "./local.json"
    assert args[1] == "/srv/app/config.json"
    assert args[3] is False, "--no-overwrite must arrive as overwrite=False"


def test_upload_file_default_overwrite_is_true(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "upload-file", "./local.json",
             "/srv/app/config.json")
    _, args, _ = stub_conn.call("upload_file")
    assert args[3] is True


def test_upload_file_executable_and_no_overwrite_tail_combo(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "upload-file", "./run.sh", "/srv/app/run.sh",
             "-x", "--no-overwrite")
    _, args, kwargs = stub_conn.call("upload_file")
    assert args[3] is False
    assert kwargs.get("executable") is True


def test_download_no_overwrite_and_pattern_and_sudo(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "download", "/var/log/secure", "./secure.log",
             "-p", r"\.log$", "--no-overwrite", "-s")
    _, args, kwargs = stub_conn.call("download")
    assert args[0] == "/var/log/secure"
    assert args[1] == "./secure.log"
    assert kwargs["pattern"] == r"\.log$"
    assert kwargs["overwrite"] is False
    assert kwargs["sudo"] is True


def test_download_file_no_overwrite_reaches_primitive(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "download-file", "/etc/app.log", "./app.log",
             "--no-overwrite", "-t", "60")
    _, args, _ = stub_conn.call("download_file")
    assert args[0] == "/etc/app.log"
    assert args[1] == "./app.log"
    assert args[2] == 60  # -t parsed from the tail as well
    assert args[3] is False, "--no-overwrite must arrive as overwrite=False"
    assert args[4] is False  # sudo


def test_download_file_default_overwrite_is_true(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "download-file", "/etc/app.log", "./app.log")
    _, args, _ = stub_conn.call("download_file")
    assert args[3] is True


def test_download_script_no_overwrite_reaches_primitive(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "download-script", "fix.sh", "./fix.sh",
             "--no-overwrite")
    _, args, _ = stub_conn.call("download_script")
    assert args[0] == "fix.sh"
    assert args[1] == "./fix.sh"
    assert args[3] is False, "--no-overwrite must arrive as overwrite=False"


def test_download_script_default_overwrite_is_true(monkeypatch, stub_conn):
    _main_ex(monkeypatch, "srv", "download-script", "fix.sh", "./fix.sh")
    _, args, _ = stub_conn.call("download_script")
    assert args[3] is True


# ---------------------------------------------------------------------------
# c. real-time streaming for run / alias / run-script
# ---------------------------------------------------------------------------

class StreamingConn(StubConn):
    """Stubs whose executed command pushes chunks through stream_cb and then
    also returns the same text in the final result (proving the live stream
    is the *only* eager write: nothing may be printed twice)."""

    def __init__(self, chunks):
        super().__init__()
        self._chunks = chunks

    def _stream(self, stream_cb):
        assert callable(stream_cb), "cli must pass a stream_cb"
        for chunk, is_stderr in self._chunks:
            stream_cb(chunk, is_stderr)

    def run(self, cmd, timeout=None, sudo=False, stream_cb=None, internal=False):
        self._stream(stream_cb)
        return self._record("run", (cmd,), {
            "timeout": timeout, "sudo": sudo, "stream_cb": stream_cb,
        }, result={"stdout": "LATE-STDOUT", "stderr": "LATE-STDERR"})

    def run_alias(self, name, stream_cb=None):
        self._stream(stream_cb)
        return self._record("run_alias", (name,), {"stream_cb": stream_cb},
                            result={"stdout": "LATE-STDOUT", "stderr": "LATE-STDERR"})

    def run_script(self, name, timeout=None, sudo=False, stream_cb=None):
        self._stream(stream_cb)
        return self._record("run_script", (name,), {
            "timeout": timeout, "sudo": sudo, "stream_cb": stream_cb,
        }, result={"stdout": "LATE-STDOUT", "stderr": "LATE-STDERR"})


@pytest.fixture
def streaming_conn(monkeypatch):
    chunks = [("out-1|", False), ("err-A|", True), ("out-2|", False)]
    stub = StreamingConn(chunks)
    monkeypatch.setattr(ssh_client.pool, "get", lambda name: stub)
    return stub


def test_run_streams_chunks_live_without_duplicate_output(monkeypatch,
                                                           streaming_conn, capsys):
    code = _main_ex(monkeypatch, "srv", "run", "uptime")
    assert code == 0
    captured = capsys.readouterr()
    # exactly the streamed chunks, in order, and NOT the result payload
    assert captured.out == "out-1|out-2|"
    assert captured.err == "err-A|"
    _, args, kwargs = streaming_conn.call("run")
    assert args[0] == "uptime"
    assert callable(kwargs["stream_cb"])


def test_run_flag_parsing_and_exit_code_passthrough(monkeypatch, streaming_conn):
    code = _main_ex(monkeypatch, "srv", "run", "apt", "update", "-t", "7", "-s")
    assert code == 0
    _, args, kwargs = streaming_conn.call("run")
    assert args[0] == "apt update"  # payload joined, flags eaten from tail
    assert kwargs["timeout"] == 7
    assert kwargs["sudo"] is True


def test_run_exits_with_remote_exit_code(monkeypatch, streaming_conn):
    # StreamingConn always returns code 0; override to check passthrough
    monkeypatch.setattr(streaming_conn, "run",
                        lambda *a, **k: streaming_conn._record(
                            "run", a, k, result={"code": 3}))
    code = _main_ex(monkeypatch, "srv", "run", "false")
    assert code == 3


def test_run_keeps_embedded_flags_verbatim(monkeypatch, streaming_conn):
    _main_ex(monkeypatch, "srv", "run", "grep", "-s", "foo")
    _, args, _ = streaming_conn.call("run")
    assert args[0] == "grep -s foo"


def test_alias_streams_chunks_live(monkeypatch, streaming_conn, capsys):
    code = _main_ex(monkeypatch, "srv", "alias", "healthcheck")
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == "out-1|out-2|"
    assert captured.err == "err-A|"
    _, args, kwargs = streaming_conn.call("run_alias")
    assert args[0] == "healthcheck"
    assert callable(kwargs["stream_cb"])


def test_run_script_streams_chunks_live(monkeypatch, streaming_conn, capsys):
    code = _main_ex(monkeypatch, "srv", "run-script", "restart.sh", "-s")
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == "out-1|out-2|"
    assert captured.err == "err-A|"
    _, args, kwargs = streaming_conn.call("run_script")
    assert args[0] == "restart.sh"
    assert kwargs["sudo"] is True
    assert callable(kwargs["stream_cb"])


# ---------------------------------------------------------------------------
# d. _write_result merging and exit code
# ---------------------------------------------------------------------------

def test_write_result_merges_and_orders_streams(capsys):
    code = cli._write_result({
        "stdout": "A", "run_stdout": "B",
        "stderr": "E", "run_stderr": "F",
        "code": 3,
    })
    captured = capsys.readouterr()
    assert captured.out == "AB"   # stdout before run_stdout
    assert captured.err == "EF"   # stderr before run_stderr
    assert code == 3


def test_write_result_partial_streams_and_default_code(capsys):
    assert cli._write_result({"run_stderr": "only"}) == 1  # missing code -> 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "only"


def test_upload_dispatch_uses_write_result_merge(monkeypatch, stub_conn, capsys):
    monkeypatch.setattr(stub_conn, "upload_file",
                        lambda *a, **k: stub_conn._record(
                            "upload_file", a, k,
                            result={"stdout": "up\n", "run_stdout": "ran\n",
                                    "stderr": "w\n", "run_stderr": "rw\n",
                                    "code": 5}))
    code = _main_ex(monkeypatch, "srv", "upload-file", "./a", "/r/a")
    captured = capsys.readouterr()
    assert code == 5
    assert captured.out == "up\nran\n"
    assert captured.err == "w\nrw\n"


# ---------------------------------------------------------------------------
# e. _safe_json with lone surrogates
# ---------------------------------------------------------------------------

def test_safe_json_plain_unicode_stays_raw():
    text = cli._safe_json({"desc": "中文"})
    assert "中文" in text  # ensure_ascii=False path keeps native text
    assert json.loads(text) == {"desc": "中文"}


def test_safe_json_lone_surrogate_falls_back_to_ascii_escape():
    payload = {"password": "p" + "\udcae" + "es", "n": [1, "\udcae"]}
    text = cli._safe_json(payload)  # must not raise
    assert text.isascii(), "surrogate payload must fall back to ensure_ascii=True"
    parsed = json.loads(text)  # must be valid JSON
    assert [ord(c) for c in parsed["password"]] == [ord("p"), 0xDCAE, ord("e"), ord("s")]
    assert [ord(c) for c in parsed["n"][1]] == [0xDCAE]


# ---------------------------------------------------------------------------
# f. main() top-level error handling (subprocess, real interpreter)
# ---------------------------------------------------------------------------

def test_unknown_server_clean_error_via_subprocess(run_cli):
    out, err, code = run_cli("no-such-server-zz9", "run", "ls")
    assert code == 1
    assert err.startswith("Error:"), f"expected clean one-line error, got {err!r}"
    assert "Traceback" not in err
    assert "Traceback" not in out
    assert "no-such-server-zz9" in err or "not found" in err


# ---------------------------------------------------------------------------
# g. server-config commands (JSON output, tmp SERVERS_DIR, no network)
# ---------------------------------------------------------------------------

@pytest.fixture
def servers_dir(monkeypatch, tmp_path):
    servers = tmp_path / "servers"
    monkeypatch.setattr(ssh_client, "SERVERS_DIR", servers)
    return servers


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


def test_create_copy_delete_server_json_flow(monkeypatch, servers_dir, capsys):
    cfg = _write_yaml(servers_dir.parent / "alpha.yml.src",
                      {"server": {"host": "h1", "user": "u1", "desc": "first"}})

    _main(monkeypatch, "create-server", "alpha", cfg)
    created = json.loads(capsys.readouterr().out)
    assert created["created"] is True
    assert created["name"] == "alpha"
    target = servers_dir / "alpha.yml"
    assert target.exists()
    assert target.read_text(encoding="utf-8").count("h1") == 1

    _main(monkeypatch, "copy-server", "alpha", "beta")
    copied = json.loads(capsys.readouterr().out)
    assert copied == {"source": "alpha", "name": "beta",
                      "path": str(servers_dir / "beta.yml"), "copied": True}
    assert (servers_dir / "beta.yml").exists()

    with pytest.raises(SystemExit) as excinfo:
        _main(monkeypatch, "copy-server", "missing", "gamma")
    assert excinfo.value.code == 1
    assert capsys.readouterr().err.startswith("Error:")

    _main(monkeypatch, "delete-server", "beta")
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["deleted"] is True
    assert not (servers_dir / "beta.yml").exists()


def test_create_server_duplicate_reports_clean_error(monkeypatch, servers_dir,
                                                     capsys):
    cfg = _write_yaml(servers_dir.parent / "dup.yml.src",
                      {"server": {"host": "h1", "user": "u1"}})
    _main(monkeypatch, "create-server", "alpha", cfg)
    capsys.readouterr()
    with pytest.raises(SystemExit) as excinfo:
        _main(monkeypatch, "create-server", "alpha", cfg)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("Error:")
    assert "already exists" in err


def test_update_server_merges_by_default_and_replaces_on_flag(
        monkeypatch, servers_dir, capsys):
    cfg = _write_yaml(servers_dir.parent / "base.yml.src",
                      {"server": {"host": "h1", "user": "u1", "desc": "first"}})
    _main(monkeypatch, "create-server", "gamma", cfg)
    capsys.readouterr()

    # default: recursive merge keeps untouched fields
    patch_file = _write_yaml(servers_dir.parent / "patch.yml",
                             {"server": {"desc": "updated"}})
    _main(monkeypatch, "update-server", "gamma", patch_file)
    merged_out = json.loads(capsys.readouterr().out)
    assert merged_out["updated"] is True
    assert merged_out["replace"] is False
    saved = yaml.safe_load((servers_dir / "gamma.yml").read_text(encoding="utf-8"))
    assert saved["server"] == {"host": "h1", "user": "u1", "desc": "updated"}

    # --replace: whole config swapped
    full_patch = _write_yaml(servers_dir.parent / "full.yml",
                             {"server": {"host": "h2", "user": "u2"}})
    _main(monkeypatch, "update-server", "gamma", full_patch, "--replace")
    replaced_out = json.loads(capsys.readouterr().out)
    assert replaced_out["replace"] is True
    saved = yaml.safe_load((servers_dir / "gamma.yml").read_text(encoding="utf-8"))
    assert saved["server"] == {"host": "h2", "user": "u2"}
    assert "desc" not in saved["server"]

    with pytest.raises(SystemExit) as excinfo:
        _main(monkeypatch, "update-server", "ghost", full_patch)
    assert excinfo.value.code == 1
    assert capsys.readouterr().err.startswith("Error:")


# ---------------------------------------------------------------------------
# run - : command from stdin (bypasses local-shell $ expansion entirely)
# ---------------------------------------------------------------------------

def _stdin(monkeypatch, text):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_run_dash_reads_command_from_stdin_verbatim(monkeypatch, stub_conn, capsys):
    # Raw $ and quotes — the exact characters the local shell would eat.
    command = ("awk 'match($0,/req: [0-9]+/){print substr($0,RSTART+5,"
               "RLENGTH-5)}' /tmp/dec.txt | sort -n\n")
    _stdin(monkeypatch, command)
    code = _main_ex(monkeypatch, "srv", "run", "-")
    assert code == 0
    cmd, = stub_conn.call("run")[1]
    assert cmd == command.rstrip("\n") or cmd == command
    assert "$0" in cmd  # dollar survived untouched


def test_run_dash_streams_and_honours_tail_flags(monkeypatch, stub_conn):
    _stdin(monkeypatch, "docker logs x 2>&1 | awk 'match($0,/y/){print}'")
    code = _main_ex(monkeypatch, "srv", "run", "-", "-t", "45", "-s")
    assert code == 0
    name, args, kwargs = stub_conn.call("run")
    assert args[0] == "docker logs x 2>&1 | awk 'match($0,/y/){print}'"
    assert kwargs["timeout"] == 45 and kwargs["sudo"] is True
    assert kwargs["stream_cb"] is not None


def test_run_dash_mixed_with_other_tokens_is_error(monkeypatch, stub_conn, capsys):
    _stdin(monkeypatch, "uptime")
    code = _main_ex(monkeypatch, "srv", "run", "-", "uptime")
    assert code == 2
    assert "only command argument" in capsys.readouterr().err
    assert stub_conn.calls == []


def test_run_dash_empty_stdin_is_error(monkeypatch, stub_conn, capsys):
    _stdin(monkeypatch, "   \n")
    code = _main_ex(monkeypatch, "srv", "run", "-")
    assert code == 2
    assert "Empty command" in capsys.readouterr().err
    assert stub_conn.calls == []
