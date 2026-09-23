"""HTML pages for the local dashboard. Every value from disk goes through escape()."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from urllib.parse import quote

from litetraffic import runs
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
    return runs.read_json(path) or {}


def _field(data: object, key: str, kind: type) -> dict | list:
    # A parseable but malformed result.json must still render, so wrong-typed fields read as empty.
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, kind) else kind()


def _table(head: tuple[str, ...], rows: str, body_id: str = "") -> str:
    ths = "".join(f"<th>{escape(name)}</th>" for name in head)
    tbody = f'<tbody id="{body_id}">' if body_id else "<tbody>"
    return f'<div class="scroll"><table><thead><tr>{ths}</tr></thead>{tbody}{rows}</tbody></table></div>'


# The server swaps this for sidebar(); pages rendered without a server keep an empty sidebar.
SIDEBAR_SLOT = "<!--lt-sidebar-->"
_VERDICT_LINKS = (("/?verdict=fail", "Failed"), ("/?verdict=inconclusive", "Inconclusive"), ("/?verdict=pass", "Passed"))


def page(title: str, body: str, script: str = "") -> str:
    tail = f"<script>(function(){{{script}}})();</script>" if script else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style><script>{THEME_INIT}</script></head><body>"
        f'<div class="layout"><aside class="sidebar" aria-label="Dashboard sections">{SIDEBAR_SLOT}'
        '<button type="button" id="theme" aria-label="Toggle light or dark theme">Theme</button></aside>'
        f"<main>{body}</main></div><script>{THEME_TOGGLE}</script>{tail}</body></html>"
    )


def sidebar(scenarios: list[str], current: str, runs_dir: Path) -> str:
    def link(href: str, text: str) -> str:
        mark = ' aria-current="page"' if href == current else ""
        return f'<li><a href="{escape(href)}"{mark}>{escape(text)}</a></li>'

    runs_links = link("/", "All runs") + "".join(link(href, text) for href, text in _VERDICT_LINKS)
    scenario_links = "".join(link(_url("scenarios", name), name) for name in scenarios) or '<li class="muted">None yet</li>'
    return (
        '<a class="brand" href="/">LiteTraffic</a>'
        f'<p class="muted small" title="Runs folder">{escape(str(runs_dir))}</p>'
        f"<h2>Runs</h2><ul>{runs_links}</ul>"
        f"<h2>Scenario trends</h2><ul>{scenario_links}</ul>"
        f'<h2>Help</h2><ul>{link("/about", "About this dashboard")}</ul>'
    )


def about(runs_dir: Path) -> str:
    return page(
        "About the LiteTraffic dashboard",
        "<h1>What is this dashboard?</h1>"
        "<p>A local, read-only view of the results LiteTraffic writes to disk. Every <code>litetraffic verify</code> run, "
        "<code>--repeat</code> series and <code>up</code> background activity in the runs folder shows up here; nothing is "
        "sent anywhere and nothing is changed.</p>"
        f'<p class="muted">Runs folder: <code>{escape(str(runs_dir))}</code></p>'
        '<div class="card"><h2>Runs</h2><p>Newest first. Each run has a <strong>verdict</strong>: <span class="badge v-pass">PASS</span> '
        'every declared check held with complete evidence; <span class="badge v-fail">FAIL</span> at least one business check '
        'definitely failed; <span class="badge v-inconclusive">INCONCLUSIVE</span> evidence was missing or incomplete, so it '
        'cannot pass; <span class="badge v-error">ERROR</span> the run could not be evaluated (unreachable target, budget or '
        "fixture failure). Filter by scenario, verdict or seed; the list refreshes every 5 seconds.</p></div>"
        '<div class="card"><h2>Run page</h2><p>Open a run to see its assertions, the first failing samples with '
        "<strong>expected vs actual</strong> values, final observations, per-operation latency, the client-side overlap check, "
        "limitations, and links to <code>report.html</code> and every artifact.</p></div>"
        '<div class="card"><h2>Compare</h2><p>Tick exactly two runs on the Runs page and press Compare. The older run is the '
        "baseline. Compare shows whether correctness regressed, whether the runs are comparable (same scenario digest, seed, "
        "engine and schedule), and the p95 latency change.</p></div>"
        '<div class="card"><h2>Scenario trends</h2><p>Each scenario in the sidebar opens a trend chart of p95 latency per run '
        "over time, with verdict markers and the data as a table.</p></div>"
        '<div class="card"><h2>Safety</h2><p>The dashboard listens on 127.0.0.1 only, answers only requests addressed to '
        "localhost for its own port, never follows symlinks out of the runs folder, and escapes everything it shows.</p></div>",
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
        f'<label>Scenario <select name="scenario" id="scenario-filter">{options(scenarios, filters.get("scenario"))}</select></label>'
        f'<label>Verdict <select name="verdict">{options(VERDICTS, filters.get("verdict"))}</select></label>'
        f'<label>Seed <input name="seed" inputmode="numeric" size="8" value="{escape(filters.get("seed", ""))}"></label>'
        '<button>Filter</button> <a href="/">Clear</a></form>'
    )


def index(entries: list[dict], scenarios: list[str], filters: dict[str, str], runs_dir: Path) -> str:
    rows = "".join(_index_row(entry) for entry in entries)
    table = _table(("Select", "Run", "Kind", "Scenario", "Lifecycle", "Seed", "Finished", "Verdict"), rows, "rows")
    return page(
        "LiteTraffic runs",
        "<h1>Runs</h1>"
        f'<p class="muted">Every LiteTraffic run, repeat series and background activity in <code>{escape(str(runs_dir))}</code>, '
        'newest first. Open a run for its evidence, or tick two runs to compare them. <a href="/about">How to read this</a></p>'
        f"{_filter_form(scenarios, filters)}"
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
        f'<text x="4" y="{_PAD - 14}">{top:g} ms</text><text x="4" y="{_H - _PAD + 4}">0</text>'
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
