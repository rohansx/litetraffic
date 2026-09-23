"""HTML pages for the local dashboard. Every value from disk goes through escape()."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from urllib.parse import quote

from litetraffic.compare import ComparisonError, compare_runs
from litetraffic.dashboard_assets import INDEX, STYLE, THEME_INIT, THEME_TOGGLE
from litetraffic.human import format_diff
from litetraffic.report import _failure_tables
from litetraffic.series import _value

VERDICTS = ("pass", "fail", "inconclusive", "error", "unreadable", "background")
_BADGE = '<span class="badge">background</span>'


def plain(value: object) -> str:
    return "" if value is None else value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _text(value: object) -> str:
    return escape(plain(value))


def _url(*parts: str) -> str:
    return "/" + "/".join(quote(part, safe="") for part in parts)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _field(data: object, key: str, kind: type) -> dict | list:
    # A parseable but malformed result.json must still render, so wrong-typed fields read as empty.
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, kind) else kind()


def _table(head: tuple[str, ...], rows: str, body_id: str = "") -> str:
    ths = "".join(f"<th>{escape(name)}</th>" for name in head)
    tbody = f'<tbody id="{body_id}">' if body_id else "<tbody>"
    return f'<div class="scroll"><table><thead><tr>{ths}</tr></thead>{tbody}{rows}</tbody></table></div>'


def page(title: str, body: str, script: str = "") -> str:
    tail = f"<script>(function(){{{script}}})();</script>" if script else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style><script>{THEME_INIT}</script></head><body>"
        '<nav><a href="/">All runs</a><button type="button" id="theme" aria-label="Toggle light or dark theme">'
        f"Theme</button></nav><main>{body}</main><script>{THEME_TOGGLE}</script>{tail}</body></html>"
    )


def verdict_badge(verdict: object) -> str:
    name = plain(verdict)
    css = name if name in VERDICTS else "unreadable"
    return f'<span class="badge v-{css}">{escape(name.upper())}</span>'


def _index_row(entry: dict) -> str:
    run_id, kind, scenario = entry["run_id"], entry["kind"], entry["scenario"]
    select = (
        f'<input type="checkbox" name="run" value="{escape(run_id)}" aria-label="Select {escape(run_id)}">' if kind == "run" else ""
    )
    name = f'<a href="{_url("runs", run_id)}">{escape(run_id)}</a>' if kind != "series" else _text(run_id)
    scenario_cell = f'<a href="{_url("scenarios", scenario)}">{escape(scenario)}</a>' if isinstance(scenario, str) else _text(scenario)
    cells = "".join(f"<td>{_text(entry[key])}</td>" for key in ("lifecycle", "seed", "finished_at"))
    verdict = _BADGE if kind == "activity" else verdict_badge(entry["verdict"])
    return f"<tr><td>{select}</td><td>{name}</td><td>{_text(kind)}</td><td>{scenario_cell}</td>{cells}<td>{verdict}</td></tr>"


def _filter_form(scenarios: list[str], filters: dict[str, str]) -> str:
    def options(values: list[str] | tuple[str, ...], chosen: str | None) -> str:
        return '<option value="">Any</option>' + "".join(
            f'<option value="{escape(value)}"{" selected" if value == chosen else ""}>{escape(value)}</option>' for value in values
        )

    return (
        '<form class="controls" action="/" method="get" aria-label="Filter runs">'
        f'<label>Scenario <select name="scenario">{options(scenarios, filters.get("scenario"))}</select></label>'
        f'<label>Verdict <select name="verdict">{options(VERDICTS, filters.get("verdict"))}</select></label>'
        f'<label>Seed <input name="seed" inputmode="numeric" size="8" value="{escape(filters.get("seed", ""))}"></label>'
        '<button>Filter</button> <a href="/">Clear</a></form>'
    )


def index(entries: list[dict], scenarios: list[str], filters: dict[str, str]) -> str:
    rows = "".join(_index_row(entry) for entry in entries)
    table = _table(("Select", "Run", "Kind", "Scenario", "Lifecycle", "Seed", "Finished", "Verdict"), rows, "rows")
    return page(
        "LiteTraffic runs",
        f"<h1>Runs</h1>{_filter_form(scenarios, filters)}"
        '<form id="select" action="/diff" method="get" aria-label="Compare two runs"><div class="controls">'
        '<button id="compare" disabled>Compare</button><span class="muted">Select exactly two runs.</span>'
        '<label style="flex-direction:row;gap:6px;align-items:center"><input type="checkbox" id="autorefresh" checked> Auto-refresh every 5s</label>'
        f'<span id="refresh-status" role="status" class="muted"></span></div>{table}</form>',
        INDEX,
    )


def _artifacts(path: Path) -> str:
    files = sorted(item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file() and not item.is_symlink())
    links = "".join(f'<li><a href="{_url("runs", path.name, *name.split("/"))}">{escape(name)}</a></li>' for name in files)
    return f"<ul>{links or '<li>None</li>'}</ul>"


def _operations(metrics: dict) -> str:
    by_operation, overlap = _field(metrics, "by_operation", dict), _field(metrics, "overlap", dict)
    names = sorted(set(by_operation) | set(overlap))
    rows = "".join(
        f"<tr><td>{_text(name)}</td>"
        + "".join(f"<td>{_text(_field(by_operation, name, dict).get(key))}</td>" for key in ("samples", "p95", "failed_rate"))
        + f"<td>{_text(overlap.get(name))}</td></tr>"
        for name in names
    )
    return _table(("Operation", "Samples", "p95 (ms)", "Failed rate", "Peak in flight"), rows or '<tr><td colspan="5">None</td></tr>')


def run(path: Path) -> str:
    run_data, result = read_json(path / "run.json"), read_json(path / "result.json")
    assertions = [item for item in _field(result, "assertions", list) if isinstance(item, dict)]
    rows = "".join(
        f"<tr><td>{_text(item.get('id'))}</td><td>{verdict_badge(item.get('status'))}</td><td>{_text(item.get('samples'))}</td></tr>"
        for item in assertions
    )
    metrics = _field(result, "metrics", dict)
    metric_rows = "".join(f"<tr><td>{_text(key)}</td><td>{_text(value)}</td></tr>" for key, value in sorted(metrics.items()))
    limitations = "".join(f"<li>{_text(item)}</li>" for item in _field(result, "limitations", list)) or "<li>None</li>"
    report = f'<p><a href="{_url("runs", path.name, "report.html")}">Full report</a></p>' if (path / "report.html").is_file() else ""
    # _failure_tables expects well-formed assertions: drop ones missing id/status and keep only dict samples.
    failures = _failure_tables(
        [
            {**item, "failures": [sample for sample in _field(item, "failures", list) if isinstance(sample, dict)]}
            for item in assertions
            if "id" in item and "status" in item
        ]
    )
    scenario = run_data.get("scenario")
    trend = f' · <a href="{_url("scenarios", scenario)}">Scenario trend</a>' if isinstance(scenario, str) else ""
    return page(
        path.name,
        f"<h1>{_text(scenario)}</h1><p class=verdict>{verdict_badge(result.get('verdict', 'unreadable'))}</p>"
        f"<p>Run {escape(path.name)} · lifecycle {_text(result.get('lifecycle'))} · seed {_text(run_data.get('seed'))}{trend}</p>{report}"
        f"<h2>Limitations</h2><ul>{limitations}</ul>"
        f"<h2>Assertions</h2>{_table(('Assertion', 'Status', 'Samples'), rows)}{failures}"
        f"<h2>Operations</h2>{_operations(metrics)}"
        f"<h2>Metrics</h2>{_table(('Metric', 'Value'), metric_rows)}<h2>Artifacts</h2>{_artifacts(path)}",
    )


def activity(path: Path) -> str:
    data = read_json(path / "activity.json")
    slices = [item for item in _field(data, "slices", list) if isinstance(item, dict)]
    keys = ("run_id", "seed", "lifecycle", "iterations", "http_reqs", "finished_at")
    rows = "".join("<tr>" + "".join(f"<td>{_text(item.get(key))}</td>" for key in keys) + "</tr>" for item in slices)
    return page(
        path.name,
        f"<h1>{_text(data.get('scenario'))}</h1><p>{_BADGE} status {_text(data.get('status'))}</p>"
        f"<p>Activity {escape(path.name)} · target {_text(data.get('target'))} · starting seed {_text(data.get('starting_seed'))}</p>"
        f"<h2>Slices</h2>{_table(('Run', 'Seed', 'Lifecycle', 'Iterations', 'HTTP requests', 'Finished'), rows)}",
    )


def diff(baseline: Path, candidate: Path) -> str:
    try:
        lines = format_diff(compare_runs(baseline, candidate))
    except ComparisonError as exc:
        lines = ["verdict: ERROR", str(exc)]
    return page("Diff", f"<h1>{escape(baseline.name)} → {escape(candidate.name)}</h1><pre>{escape(chr(10).join(lines))}</pre>")


_W, _H, _PAD = 640, 240, 40


def _chart(scenario: str, points: list[tuple[dict, float | None]]) -> str:
    measured = [p95 for _, p95 in points if p95 is not None]
    top = max(measured, default=0) or 1
    step = (_W - 2 * _PAD) / max(len(points) - 1, 1)
    # ponytail: runs are spaced evenly in finish order, not on a true time axis.
    coords = [
        (entry, p95, _PAD + index * step if len(points) > 1 else _W / 2, _H - _PAD - (p95 / top) * (_H - 2 * _PAD))
        for index, (entry, p95) in enumerate(points)
        if p95 is not None
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for _, _, x, y in coords)
    markers = "".join(
        f'<circle class="v-{escape(plain(entry["verdict"]))}" cx="{x:.1f}" cy="{y:.1f}" r="6">'
        f"<title>{escape(entry['run_id'])}: {escape(plain(entry['verdict']).upper())}, p95 {p95:g} ms</title></circle>"
        for entry, p95, x, y in coords
    )
    summary = (
        f"{len(points)} runs, {len(measured)} with a p95; p95 ranges {min(measured):g} to {max(measured):g} ms."
        if measured
        else f"{len(points)} runs, none with a p95."
    )
    return (
        f'<svg role="img" class="chart" viewBox="0 0 {_W} {_H}" aria-labelledby="chart-title chart-desc">'
        f'<title id="chart-title">p95 latency per run for {escape(scenario)}</title><desc id="chart-desc">{escape(summary)} '
        "Marker colour shows the verdict; the table below lists every value.</desc>"
        f'<line x1="{_PAD}" y1="{_H - _PAD}" x2="{_W - _PAD}" y2="{_H - _PAD}"/><line x1="{_PAD}" y1="{_PAD}" x2="{_PAD}" y2="{_H - _PAD}"/>'
        f'<text x="4" y="{_PAD + 4}">{top:g} ms</text><text x="4" y="{_H - _PAD + 4}">0</text>'
        f'<text x="{_PAD}" y="{_H - 12}">oldest</text><text x="{_W - _PAD}" y="{_H - 12}" text-anchor="end">newest</text>'
        f'<polyline points="{line}"/>{markers}</svg>'
    )


def trend(scenario: str, entries: list[dict]) -> str:
    """entries: this scenario's runs, oldest first."""
    points = [(entry, _value(read_json(Path(entry["path"]) / "result.json").get("metrics"), ("http_req_duration_ms", "p95"))) for entry in entries]
    rows = "".join(
        f'<tr><td><a href="{_url("runs", entry["run_id"])}">{escape(entry["run_id"])}</a></td><td>{_text(entry["finished_at"])}</td>'
        f"<td>{_text(entry['seed'])}</td><td>{verdict_badge(entry['verdict'])}</td><td>{'' if p95 is None else f'{p95:g}'}</td></tr>"
        for entry, p95 in points
    )
    return page(
        f"Trend: {scenario}",
        f"<h1>{escape(scenario)}</h1><p class=muted>p95 HTTP latency and verdict per run, oldest first.</p>"
        f"{_chart(scenario, points)}<h2>Data</h2>{_table(('Run', 'Finished', 'Seed', 'Verdict', 'p95 (ms)'), rows)}",
    )
