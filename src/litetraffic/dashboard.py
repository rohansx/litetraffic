"""Read-only local dashboard over a runs directory, served on 127.0.0.1 with the stdlib."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from litetraffic import dashboard_pages as pages
from litetraffic.runs import list_runs

HOST = "127.0.0.1"
FILTERS = ("scenario", "verdict", "seed")
_TYPES = {".html": "text/html", ".json": "application/json"}


def _run_dir(runs_dir: Path, run_id: str | None) -> Path | None:
    # Only a plain child directory of runs-dir is ever read; anything else is 404.
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id or run_id.startswith(".") or "\x00" in run_id:
        return None
    root = runs_dir.resolve()
    path = (root / run_id).resolve()
    return path if path.parent == root and path.is_dir() else None


def _artifact(run: Path, parts: list[str]) -> Path | None:
    # Files anywhere under the run directory; never a symlink or a path that resolves outside it.
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part or "\x00" in part for part in parts):
        return None
    path = run.joinpath(*parts)
    if path.is_symlink() or not path.is_file():
        return None
    return path if path.resolve().is_relative_to(run) else None


def _filters(query: str) -> dict[str, str]:
    values = parse_qs(query)
    return {key: values[key][0] for key in FILTERS if values.get(key, [""])[0]}


def _entries(runs_dir: Path, filters: dict[str, str]) -> list[dict]:
    return [entry for entry in list_runs(runs_dir) if all(pages.plain(entry[key]) == value for key, value in filters.items())]


def _scenarios(runs_dir: Path) -> list[str]:
    return sorted({entry["scenario"] for entry in list_runs(runs_dir) if isinstance(entry["scenario"], str)})


def _index(runs_dir: Path, query: str) -> str:
    filters = _filters(query)
    return pages.index(_entries(runs_dir, filters), _scenarios(runs_dir), filters)


def _diff_pair(runs_dir: Path, query: str) -> list[Path | None]:
    values = parse_qs(query)
    # Two ticked rows arrive newest first (the index order), so the second is the baseline.
    refs = values["run"][::-1] if len(values.get("run", [])) == 2 else [values.get(key, [None])[0] for key in ("a", "b")]
    return [_run_dir(runs_dir, ref) for ref in refs]


def _trend(runs_dir: Path, scenario: str) -> str | None:
    entries = [entry for entry in reversed(list_runs(runs_dir)) if entry["kind"] == "run" and entry["scenario"] == scenario]
    return pages.trend(scenario, entries) if entries else None


def _handler(runs_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str | bytes, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            url = urlsplit(self.path)
            parts = [unquote(part) for part in url.path.split("/")[1:]]
            if parts == [""]:
                return self._send(200, _index(runs_dir, url.query))
            if parts == ["api", "runs"]:
                return self._send(200, json.dumps(_entries(runs_dir, _filters(url.query)), sort_keys=True), "application/json")
            if parts == ["api", "scenarios"]:
                return self._send(200, json.dumps(_scenarios(runs_dir)), "application/json")
            if parts == ["diff"] and all(pair := _diff_pair(runs_dir, url.query)):
                return self._send(200, pages.diff(*pair))
            if len(parts) == 2 and parts[0] == "scenarios" and (body := _trend(runs_dir, parts[1])):
                return self._send(200, body)
            if len(parts) >= 2 and parts[0] == "runs" and (path := _run_dir(runs_dir, parts[1])):
                if len(parts) == 2:
                    return self._send(200, pages.activity(path) if (path / "activity.json").is_file() else pages.run(path))
                if artifact := _artifact(path, parts[2:]):
                    kind = _TYPES.get(artifact.suffix, "text/plain")
                    return self._send(200, artifact.read_bytes(), f"{kind}; charset=utf-8")
            self._send(404, pages.page("Not found", "<h1>Not found</h1>"))

        do_HEAD = do_GET  # noqa: N815 - same routes and headers; _send skips the body

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - quiet by default
            pass

    return Handler


def make_server(runs_dir: Path, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), _handler(Path(runs_dir)))


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
