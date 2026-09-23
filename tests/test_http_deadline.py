import socket
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
