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
from urllib.parse import quote

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
            payload = json.dumps({"org": self.path}).encode() if own else b"{}"  # a non-empty read-back state
            self.send_response(200 if own else 403)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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
            {"method": "PUT", "path": "/positions/{position}", "kind": "write", "body": {"organization_id": "{id}"},
             "attack_body": {"organization_id": "{id}", "title": "attack"}},
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
    posts = [body for method, path, body in seen if (method, path) == ("POST", "/positions") and body["organization_id"] == "org-b"]
    assert posts and posts[0]["note"][0].startswith("{literal}-lt-attack-")  # a writes b's org; attacker strings get the suffix
    assert ("PUT", "/positions/pos%20a", {"organization_id": "org-a", "title": "attack"}) in seen  # b writes a's position


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

    write = {"method": "PUT", "path": "/notes/{id}/meta", "kind": "write", "body": {"m": "1"}}
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


@pytest.mark.parametrize(("anonymous_allowed", "verdict"), [(False, "pass"), (True, "fail")])
def test_kit_unauthenticated_probe(tmp_path, anonymous_allowed, verdict):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            caller = self.headers.get("X-Tenant")
            ok = caller == self.path.rsplit("/", 1)[-1] or (caller is None and anonymous_allowed)
            self.send_response(200 if ok else 401 if caller is None else 403)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            return

    config = {
        "name": "anonymous",
        "identities": [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"]},
                       {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"]}],
        "endpoints": [{"method": "GET", "path": "/notes/{id}", "kind": "read"}],
        "unauthenticated_probe": True,
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, Handler)
    assert _failed(result) == ({"unauthenticated_rejected"} if anonymous_allowed else set()), result
    assert result["verdict"] == verdict, result


@pytest.mark.parametrize(("anonymous_allowed", "verdict"), [(False, "pass"), (True, "fail")])
def test_kit_isolates_cookies_per_identity_and_for_anonymous_probes(tmp_path, anonymous_allowed, verdict):
    """The server falls back to a session cookie it sets whenever X-Tenant is present; a shared jar would mask anonymous leaks."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            header, cookie = self.headers.get("X-Tenant"), self.headers.get("Cookie")
            caller = header or (cookie.removeprefix("session=") if cookie else None)
            ok = caller == self.path.rsplit("/", 1)[-1] or (caller is None and anonymous_allowed)
            self.send_response(200 if ok else 401 if caller is None else 403)
            if header:
                self.send_header("Set-Cookie", f"session={header}; Path=/")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            return

    config = {
        "name": "cookies",
        "identities": [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"]},
                       {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"]}],
        "endpoints": [{"method": "GET", "path": "/notes/{id}", "kind": "read"}],
        "unauthenticated_probe": True,
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, Handler)
    assert _failed(result) == ({"unauthenticated_rejected"} if anonymous_allowed else set()), result
    assert result["verdict"] == verdict, result


@pytest.mark.parametrize(
    ("mode", "failed"),
    [("reference", set()), ("denial-body", {"cross_tenant_read_blocked", "unauthenticated_rejected"}),
     ("owner-list", {"no_foreign_data_in_own_responses"})],
)
def test_kit_protected_markers_in_response_bodies(tmp_path, mode, failed):
    """Statuses are right in every mode; only the bodies leak: a 403 carrying the record, or an owner's list with both tenants' rows."""
    from http.server import BaseHTTPRequestHandler

    records = {"a": {"id": "a", "secret": "private-a"}, "b": {"id": "b", "secret": "private-b"}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            caller, (kind, owner) = self.headers.get("X-Tenant"), self.path.strip("/").split("/")
            if caller == owner:
                rows = list(records.values()) if mode == "owner-list" else [records[owner]]
                status, payload = 200, rows if kind == "lists" else records[owner]
            else:
                status = 401 if caller is None else 403
                payload = records[owner] if mode == "denial-body" else {"error": "forbidden"}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    config = {
        "name": "markers",
        "identities": [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"], "markers": ["private-a"]},
                       {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"], "markers": ["private-b"]}],
        "endpoints": [{"method": "GET", "path": "/records/{id}", "kind": "read"},
                      {"method": "GET", "path": "/lists/{id}", "kind": "read"}],
        "unauthenticated_probe": True,
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, Handler)
    assert _failed(result) == failed, result
    assert result["verdict"] == ("fail" if failed else "pass"), result
    evidence = "".join(path.read_text(errors="replace") for path in (tmp_path / "runs").rglob("*") if path.is_file() and path.suffix != ".js")
    assert "private-" not in json.dumps(result) and "private-" not in evidence  # marker names/indexes only, never values or bodies


def test_kit_gives_each_identity_a_fresh_jar_every_journey(tmp_path):
    """A cookie-first server: the session cookie wins over X-Tenant, and a cookie from another journey is refused.

    A jar shared between identities makes b act as a; a jar kept across journeys sends a stale session.
    """
    from http.server import BaseHTTPRequestHandler
    from itertools import count

    sessions, tokens, journeys = {}, count(), set()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _, _, owner, journey = self.path.split("/")
            journeys.add(journey)
            cookie = self.headers.get("Cookie", "").removeprefix("session=")
            header = self.headers.get("X-Tenant")
            if cookie:
                caller, issued_for = sessions[cookie]
                status = 409 if issued_for != journey else 200 if caller == owner else 403
            else:
                status = 401 if header is None else 200 if header == owner else 403
            self.send_response(status)
            if header and not cookie:
                token = str(next(tokens))
                sessions[token] = (header, journey)
                self.send_header("Set-Cookie", f"session={token}; Path=/")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            return

    config = {
        "name": "cookie-first",
        "identities": [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"]},
                       {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"]}],
        "endpoints": [{"method": "GET", "path": "/notes/{id}/{journey}", "kind": "read", "journey_in_path": True}],
        "unauthenticated_probe": True,
        "max_in_flight": 1,  # one VU runs every journey, so a jar that outlived its journey would be reused
        "schedule": {"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 2, "rate": 3}]},
    }
    result = _serve_kit(tmp_path, config, Handler)
    assert len(journeys) > 1, journeys  # several journeys ran
    assert _failed(result) == set(), result
    assert result["verdict"] == "pass", result


def test_kit_fills_journey_placeholder(tmp_path):
    """`{journey}` becomes lt.journeyKey(): in body strings always, in a path only with journey_in_path."""
    from http.server import BaseHTTPRequestHandler

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.command, self.path, json.loads(body) if body else None))
            own = self.headers.get("X-Tenant") == self.path.split("/")[2]
            payload = b'{"ok": true}' if own and self.command == "GET" else b"{}"  # a non-empty read-back state
            self.send_response(200 if own and self.command == "GET" else 403)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_PUT = _reply

        def log_message(self, *args):
            return

    config = {
        "name": "journey-key",
        "identities": [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"]},
                       {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"]}],
        "endpoints": [{"method": "GET", "path": "/notes/{id}", "kind": "read"},
                      {"method": "PUT", "path": "/notes/{id}/{journey}", "kind": "write", "journey_in_path": True,
                       "body": {"tag": "{journey}", "note": "{id}"}}],
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, Handler)
    assert result["verdict"] == "pass", result
    writes = [(path, body) for method, path, body in seen if method == "PUT"]
    assert len(writes) == 2
    for path, body in writes:
        journey = body["tag"].split("-lt-attack-", 1)[0]  # the attacker's generated body suffixes every string
        assert body["tag"] == f"{journey}-lt-attack-{journey}", body
        assert journey.startswith(result["run_id"]) and path.endswith("/" + quote(journey, safe="")), (path, body)


TWO_TENANTS = [{"name": "a", "headers": {"X-Tenant": "a"}, "resources": ["a"]},
               {"name": "b", "headers": {"X-Tenant": "b"}, "resources": ["b"]}]


def _record_handler(values: dict, *, apply_every_put: bool, read_body=None, field="value"):
    """GET /records/{id} returns {field: ...} to its owner (or `read_body`); PUT stores only body[field], 200 for owners, 403 otherwise."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            owner = self.path.rsplit("/", 1)[-1]
            if self.headers.get("X-Tenant") != owner:
                self._send(403, {})
            else:
                self._send(200, {field: values[owner]} if read_body is None else read_body)

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            owner = self.path.rsplit("/", 1)[-1]
            allowed = self.headers.get("X-Tenant") == owner
            if allowed or apply_every_put:
                values[owner] = body[field]
            self._send(200 if allowed else 403, {})

        def log_message(self, *args):
            return

    return Handler


def test_kit_rejects_head_read_back_at_init(tmp_path):
    """HEAD read-backs compare two empty bodies, so a 403-but-applied write would pass; init refuses them."""
    config = {
        "name": "head-read-back",
        "identities": TWO_TENANTS,
        "endpoints": [{"method": "HEAD", "path": "/records/{id}", "kind": "read"},
                      {"method": "PUT", "path": "/records/{id}", "kind": "write", "body": {"value": "hacked"}}],
        "schedule": ONE_SECOND,
    }
    (tmp_path / "kit.json").write_text(json.dumps(config))
    generated = subprocess.run([sys.executable, "-m", "litetraffic", "init", "tenant-isolation", "--config", str(tmp_path / "kit.json"),
                                "--out", str(tmp_path / "kit")], capture_output=True, text=True, timeout=30)
    assert generated.returncode != 0 and "HEAD" in generated.stdout + generated.stderr, generated.stdout + generated.stderr
    assert not (tmp_path / "kit").exists()


def test_kit_check_own_does_not_neutralize_the_attack(tmp_path):
    """An idempotent PUT with check_own: the owner writes the body first, so an attacker writing that same body changes nothing."""
    config = {
        "name": "check-own",
        "identities": TWO_TENANTS,
        "endpoints": [{"method": "GET", "path": "/records/{id}", "kind": "read"},
                      {"method": "PUT", "path": "/records/{id}", "kind": "write", "body": {"value": "x"}, "check_own": True}],
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, _record_handler({"a": "a0", "b": "b0"}, apply_every_put=True))
    assert _failed(result) == {"victim_unchanged"}, result
    assert result["verdict"] == "fail", result


def test_kit_check_own_mixed_body_is_not_a_false_pass(tmp_path):
    """The attacker varies only a string the server ignores, so its applied write re-stores the qty the owner already wrote."""
    config = {
        "name": "mixed-body",
        "identities": TWO_TENANTS,
        "endpoints": [{"method": "GET", "path": "/records/{id}", "kind": "read"},
                      {"method": "PUT", "path": "/records/{id}", "kind": "write", "body": {"qty": 5, "note": "n"}, "check_own": True}],
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, _record_handler({"a": 1, "b": 2}, apply_every_put=True, field="qty"))
    rows = {row["id"]: row["status"] for row in result["assertions"]}
    assert rows["victim_unchanged"] == "unknown" and result["verdict"] != "pass", result


@pytest.mark.parametrize(("read_body", "attack_body"), [(b"", None), ({}, None), ({"value": "evil"}, {"value": "evil"})],
                         ids=["empty-body", "empty-json", "attack-equals-state"])
def test_kit_victim_unchanged_is_unknown_when_the_read_back_cannot_show_the_attack(tmp_path, read_body, attack_body):
    """A secure server, but the read-back cannot reveal a mutation: an empty state, or one the attacker body already matches."""
    write = {"method": "PUT", "path": "/records/{id}", "kind": "write", "body": {"value": "owner"}}
    config = {
        "name": "inconclusive",
        "identities": TWO_TENANTS,
        "endpoints": [{"method": "GET", "path": "/records/{id}", "kind": "read"},
                      write | ({"attack_body": attack_body} if attack_body else {})],
        "schedule": ONE_SECOND,
    }
    result = _serve_kit(tmp_path, config, _record_handler({"a": "a0", "b": "b0"}, apply_every_put=False, read_body=read_body))
    rows = {row["id"]: row["status"] for row in result["assertions"]}
    assert rows["victim_unchanged"] == "unknown", result
    assert _failed(result) == set() and result["verdict"] == "inconclusive", result
