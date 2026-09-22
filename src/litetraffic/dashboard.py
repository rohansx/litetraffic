"""Read-only local dashboard over a runs directory, served on 127.0.0.1 with the stdlib."""

from __future__ import annotations

import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from litetraffic.compare import ComparisonError, compare_runs
from litetraffic.human import format_diff
from litetraffic.report import _failure_tables
from litetraffic.runs import list_runs

HOST = "127.0.0.1"
_STYLE = (
    "body{font:15px system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#17211b;background:#f7f8fa}"
    "table{width:100%;border-collapse:collapse;background:white}th,td{padding:8px;text-align:left;border-bottom:1px solid #e5e7eb}"
    "a{color:#0e6b3c}pre{background:white;padding:12px;overflow-x:auto}.verdict{font-size:1.8rem;font-weight:750}"
    ".badge{padding:2px 8px;border-radius:999px;background:#e0e7ff;color:#3730a3;font-size:.85rem}"
)


def _text(value: object) -> str:
    return escape("" if value is None else value if isinstance(value, str) else json.dumps(value, sort_keys=True))


def _page(title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title>'
        f'<style>{_STYLE}</style></head><body><p><a href="/">All runs</a></p>{body}</body></html>'
    )


def _run_dir(runs_dir: Path, run_id: str | None) -> Path | None:
    # Only a plain child directory of runs-dir is ever read; anything else is 404.
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id or run_id.startswith(".") or "\x00" in run_id:
        return None
    root = runs_dir.resolve()
    path = (root / run_id).resolve()
    return path if path.parent == root and path.is_dir() else None


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _field(result: dict, key: str, kind: type) -> dict | list:
    # A parseable but malformed result.json must still render, so wrong-typed fields read as empty.
    value = result.get(key)
    return value if isinstance(value, kind) else kind()


_BADGE = '<span class="badge">background</span>'


def _verdict_cell(entry: dict) -> str:
    return _BADGE if entry["kind"] == "activity" else _text(str(entry["verdict"]).upper())


def _index(runs_dir: Path) -> str:
    rows = "".join(
        "<tr><td>"
        + (f'<a href="/runs/{escape(entry["run_id"])}">{escape(entry["run_id"])}</a>' if entry["kind"] in {"run", "activity"} else _text(entry["run_id"]))
        + "</td>"
        + "".join(f"<td>{_text(entry[key])}</td>" for key in ("kind", "scenario", "lifecycle", "seed", "finished_at"))
        + f"<td>{_verdict_cell(entry)}</td></tr>"
        for entry in list_runs(runs_dir)
    )
    return _page(
        "LiteTraffic runs",
        "<h1>Runs</h1><form action=\"/diff\">Diff <input name=a placeholder=baseline> <input name=b placeholder=candidate>"
        " <button>Compare</button></form><table><thead><tr><th>Run</th><th>Kind</th><th>Scenario</th><th>Lifecycle</th>"
        f"<th>Seed</th><th>Finished</th><th>Verdict</th></tr></thead><tbody>{rows}</tbody></table>",
    )


def _run(path: Path) -> str:
    run, result = _read(path / "run.json"), _read(path / "result.json")
    assertions = [item for item in _field(result, "assertions", list) if isinstance(item, dict)]
    rows = "".join(
        f"<tr><td>{_text(item.get('id'))}</td><td>{_text(str(item.get('status')).upper())}</td><td>{_text(item.get('samples'))}</td></tr>"
        for item in assertions
    )
    metrics = "".join(f"<tr><td>{_text(key)}</td><td>{_text(value)}</td></tr>" for key, value in sorted(_field(result, "metrics", dict).items()))
    limitations = "".join(f"<li>{_text(item)}</li>" for item in _field(result, "limitations", list)) or "<li>None</li>"
    report = f'<p><a href="/runs/{escape(path.name)}/report.html">Full report</a></p>' if (path / "report.html").is_file() else ""
    # _failure_tables expects well-formed assertions: drop ones missing id/status and keep only dict samples.
    failures = _failure_tables(
        [
            {**item, "failures": [sample for sample in _field(item, "failures", list) if isinstance(sample, dict)]}
            for item in assertions
            if "id" in item and "status" in item
        ]
    )
    return _page(
        path.name,
        f"<h1>{_text(run.get('scenario'))}</h1><p class=verdict>{_text(str(result.get('verdict', 'unreadable')).upper())}</p>"
        f"<p>Run {escape(path.name)} · lifecycle {_text(result.get('lifecycle'))} · seed {_text(run.get('seed'))}</p>{report}"
        f"<h2>Assertions</h2><table><thead><tr><th>Assertion</th><th>Status</th><th>Samples</th></tr></thead><tbody>{rows}</tbody></table>"
        f"{failures}<h2>Metrics</h2><table><tbody>{metrics}</tbody></table><h2>Limitations</h2><ul>{limitations}</ul>",
    )


def _activity(path: Path) -> str:
    activity = _read(path / "activity.json")
    slices = [item for item in _field(activity, "slices", list) if isinstance(item, dict)]
    rows = "".join(
        "<tr>" + "".join(f"<td>{_text(item.get(key))}</td>" for key in ("run_id", "seed", "lifecycle", "iterations", "http_reqs", "finished_at")) + "</tr>"
        for item in slices
    )
    return _page(
        path.name,
        f"<h1>{_text(activity.get('scenario'))}</h1><p>{_BADGE} status {_text(activity.get('status'))}</p>"
        f"<p>Activity {escape(path.name)} · target {_text(activity.get('target'))} · starting seed {_text(activity.get('starting_seed'))}</p>"
        "<h2>Slices</h2><table><thead><tr><th>Run</th><th>Seed</th><th>Lifecycle</th><th>Iterations</th><th>HTTP requests</th>"
        f"<th>Finished</th></tr></thead><tbody>{rows}</tbody></table>",
    )


def _diff(baseline: Path, candidate: Path) -> str:
    try:
        lines = format_diff(compare_runs(baseline, candidate))
    except ComparisonError as exc:
        lines = ["verdict: ERROR", str(exc)]
    return _page("Diff", f"<h1>{escape(baseline.name)} → {escape(candidate.name)}</h1><pre>{escape(chr(10).join(lines))}</pre>")


def _handler(runs_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            url = urlsplit(self.path)
            parts = [unquote(part) for part in url.path.split("/")[1:]]
            if parts == [""]:
                return self._send(200, _index(runs_dir))
            if parts == ["api", "runs"]:
                return self._send(200, json.dumps(list_runs(runs_dir), sort_keys=True), "application/json")
            if parts == ["diff"]:
                query = parse_qs(url.query)
                pair = [_run_dir(runs_dir, query.get(key, [None])[0]) for key in ("a", "b")]
                if all(pair):
                    return self._send(200, _diff(*pair))
            if len(parts) in {2, 3} and parts[0] == "runs" and (path := _run_dir(runs_dir, parts[1])):
                if len(parts) == 2:
                    return self._send(200, _activity(path) if (path / "activity.json").is_file() else _run(path))
                report = (path / "report.html").resolve()
                if parts[2] == "report.html" and report.parent == path and report.is_file():
                    return self._send(200, report.read_text(encoding="utf-8"))
            self._send(404, _page("Not found", "<h1>Not found</h1>"))

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
