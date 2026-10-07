"""Local dashboard: a JSON API over a runs directory plus the built SPA, on 127.0.0.1 with the stdlib.

It only reads runs, except that POST /api/runs/<id>/explain caches an AI write-up as explanation.json in that run.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from litetraffic import __version__
from litetraffic.compare import ComparisonError, compare_runs
from litetraffic.explain import ExplainError, explain, explain_provider, load_cached, summarize
from litetraffic.runs import _real_file, list_runs, read_json
from litetraffic.series import _value

HOST = "127.0.0.1"
UI_DIR = Path(__file__).parent / "dashboard_ui"
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
ACTION_HEADER = "X-LiteTraffic-Action"
_EXPLAIN_LOCK = threading.Lock()  # ponytail: one CLI call at a time; per-run locks if that ever queues
FILTERS = ("scenario", "verdict", "seed")
_ARTIFACT_TYPES = {".html": "text/html", ".json": "application/json"}  # anything else is shown as plain text
_UI_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2"}
CSP = (
    "default-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)
# Raw artifacts (report.html above all) are untrusted: no scripts, no requests, own origin.
ARTIFACT_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
MISSING_BUNDLE = (
    "The LiteTraffic dashboard UI has not been built.\n\n"
    "This checkout has no src/litetraffic/dashboard_ui/index.html. Build it with:\n\n"
    "    pnpm -C web install && pnpm -C web build\n\n"
    "then reload this page. The JSON API under /api/ works without it.\n"
)


def _run_dir(runs_dir: Path, run_id: str | None) -> Path | None:
    # Only a plain child directory of runs-dir is ever read; anything else is 404.
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id or run_id.startswith(".") or "\x00" in run_id:
        return None
    root = runs_dir.resolve()
    if (root / run_id).is_symlink():
        return None
    path = (root / run_id).resolve()
    return path if path.parent == root and path.is_dir() else None


def _file(root: Path, parts: list[str]) -> Path | None:
    # Files anywhere under root; never a symlink or a path that resolves outside it.
    if not parts or any(part in {"", ".", ".."} or "/" in part or "\\" in part or "\x00" in part for part in parts):
        return None
    path = root.joinpath(*parts)
    if path.is_symlink() or not path.is_file():
        return None
    return path if path.resolve().is_relative_to(root.resolve()) else None


def _artifacts(run: Path) -> list[dict]:
    found = []
    for folder, _, names in os.walk(run):  # os.walk does not descend into symlinked directories
        for name in names:
            path = Path(folder, name)
            if not path.is_symlink() and path.is_file():
                found.append({"path": path.relative_to(run).as_posix(), "size": path.stat().st_size})
    return sorted(found, key=lambda item: item["path"])


def _load(path: Path) -> object:
    """Any JSON value from a regular file, or None; NaN/Infinity become null so browsers can parse it."""
    if not _real_file(path):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: None)
    except (OSError, ValueError):
        return None


def _plain(value: object) -> str:
    return "" if value is None else value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _entries(runs_dir: Path, query: str) -> list[dict]:
    values = parse_qs(query)
    filters = {key: values[key][0] for key in FILTERS if values.get(key, [""])[0]}
    return [entry for entry in list_runs(runs_dir) if all(_plain(entry[key]) == value for key, value in filters.items())]


def _scenarios(runs_dir: Path) -> list[str]:
    return sorted({entry["scenario"] for entry in list_runs(runs_dir) if isinstance(entry["scenario"], str)})


def _detail(path: Path) -> dict:
    detail = {"run_id": path.name, "run": read_json(path / "run.json"), "result": read_json(path / "result.json")}
    for name in ("observation", "fixture", "activity", "server"):
        if (value := _load(path / f"{name}.json")) is not None:
            detail[name] = value
    if (explanation := summarize(detail)) is not None:
        detail["explanation"] = explanation
    if (cached := load_cached(path)) is not None:
        detail["ai_explanation"] = cached
    return {**detail, "artifacts": _artifacts(path)}


def _trend(runs_dir: Path, scenario: str) -> list[dict]:
    entries = [entry for entry in reversed(list_runs(runs_dir)) if entry["kind"] == "run" and entry["scenario"] == scenario]

    def p95(entry: dict) -> float | None:
        value = _value((read_json(Path(entry["path"]) / "result.json") or {}).get("metrics"), ("http_req_duration_ms", "p95"))
        return value if value is not None and math.isfinite(value) else None

    return [{"run_id": e["run_id"], "finished_at": e["finished_at"], "p95": p95(e), "verdict": e["verdict"]} for e in entries]


def _json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False).encode()
    except ValueError:  # a NaN from list_runs metadata: send null instead of invalid JSON
        return json.dumps(json.loads(json.dumps(value), parse_constant=lambda _: None), sort_keys=True).encode()


def _handler(runs_dir: Path, ui_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str, csp: str = CSP, cache: str = "no-cache") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _api(self, status: int, value: object) -> None:
            self._send(status, _json(value), "application/json")

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            # Unsupported methods and malformed requests get the same security headers, plain text only.
            self.close_connection = True
            self._send(code, f"{code} {message or self.responses.get(code, ('Error',))[0]}\n".encode(), "text/plain; charset=utf-8")

        def _not_found(self) -> None:
            self._api(404, {"error": "not found"})

        def _loopback_host(self) -> str | None:
            # DNS rebinding guard: only loopback names for this exact port may use anything.
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").lower()
            return host if host in {f"{name}:{port}" for name in _LOOPBACK_NAMES} else None

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if not self._loopback_host():
                return self._send(421, b"Misdirected request\n", "text/plain; charset=utf-8")
            url = urlsplit(self.path)
            parts = [unquote(part) for part in url.path.split("/")[1:]]
            if parts[0] == "api":
                return self._route_api(parts[1:], url.query)
            return self._route_ui(parts)

        def _route_api(self, parts: list[str], query: str) -> None:
            values = parse_qs(query)
            if parts == ["meta"]:
                return self._api(200, {"explain_cli": explain_provider(), "runs_dir": str(runs_dir.resolve()), "version": __version__})
            if parts == ["runs"]:
                return self._api(200, _entries(runs_dir, query))
            if parts == ["scenarios"]:
                return self._api(200, _scenarios(runs_dir))
            if len(parts) == 3 and parts[0] == "scenarios" and parts[2] == "trend" and (points := _trend(runs_dir, parts[1])):
                return self._api(200, points)
            if parts == ["diff"]:
                pair = [_run_dir(runs_dir, values.get(key, [None])[0]) for key in ("baseline", "candidate")]
                if all(pair):
                    try:
                        return self._api(200, compare_runs(*pair))
                    except ComparisonError as exc:
                        return self._api(409, {"error": str(exc)})
            if len(parts) >= 2 and parts[0] == "runs" and (run := _run_dir(runs_dir, parts[1])):
                if len(parts) == 2:
                    return self._api(200, _detail(run))
                if parts[2] == "artifacts" and (artifact := _file(run, parts[3:])):
                    kind = _ARTIFACT_TYPES.get(artifact.suffix, "text/plain")
                    return self._send(200, artifact.read_bytes(), f"{kind}; charset=utf-8", ARTIFACT_CSP)
            self._not_found()

        def _route_ui(self, parts: list[str]) -> None:
            index = ui_dir / "index.html"
            if not _real_file(index):
                return self._send(503, MISSING_BUNDLE.encode(), "text/plain; charset=utf-8")
            if static := _file(ui_dir, parts):
                kind = _UI_TYPES.get(static.suffix) or mimetypes.guess_type(static.name)[0] or "application/octet-stream"
                # Vite puts content-hashed files under assets/, so they can be cached for good.
                cache = "public, max-age=31536000, immutable" if parts[0] == "assets" else "no-cache"
                return self._send(200, static.read_bytes(), kind, cache=cache)
            if parts[0] == "assets":
                return self._send(404, b"Not found\n", "text/plain; charset=utf-8")
            # Every other path is a client-side route.
            self._send(200, index.read_bytes(), "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            host = self._loopback_host()
            if not host:
                return self._send(421, b"Misdirected request\n", "text/plain; charset=utf-8")
            # A custom header forces a CORS preflight this server never approves, so other sites cannot POST here.
            origin = self.headers.get("Origin")
            if self.headers.get(ACTION_HEADER) != "explain" or (origin is not None and origin.lower() != f"http://{host}"):
                return self._api(403, {"error": "forbidden"})
            parts = [unquote(part) for part in urlsplit(self.path).path.split("/")[1:]]
            if len(parts) != 4 or parts[:2] != ["api", "runs"] or parts[3] != "explain" or not (run := _run_dir(runs_dir, parts[2])):
                return self._not_found()
            detail = _detail(run)
            if detail["result"] is None:
                return self._api(409, {"error": "This run has no result to explain yet."})
            with _EXPLAIN_LOCK:
                try:
                    return self._api(200, explain(run, detail))
                except ExplainError as exc:
                    return self._api(exc.status, {"error": str(exc)})

        do_HEAD = do_GET  # noqa: N815 - same routes and headers; _send skips the body

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - quiet by default
            pass

    return Handler


def make_server(runs_dir: Path, port: int, ui_dir: Path = UI_DIR) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), _handler(Path(runs_dir), Path(ui_dir)))


def serve(runs_dir: Path, port: int) -> int:
    server = make_server(runs_dir, port)
    print(f"LiteTraffic dashboard for {Path(runs_dir).resolve()} at http://{HOST}:{server.server_address[1]}/ (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0
