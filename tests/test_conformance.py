"""Real-engine conformance: each example's reference server passes, each wrong mutation fails.

Needs the pinned k6 binary; run with ``pytest -m real_k6``. Default CI deselects it.
"""

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from litetraffic.runner import SUPPORTED_K6_VERSION

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _k6_version() -> str | None:
    k6 = shutil.which("k6")
    if k6 is None:
        return None
    try:
        return subprocess.run([k6, "version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None


pytestmark = [
    pytest.mark.real_k6,
    pytest.mark.skipif(
        f"k6 {SUPPORTED_K6_VERSION} " not in (_k6_version() or ""),
        reason=f"real k6 {SUPPORTED_K6_VERSION} not installed",
    ),
]

CASES = [
    ("checkout", [], "pass"),
    ("checkout", ["--wrong-duplicate"], "fail"),
    ("inventory", [], "pass"),
    ("inventory", ["--wrong-oversell"], "fail"),
    ("inventory", ["--reject-all"], "fail"),
    ("reporting", [], "pass"),
    ("reporting", ["--wrong-partial"], "fail"),
    ("reporting", ["--wrong-ledger"], "fail"),
    ("cached_search", [], "pass"),
    ("cached_search", ["--wrong-stale"], "fail"),
    ("tenant_api", [], "pass"),
    ("tenant_api", ["--wrong-leak"], "fail"),
    ("tenant_api", ["--deny-all"], "fail"),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_listening(server: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AssertionError(f"server exited early: {server.stderr.read()}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"server did not listen on {port}")


@pytest.mark.parametrize(
    ("example", "flags", "expected"),
    CASES,
    ids=[f"{example}{''.join(flags) or '-reference'}" for example, flags, _ in CASES],
)
def test_example_conformance(tmp_path, example, flags, expected):
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(EXAMPLES / example / "server.py"), "--port", str(port), *flags],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_listening(server, port)
        completed = subprocess.run(
            [sys.executable, "-m", "litetraffic", "verify", str(EXAMPLES / example),
             "--target", f"http://127.0.0.1:{port}", "--seed", "42",
             "--output-dir", str(tmp_path / "runs"), "--json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert completed.stdout, f"verify produced no output: {completed.stderr}"
    result = json.loads(completed.stdout)
    assert result["verdict"] == expected, completed.stdout
    if flags == ["--wrong-duplicate"]:
        row = next(row for row in result["assertions"] if row["id"] == "one_effect_per_payment")
        assert {"expected": 1, "actual": 2}.items() <= row["failures"][0].items(), row


KIT_CASES = [([], "pass", set()), (["--wrong-leak"], "fail", {"cross_tenant_read_blocked", "cross_tenant_write_blocked"}),
             (["--deny-all"], "fail", {"own_access"}), (["--wrong-silent-write"], "fail", {"victim_unchanged"})]


@pytest.mark.parametrize(("flags", "expected", "failed"), KIT_CASES, ids=[flags[0] if flags else "reference" for flags, *_ in KIT_CASES])
def test_tenant_isolation_kit_conformance(tmp_path, flags, expected, failed):
    scenario = tmp_path / "kit"
    generated = subprocess.run(
        [sys.executable, "-m", "litetraffic", "init", "tenant-isolation",
         "--config", str(EXAMPLES / "tenant_api" / "kit.json"), "--out", str(scenario)],
        capture_output=True, text=True, timeout=30,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(EXAMPLES / "tenant_api" / "server.py"), "--port", str(port), *flags],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_listening(server, port)
        completed = subprocess.run(
            [sys.executable, "-m", "litetraffic", "verify", str(scenario), "--target", f"http://127.0.0.1:{port}",
             "--seed", "42", "--output-dir", str(tmp_path / "runs"), "--json"],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
    result = json.loads(completed.stdout)
    assert result["verdict"] == expected, completed.stdout
    assert {row["id"] for row in result["assertions"] if row["status"] == "fail"} >= failed, completed.stdout


def test_kit_fills_resource_placeholders_in_path_and_body(tmp_path):
    """Each identity's resource keys replace `{name}` in the path and in body strings; unknown `{x}` stays literal."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.command, self.path, json.loads(body) if body else None))
            own = self.command == "GET" and self.path == f"/orgs/org-{self.headers.get('X-Tenant')}"
            self.send_response(200 if own else 403)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = do_POST = do_PUT = _reply

        def log_message(self, *args):
            return

    config = {
        "name": "placeholders",
        "identities": [
            {"name": "a", "headers": {"X-Tenant": "a"}, "resources": [{"id": "org-a", "position": "pos a"}]},
            {"name": "b", "headers": {"X-Tenant": "b"}, "resources": [{"id": "org-b", "position": "pos b"}]},
        ],
        "endpoints": [
            {"method": "GET", "path": "/orgs/{id}", "kind": "read"},
            {"method": "POST", "path": "/positions", "kind": "write", "body": {"organization_id": "{id}", "note": ["{literal}"]}},
            {"method": "PUT", "path": "/positions/{position}", "kind": "write", "body": {"organization_id": "{id}"}},
        ],
        "schedule": {"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
    }
    (tmp_path / "kit.json").write_text(json.dumps(config))
    scenario = tmp_path / "kit"
    generated = subprocess.run([sys.executable, "-m", "litetraffic", "init", "tenant-isolation", "--config", str(tmp_path / "kit.json"),
                                "--out", str(scenario)], capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "litetraffic", "verify", str(scenario), "--target", f"http://127.0.0.1:{server.server_port}",
             "--seed", "42", "--output-dir", str(tmp_path / "runs"), "--json"],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        server.shutdown()
    assert json.loads(completed.stdout)["verdict"] == "pass", completed.stdout
    assert ("POST", "/positions", {"organization_id": "org-b", "note": ["{literal}"]}) in seen  # a writes b's org
    assert ("PUT", "/positions/pos%20a", {"organization_id": "org-a"}) in seen  # b writes a's position


def _serve_kit(tmp_path, config, handler) -> dict:
    """Generate the kit bundle from `config`, verify it against `handler`, return the verify JSON."""
    from http.server import ThreadingHTTPServer
    from threading import Thread

    (tmp_path / "kit.json").write_text(json.dumps(config))
    scenario = tmp_path / "kit"
    generated = subprocess.run([sys.executable, "-m", "litetraffic", "init", "tenant-isolation", "--config", str(tmp_path / "kit.json"),
                                "--out", str(scenario)], capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "litetraffic", "verify", str(scenario), "--target", f"http://127.0.0.1:{server.server_port}",
             "--seed", "42", "--output-dir", str(tmp_path / "runs"), "--json"],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        server.shutdown()
    return json.loads(completed.stdout)


def _failed(result: dict) -> set[str]:
    return {row["id"] for row in result["assertions"] if row["status"] == "fail"}


ONE_SECOND = {"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]}


@pytest.mark.parametrize(("read_back", "verdict"), [(None, "pass"), ("meta", "fail")])
def test_kit_reads_back_through_the_write_endpoints_read_back(tmp_path, read_back, verdict):
    """The server rejects cross-tenant meta writes but applies them; only a meta read-back sees it."""
    from http.server import BaseHTTPRequestHandler

    meta = {"a": 0, "b": 0}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            parts, caller = self.path.strip("/").split("/"), self.headers.get("X-Tenant")
            owner = parts[1]
            if self.command == "PUT":
                meta[owner] += 1  # applied even when the caller is rejected below
            body = json.dumps({"meta": meta[owner]} if parts[-1] == "meta" else {"id": owner}).encode()
            self.send_response(200 if caller == owner else 403)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_PUT = _reply

        def log_message(self, *args):
            return

    write = {"method": "PUT", "path": "/notes/{id}/meta", "kind": "write", "body": {"m": 1}}
    config = {
        "name": "read-back",
        "identities": [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"]},
                       {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"]}],
        "endpoints": [{"method": "GET", "path": "/notes/{id}", "kind": "read"},
                      {"method": "GET", "path": "/notes/{id}/meta", "kind": "read", "name": "meta"},
                      write | ({"read_back": read_back} if read_back else {})],
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, Handler)
    assert _failed(result) == ({"victim_unchanged"} if verdict == "fail" else set()), result
    assert result["verdict"] == verdict, result
