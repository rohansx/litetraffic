from __future__ import annotations

import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlsplit


class CachedSearchHandler(BaseHTTPRequestHandler):
    wrong_stale = False
    fixtures: dict[str, dict] = {}
    lock = Lock()

    def do_POST(self) -> None:
        run_id = self.headers.get("X-LiteTraffic-Run")
        body = self._body()
        if self.path == "/fixtures" and run_id and body == {"hot_price": 100, "cold_price": 10}:
            fixture_id = uuid.uuid4().hex
            with self.lock:
                self.fixtures[fixture_id] = {"run_id": run_id, "source": {"hot": 100, "cold": 10}, "cache": {}}
            self._send(201, {"id": fixture_id})
            return
        fixture = self._owned_fixture()
        if self.path != "/products/hot/price" or fixture is None or body != {"price": 120}:
            self._send(404, {})
            return
        with self.lock:
            fixture["source"]["hot"] = 120
            if not self.wrong_stale:
                fixture["cache"].pop("hot", None)
        self._send(200, {"updated": "hot"})

    def do_GET(self) -> None:
        fixture = self._owned_fixture()
        if fixture is None:
            self._send(404, {})
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            if query not in {"hot", "cold"}:
                self._send(404, {})
                return
            with self.lock:
                price = fixture["cache"].setdefault(query, fixture["source"][query])
            self._send(200, {"query": query, "price": price})
            return
        if parsed.path == "/cache/state":
            with self.lock:
                body = {"source_price": fixture["source"]["hot"], "cache_price": fixture["cache"].get("hot")}
            self._send(200, body)
            return
        self._send(404, {})

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
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--wrong-stale", action="store_true")
    args = parser.parse_args()
    CachedSearchHandler.wrong_stale = args.wrong_stale
    ThreadingHTTPServer(("127.0.0.1", args.port), CachedSearchHandler).serve_forever()


if __name__ == "__main__":
    main()
