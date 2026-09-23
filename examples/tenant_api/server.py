from __future__ import annotations

import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


class TenantHandler(BaseHTTPRequestHandler):
    wrong_leak = False
    deny_all = False
    wrong_silent_write = False
    fixtures: dict[str, dict] = {}
    lock = Lock()

    def do_POST(self) -> None:
        run_id = self.headers.get("X-LiteTraffic-Run")
        body = self._body()
        if self.path != "/fixtures" or not run_id or body != {"tenant_a_value": "alpha", "tenant_b_value": "beta"}:
            self._send(404, {})
            return
        fixture_id = uuid.uuid4().hex
        with self.lock:
            self.fixtures[fixture_id] = {"run_id": run_id, "records": {"a": {"1": "alpha"}, "b": {"1": "beta"}}}
        self._send(201, {"id": fixture_id})

    def do_GET(self) -> None:
        fixture = self._owned_fixture()
        if fixture is None:
            self._send(404, {})
            return
        if self.path == "/tenant/state":
            self._send(200, {"tenant_count": len(fixture["records"]), "overlapping_local_ids": all("1" in records for records in fixture["records"].values())})
            return
        parts = self.path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "tenants" or parts[2] != "records" or parts[3] != "1":
            self._send(404, {})
            return
        requested_tenant = parts[1]
        actor_tenant = self.headers.get("X-Actor-Tenant")
        if self.deny_all or (not self.wrong_leak and actor_tenant != requested_tenant):
            self._send(403, {})
            return
        record = fixture["records"].get(requested_tenant, {}).get("1")
        self._send(200, {"tenant": requested_tenant, "local_id": "1", "value": record}) if record else self._send(404, {})

    def do_PUT(self) -> None:
        fixture = self._owned_fixture()
        parts = self.path.strip("/").split("/")
        body = self._body()
        if fixture is None or len(parts) != 4 or parts[0] != "tenants" or parts[2] != "records" or parts[3] != "1":
            self._send(404, {})
            return
        records = fixture["records"].get(parts[1])
        if records is None or not isinstance(body.get("value"), str):
            self._send(404, {})
            return
        allowed = not self.deny_all and (self.wrong_leak or self.headers.get("X-Actor-Tenant") == parts[1])
        if allowed or self.wrong_silent_write:
            with self.lock:
                records["1"] = body["value"]
        self._send(200, {"tenant": parts[1], "local_id": "1", "value": body["value"]}) if allowed else self._send(403, {})

    def do_DELETE(self) -> None:
        run_id = self.headers.get("X-LiteTraffic-Run")
        fixture_id = self.path.removeprefix("/fixtures/")
        with self.lock:
            if not fixture_id or self.fixtures.get(fixture_id, {}).get("run_id") != run_id:
                self._send(404, {})
                return
            del self.fixtures[fixture_id]
        self._send(200, {"deleted": fixture_id})

    def _owned_fixture(self) -> dict | None:
        fixture = self.fixtures.get(self.headers.get("X-LiteTraffic-Fixture", ""))
        return fixture if fixture and fixture["run_id"] == self.headers.get("X-LiteTraffic-Run") else None

    def _body(self) -> dict:
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (TypeError, ValueError):
            return {}

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--wrong-leak", action="store_true")
    parser.add_argument("--deny-all", action="store_true")
    parser.add_argument("--wrong-silent-write", action="store_true", help="reject cross-tenant writes with 403 but apply them")
    args = parser.parse_args()
    TenantHandler.wrong_leak = args.wrong_leak
    TenantHandler.deny_all = args.deny_all
    TenantHandler.wrong_silent_write = args.wrong_silent_write
    ThreadingHTTPServer(("127.0.0.1", args.port), TenantHandler).serve_forever()


if __name__ == "__main__":
    main()
