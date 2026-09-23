import json
import re
import shutil
import urllib.error
import urllib.request

import pytest

from litetraffic import dashboard_pages, runs
from litetraffic.dashboard_assets import STYLE
from litetraffic.report import _failure_tables
from test_compare import write_run
from test_dashboard import get, runs_dir, server  # noqa: F401 - pytest fixtures
from test_runs import _finish


def _raw(server, path, method="GET"):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


@pytest.mark.parametrize("path", ["/", "/runs/run_a", "/runs/run_a/report.html", "/api/runs", "/runs/missing"])
def test_head_matches_get_without_a_body(server, path):
    get_status, get_headers, get_body = _raw(server, path)
    head_status, head_headers, head_body = _raw(server, path, "HEAD")

    assert head_status == get_status
    assert head_body == b""
    for name in ("Content-Type", "Content-Length", "X-Content-Type-Options"):
        assert head_headers[name] == get_headers[name]
    assert int(head_headers["Content-Length"]) == len(get_body)


def test_run_ids_with_hash_and_question_mark_are_quoted(server, runs_dir):
    name = "run#1?x"
    _finish(write_run(runs_dir / name, name), scenario="odd#scen?", finished_at="2026-02-01T00:00:00+00:00")
    (runs_dir / name / "report.html").write_text("<html>odd</html>")

    _, _, index = get(server, "/")
    assert 'href="/runs/run%231%3Fx"' in index
    assert 'href="/scenarios/odd%23scen%3F"' in index

    status, _, page = get(server, "/runs/run%231%3Fx")
    assert status == 200
    assert 'href="/runs/run%231%3Fx/report.html"' in page
    assert 'href="/scenarios/odd%23scen%3F"' in page

    status, _, trend = get(server, "/scenarios/odd%23scen%3F")
    assert status == 200 and 'href="/runs/run%231%3Fx"' in trend
    assert get(server, "/runs/run%231%3Fx/report.html")[2] == "<html>odd</html>"


def test_report_that_is_not_utf8_is_served_as_bytes(server, runs_dir):
    data = b"<html>\xff\xfe broken \xc3</html>"
    (runs_dir / "run_a" / "report.html").write_bytes(data)

    status, _, body = _raw(server, "/runs/run_a/report.html")

    assert status == 200 and body == data


def _svg_numbers(svg, pattern):
    return [float(value) for value in re.findall(pattern, svg)]


def test_trend_y_label_sits_above_the_highest_marker(server):
    _, _, body = get(server, "/scenarios/checkout")
    svg = body[body.index("<svg") : body.index("</svg>")]

    label = re.search(r'<text[^>]*y="([\d.]+)"[^>]*>[\d.]+ ms</text>', svg)
    assert label
    top_marker = min(_svg_numbers(svg, r'cy="([\d.]+)"'))
    radius = max(_svg_numbers(svg, r' r="([\d.]+)"'))
    assert float(label.group(1)) < top_marker - radius
    assert 'aria-labelledby="chart-title chart-desc"' in svg and '<title id="chart-title">' in svg and '<desc id="chart-desc">' in svg


def test_trend_text_is_at_least_11px_on_a_375px_screen():
    # body padding 16px each side leaves 343px for the viewBox width.
    scale = (375 - 32) / dashboard_pages._W
    narrow = re.search(r"@media \(max-width:\s*(\d+)px\)\{svg\.chart text\{font-size:(\d+)px", STYLE)
    assert narrow and int(narrow.group(1)) >= 375
    assert int(narrow.group(2)) * scale >= 11


def test_long_failing_sample_values_collapse_into_details():
    long_value = "x" * 301
    html = _failure_tables(
        [{"id": "a", "status": "fail", "failures": [{"expected": "short", "actual": long_value, "detail": "y" * 300}]}]
    )

    assert html.count("<details>") == 1
    assert "<td>short</td>" in html and f"<td>{'y' * 300}</td>" in html
    summary = re.search(r"<summary>(.*?)</summary>", html).group(1)
    assert len(summary) < 300 and summary.startswith("xxx")
    assert long_value in html.split("</summary>")[1]


def test_list_runs_tolerates_a_run_deleted_mid_listing(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    write_run(root / "gone", "gone")  # no finished_at, so it sorts by mtime
    _finish(write_run(root / "kept", "kept"), scenario="s", finished_at="2026-01-01T00:00:00+00:00")
    original = runs._run

    def vanishing(path):
        entry = original(path)
        if path.name == "gone":
            shutil.rmtree(path)
        return entry

    monkeypatch.setattr(runs, "_run", vanishing)

    assert "kept" in {entry["run_id"] for entry in runs.list_runs(root)}


def test_index_does_not_fail_when_a_run_vanishes(server, runs_dir, monkeypatch):
    write_run(runs_dir / "gone", "gone")
    original = runs._run

    def vanishing(path):
        entry = original(path)
        if path.name == "gone" and path.exists():
            shutil.rmtree(path)
        return entry

    monkeypatch.setattr(runs, "_run", vanishing)

    assert get(server, "/")[0] == 200


def test_api_scenarios_lists_new_scenarios(server, runs_dir):
    _, content_type, body = get(server, "/api/scenarios?scenario=checkout")
    assert content_type.startswith("application/json")
    assert "fresh" not in json.loads(body)

    _finish(write_run(runs_dir / "run_new", "run_new"), scenario="fresh", finished_at="2026-03-01T00:00:00+00:00")

    scenarios = json.loads(get(server, "/api/scenarios?scenario=checkout")[2])
    assert scenarios == sorted(scenarios) and {"fresh", "checkout"} <= set(scenarios)


def test_auto_refresh_rebuilds_the_scenario_filter(server):
    _, _, body = get(server, "/")

    assert '<select name="scenario" id="scenario-filter">' in body
    assert "/api/scenarios" in body and "scenario-filter" in body.split("<script>")[-1]
