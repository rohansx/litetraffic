import contextlib
import shutil
import socket
import ssl
import subprocess
import threading
import time

import httpx
import pytest

from litetraffic import observation
from litetraffic.fixture import cleanup_fixture, create_fixture
from litetraffic.models import FinalObservation, OwnedHttpFixture
from litetraffic.observation import observe

CONFIG = OwnedHttpFixture(create_path="/fixtures", delete_path="/fixtures/{fixture_id}", id_pointer="/id")
OBSERVATION = FinalObservation(assertion="a", path="/state", expected={"/x": 1})


@pytest.fixture
def trickle_target():
    """A server that answers every connection with one byte per second for 30 s."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    stop = threading.Event()

    def trickle(conn):
        with conn:
            conn.recv(65536)
            for byte in b"HTTP/1.1 200 OK\r\n" + b"X" * 13:
                if stop.wait(1):
                    return
                try:
                    conn.sendall(bytes([byte]))
                except OSError:
                    return

    def serve():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=trickle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    stop.set()
    server.close()


@pytest.mark.parametrize("call", ["observe", "create", "cleanup"])
def test_trickling_server_is_aborted_at_the_deadline(trickle_target, monkeypatch, call):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 1.5)
    started = time.monotonic()
    if call == "observe":
        result = observe(trickle_target, OBSERVATION, "run-1")
        assert result["status"] == "unknown"
    elif call == "create":
        result = create_fixture(trickle_target, CONFIG, "run-1")
        assert result["status"] == "error"
    else:
        result = cleanup_fixture(trickle_target, CONFIG, "run-1", "fx-1")
        assert result["status"] == "error"
    assert result["reason"].endswith("deadline exceeded")
    assert time.monotonic() - started < 3


def test_oversized_response_body_is_rejected():
    body = b"1" * (observation.MAX_BODY_BYTES + 1)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    result = observe("http://target.test", OBSERVATION, "run-1", transport)
    assert result == {"assertion": "a", "status": "unknown", "reason": "observer unavailable: response body over 1 MiB"}


class ClosableTransport(httpx.MockTransport):
    """A MockTransport that remembers being closed, so a handler can tell it was cancelled."""

    def __init__(self, handler):
        super().__init__(handler)
        self.closed = threading.Event()

    def close(self):
        self.closed.set()


def test_deadline_cancels_the_in_flight_request_before_returning(monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.1)
    release, side_effects, saw_closed = threading.Event(), [], []

    def handler(request):
        release.wait(5)
        saw_closed.append(transport.closed.is_set())
        if transport.closed.is_set():
            raise httpx.ReadError("cancelled", request=request)
        side_effects.append(request.method)
        return httpx.Response(201, json={"id": "fx-late"})

    transport = ClosableTransport(handler)
    baseline = set(threading.enumerate())
    timer = threading.Timer(0.3, release.set)
    timer.start()
    with pytest.raises(observation.DeadlineExceeded):
        observation.bounded_request("POST", "http://target.test/fixtures", {}, transport, {})
    assert saw_closed == [True]  # the handler finished, seeing the closed client, before bounded_request returned
    timer.join()
    assert set(threading.enumerate()) <= baseline  # no worker left behind
    time.sleep(0.2)
    assert side_effects == []


@pytest.fixture
def silent_target():
    """A server that accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()


def test_deadline_closes_a_silent_connection_and_joins_its_worker(silent_target, monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.3)
    baseline = set(threading.enumerate())
    started = time.monotonic()
    with pytest.raises(observation.DeadlineExceeded):
        observation.bounded_request("GET", silent_target + "/state", {})
    assert time.monotonic() - started < 1.5
    assert set(threading.enumerate()) <= baseline  # no worker left behind


@pytest.fixture
def silent_tls_target(tmp_path, monkeypatch):
    """An https server that completes the TLS handshake and never answers; clients trust its self-signed cert."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl not installed")
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert, "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, stdin=subprocess.DEVNULL, capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    stop = threading.Event()

    def serve():
        conn, _ = server.accept()
        with contextlib.suppress(OSError), context.wrap_socket(conn, server_side=True):
            stop.wait(10)  # read nothing, answer nothing

    threading.Thread(target=serve, daemon=True).start()
    real_init = httpx.Client.__init__

    def trusting_init(self, *args, **kwargs):
        real_init(self, *args, **{**kwargs, "verify": ssl.create_default_context(cafile=str(cert))})

    monkeypatch.setattr(httpx.Client, "__init__", trusting_init)
    yield f"https://127.0.0.1:{server.getsockname()[1]}"
    stop.set()
    server.close()


def test_deadline_closes_a_silent_tls_connection_and_joins_its_worker(silent_tls_target, monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.3)
    baseline = set(threading.enumerate())
    started = time.monotonic()
    with pytest.raises(observation.DeadlineExceeded):
        observation.bounded_request("GET", silent_tls_target + "/state", {})
    assert time.monotonic() - started < 1.0  # well inside the cancel grace: the TLS socket was shut down
    assert set(threading.enumerate()) <= baseline  # no worker left behind


def test_fixture_create_timeout_records_an_unknown_outcome(silent_target, monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.3)
    result = create_fixture(silent_target, CONFIG, "run-1")
    assert result == {"status": "error", "reason": "fixture create outcome unknown: deadline exceeded", "requests": 1}


def test_fixture_create_answered_during_cancellation_keeps_its_id_for_cleanup(monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.1)

    def handler(request):
        time.sleep(0.3)  # the response was already on its way when the deadline hit
        return httpx.Response(201, json={"id": "fx-late"})

    result = create_fixture("http://target.test", CONFIG, "run-1", ClosableTransport(handler))
    assert result == {"status": "created", "fixture_id": "fx-late", "requests": 1}


def test_deadline_bounds_a_hanging_tcp_connect_and_joins_its_worker(monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.3)

    def blackhole(address, timeout=None, source_address=None):
        time.sleep(timeout)  # an unroutable address: connect waits out its whole timeout
        raise TimeoutError("timed out")

    monkeypatch.setattr(socket, "create_connection", blackhole)
    baseline = set(threading.enumerate())
    with pytest.raises(observation.DeadlineExceeded):
        observation.bounded_request("GET", "http://10.255.255.1/x", {})
    assert set(threading.enumerate()) <= baseline  # no worker left behind


def test_connect_completing_after_the_deadline_sends_nothing(monkeypatch):
    monkeypatch.setattr(observation, "REQUEST_DEADLINE_SECONDS", 0.1)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(2)
    real_connect = socket.create_connection

    def late(address, timeout=None, source_address=None):
        time.sleep(0.4)  # the connect completes after the deadline, inside the cancel grace
        return real_connect(address)

    monkeypatch.setattr(socket, "create_connection", late)
    with pytest.raises(observation.DeadlineExceeded):
        observation.bounded_request("GET", f"http://127.0.0.1:{server.getsockname()[1]}/x", {})
    conn, _ = server.accept()
    conn.settimeout(0.5)
    with conn, server:
        try:
            received = conn.recv(65536)
        except TimeoutError:
            received = b""
    assert received == b""  # the request was never sent
