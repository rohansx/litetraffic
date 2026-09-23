import http.client

import pytest

from test_dashboard import runs_dir, server  # noqa: F401 - fixtures


def request(server, path, host):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=True)
        if host is not None:
            conn.putheader("Host", host)
        conn.endheaders()
        response = conn.getresponse()
        return response.status, response.read().decode()
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/", "/api/runs", "/runs/run_a/report.html"])
@pytest.mark.parametrize(
    "host",
    ["attacker.example:{port}", "attacker.example", "127.0.0.1", "127.0.0.1:1", "localhost.attacker.example:{port}", None],
)
def test_foreign_host_header_is_refused_without_data(server, path, host):
    port = server.server_address[1]
    status, body = request(server, path, host and host.format(port=port))

    assert status == 421
    assert "run_a" not in body and "the report" not in body


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}", "[::1]:{port}"])
def test_loopback_host_header_is_served(server, host):
    status, body = request(server, "/runs/run_a/report.html", host.format(port=server.server_address[1]))

    assert status == 200 and "the report" in body
