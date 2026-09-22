from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class InventoryHandler(BaseHTTPRequestHandler):
    capacity = 3
    remaining = 3
    accepted_count = 0
    accepted_keys: set[str] = set()
    lock = threading.Lock()
    oversell = False
    reject_all = False

    def do_POST(self) -> None:
        if self.path != "/inventory/reservations" or not self.headers.get("X-LiteTraffic-Run"):
            self._send(404, {})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            key = str(body["logical_key"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send(400, {})
            return
        if self.reject_all:
            accepted = False
        else:
            accepted = self._reserve_wrong(key) if self.oversell else self._reserve(key)
        self._send(201 if accepted else 200, self._state() | {"accepted": accepted})

    def do_GET(self) -> None:
        if self.path != "/inventory/state" or not self.headers.get("X-LiteTraffic-Run"):
            self._send(404, {})
            return
        self._send(200, self._state())

    @classmethod
    def _reserve(cls, key: str) -> bool:
        with cls.lock:
            if key in cls.accepted_keys:
                return True
            if cls.remaining == 0:
                return False
            cls.remaining -= 1
            cls.accepted_count += 1
            cls.accepted_keys.add(key)
            return True

    @classmethod
    def _reserve_wrong(cls, key: str) -> bool:
        if cls.remaining <= 0:
            return False
        time.sleep(0.05)
        with cls.lock:
            cls.remaining -= 1
            cls.accepted_count += 1
            cls.accepted_keys.add(key)
        return True

    @classmethod
    def _state(cls) -> dict:
        with cls.lock:
            return {"capacity": cls.capacity, "remaining": cls.remaining, "accepted_count": cls.accepted_count}

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
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--wrong-oversell", action="store_true")
    parser.add_argument("--reject-all", action="store_true")
    args = parser.parse_args()
    InventoryHandler.oversell = args.wrong_oversell
    InventoryHandler.reject_all = args.reject_all
    ThreadingHTTPServer(("127.0.0.1", args.port), InventoryHandler).serve_forever()


if __name__ == "__main__":
    main()
