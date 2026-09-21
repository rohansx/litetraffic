from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ReportingHandler(BaseHTTPRequestHandler):
    wrong_partial = False
    delay_seconds = 0.0

    def do_GET(self) -> None:
        if self.path != "/reports/sales?window=current" or not self.headers.get("X-LiteTraffic-Run"):
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
    parser.add_argument("--delay-ms", type=float, default=0)
    args = parser.parse_args()
    if args.delay_ms < 0:
        parser.error("--delay-ms must be non-negative")
    ReportingHandler.wrong_partial = args.wrong_partial
    ReportingHandler.delay_seconds = args.delay_ms / 1000
    ThreadingHTTPServer(("127.0.0.1", args.port), ReportingHandler).serve_forever()


if __name__ == "__main__":
    main()
