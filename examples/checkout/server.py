from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class PaymentHandler(BaseHTTPRequestHandler):
    payments: dict[str, dict] = {}
    lock = threading.Lock()
    duplicate_on_retry = False

    def do_POST(self) -> None:
        if self.path != "/payments" or not self.headers.get("X-LiteTraffic-Run"):
            self._send(404, {})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            key = self.headers["Idempotency-Key"]
            total = int(body["total"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send(400, {})
            return
        with self.lock:
            existing = self.payments.get(key)
            if existing and self.duplicate_on_retry:
                existing["effects"] += 1
            elif not existing:
                self.payments[key] = {"logical_key": key, "total": total, "effects": 1}
        self._send(200 if existing else 201, {"logical_key": key})

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not path.startswith("/payments/"):
            self._send(404, {})
            return
        with self.lock:
            payment = self.payments.get(unquote(path.removeprefix("/payments/")))
        self._send(200, payment) if payment else self._send(404, {})

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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--wrong-duplicate", action="store_true")
    args = parser.parse_args()
    PaymentHandler.duplicate_on_retry = args.wrong_duplicate
    ThreadingHTTPServer(("127.0.0.1", args.port), PaymentHandler).serve_forever()


if __name__ == "__main__":
    main()
