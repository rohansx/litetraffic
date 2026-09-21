from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ReportingHandler(BaseHTTPRequestHandler):
    wrong_partial = False

    def do_GET(self) -> None:
        if self.path != "/reports/sales?window=current" or not self.headers.get("X-LiteTraffic-Run"):
            self._send(404, {})
            return
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
    args = parser.parse_args()
    ReportingHandler.wrong_partial = args.wrong_partial
    ThreadingHTTPServer(("127.0.0.1", args.port), ReportingHandler).serve_forever()


if __name__ == "__main__":
    main()
