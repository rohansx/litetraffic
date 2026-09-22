from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


class ReportingHandler(BaseHTTPRequestHandler):
    wrong_partial = False
    wrong_ledger = False
    delay_seconds = 0.0
    fixtures: dict[str, str] = {}
    fixture_lock = Lock()

    def do_POST(self) -> None:
        run_id = self.headers.get("X-LiteTraffic-Run")
        if self.path != "/fixtures" or not run_id:
            self._send(404, {})
            return
        size = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(size))
        except (ValueError, TypeError):
            self._send(400, {})
            return
        if body != {"total": 1000, "row_count": 5}:
            self._send(400, {})
            return
        fixture_id = uuid.uuid4().hex
        with self.fixture_lock:
            self.fixtures[fixture_id] = run_id
        self._send(201, {"id": fixture_id})

    def do_DELETE(self) -> None:
        run_id = self.headers.get("X-LiteTraffic-Run")
        fixture_id = self.path.removeprefix("/fixtures/")
        with self.fixture_lock:
            if not run_id or not fixture_id or self.path != f"/fixtures/{fixture_id}" or self.fixtures.get(fixture_id) != run_id:
                self._send(404, {})
                return
            del self.fixtures[fixture_id]
        self._send(200, {"deleted": fixture_id})

    def do_GET(self) -> None:
        fixture_id = self.headers.get("X-LiteTraffic-Fixture")
        run_id = self.headers.get("X-LiteTraffic-Run")
        with self.fixture_lock:
            owned = bool(run_id and fixture_id and self.fixtures.get(fixture_id) == run_id)
        if not owned:
            self._send(404, {})
            return
        if self.path == "/reports/ledger":
            self._send(200, {
                "total": 900 if self.wrong_ledger else 1000,
                "row_count": 4 if self.wrong_ledger else 5,
                "regions": {"west": 100},
            })
            return
        if self.path != "/reports/sales?window=current":
            self._send(404, {})
            return
        time.sleep(self.delay_seconds)
        report = {
            "window": "current",
            "total": 1000,
            "row_count": 5,
            "regions": {"north": 700, "south": 200, "west": 100},
        }
        if self.wrong_partial:
            report = {
                "window": "current",
                "total": 900,
                "row_count": 4,
                "regions": {"north": 700, "south": 200},
            }
        self._send(200, report)

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
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--wrong-partial", action="store_true")
    parser.add_argument("--wrong-ledger", action="store_true")
    parser.add_argument("--delay-ms", type=float, default=0)
    args = parser.parse_args()
    if args.delay_ms < 0:
        parser.error("--delay-ms must be non-negative")
    ReportingHandler.wrong_partial = args.wrong_partial
    ReportingHandler.wrong_ledger = args.wrong_ledger
    ReportingHandler.delay_seconds = args.delay_ms / 1000
    ThreadingHTTPServer(("127.0.0.1", args.port), ReportingHandler).serve_forever()


if __name__ == "__main__":
    main()
