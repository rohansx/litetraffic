import json
import threading
import urllib.error
import urllib.request

import pytest

from litetraffic import dashboard
from litetraffic.cli import main
from test_compare import write_run
from test_runs import _finish


@pytest.fixture
def runs_dir(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    run = _finish(
        write_run(root / "run_a", "run_a", verdict="fail"),
        scenario="<script>alert(1)</script>",
        finished_at="2026-01-02T00:00:00+00:00",
    )
    result = json.loads((run / "result.json").read_text())
    result["assertions"] = [
        {
            "id": "report_complete",
            "status": "fail",
            "samples": 3,
            "failures": [{"sequence": 1, "logical_key": "k1", "expected": "done", "actual": "pending", "detail": None}],
        }
    ]
    result["limitations"] = ["k6 thresholds breached"]
    result["metrics"] = result.get("metrics", {}) | {
        "overlap": {"create_payment": 3},
        "by_operation": {"create_payment": {"samples": 5, "p95": 12.5, "failed_rate": 0.2}},
        "unexpected_http_failure_rate": {"samples": 4, "failed": 1, "rate": 0.25},
    }
    (run / "result.json").write_text(json.dumps(result))
    (run / "report.html").write_text("<html>the report</html>")
    (run / "events").mkdir()
    (run / "events" / "000001.jsonl").write_text('{"event": 1}\n')
    _finish(write_run(root / "run_b", "run_b"), scenario="checkout", finished_at="2026-01-03T00:00:00+00:00")
    _finish(
        write_run(root / "run_c", "run_c", scenario_sha256="other"),
        scenario="checkout",
        finished_at="2026-01-04T00:00:00+00:00",
    )
    series = {"series_id": "series_x", "starting_seed": 42, "lifecycle": "finished", "verdict": "pass", "runs": []}
    (root / "series_x.json").write_text(json.dumps(series))
    (root / "activity_x").mkdir()
    activity = {
        "activity_id": "activity_x",
        "mode": "background",
        "scenario": "checkout",
        "status": "stopped",
        "starting_seed": 5,
        "finished_at": "2026-01-05T00:00:00+00:00",
        "slices": [{"run_id": "run_s1", "seed": 5, "lifecycle": "finished", "iterations": 20, "http_reqs": 2}],
    }
    (root / "activity_x" / "activity.json").write_text(json.dumps(activity))
    (tmp_path / "secret.txt").write_text("outside")
    return root


@pytest.fixture
def server(runs_dir):
    server = dashboard.make_server(runs_dir, 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def get(server, path):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.headers.get("Content-Type"), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read().decode()


def test_server_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_cli_does_not_accept_a_host_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["dashboard", "--host", "0.0.0.0"])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err and "--host" in err


def test_index_lists_runs_and_series_escaped(server):
    status, _, body = get(server, "/")

    assert status == 200
    for text in ("run_a", "run_b", "series_x", "FAIL", "finished", "2026-01-02T00:00:00+00:00", "42", "checkout"):
        assert text in body
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_run_page_shows_verdict_assertions_metrics_limitations_and_report_link(server):
    status, _, body = get(server, "/runs/run_a")

    assert status == 200
    assert "FAIL" in body
    assert "report_complete" in body and "pending" in body and "done" in body
    assert "http_reqs_per_second" in body
    assert "overlap" in body and "create_payment" in body
    assert "unexpected_http_failure_rate" in body
    assert "k6 thresholds breached" in body
    assert 'href="/runs/run_a/report.html"' in body
    assert "<script>alert(1)" not in body


def test_report_is_served_from_the_run_directory(server):
    status, content_type, body = get(server, "/runs/run_a/report.html")

    assert status == 200
    assert content_type.startswith("text/html")
    assert body == "<html>the report</html>"


def test_diff_renders_comparison(server):
    status, _, body = get(server, "/diff?a=run_b&b=run_a")

    assert status == 200
    assert "FAIL" in body and "candidate verdict is fail" in body


def test_diff_of_incompatible_runs_is_inconclusive(server):
    status, _, body = get(server, "/diff?a=run_b&b=run_c")

    assert status == 200
    assert "INCONCLUSIVE" in body and "scenario_sha256" in body


def test_api_runs_returns_json(server, runs_dir):
    status, content_type, body = get(server, "/api/runs")

    assert status == 200
    assert content_type.startswith("application/json")
    entries = {entry["run_id"]: entry for entry in json.loads(body)}
    assert set(entries) == {"run_a", "run_b", "run_c", "series_x", "activity_x"}
    assert entries["activity_x"]["kind"] == "activity"
    assert entries["activity_x"]["verdict"] == "background"


def test_index_shows_activity_with_background_badge_not_a_verdict(server):
    _, _, body = get(server, "/")

    row = next(line for line in body.split("<tr>") if "activity_x" in line)
    assert '<span class="badge">background</span>' in row
    assert "stopped" in row
    for verdict in ("PASS", "FAIL", "INCONCLUSIVE", "UNREADABLE", "ERROR"):
        assert verdict not in row


def test_activity_page_lists_slices_without_a_verdict(server):
    status, _, body = get(server, "/runs/activity_x")

    assert status == 200
    assert '<span class="badge">background</span>' in body
    assert "run_s1" in body and "stopped" in body
    for verdict in ("PASS", "FAIL", "UNREADABLE"):
        assert verdict not in body


@pytest.mark.parametrize(
    "path",
    [
        "/runs/..",
        "/runs/%2E%2E",
        "/runs/..%2Fsecret.txt",
        "/runs/run_a%2F..%2F..",
        "/runs/../secret.txt",
        "/runs/run_a/../../secret.txt",
        "/runs/run_a/%2E%2E%2F%2E%2E%2Fsecret.txt",
        "/runs/run_a/missing.json",
        "/runs/run_a/events",
        "/scenarios/nope",
        "/diff?run=run_a",
        "/runs/series_x.json",
        "/runs/missing",
        "/runs/%00",
        "/diff?a=%00&b=run_a",
        "/diff?a=..&b=run_a",
        "/diff?a=run_a%2F..&b=run_a",
        "/secret.txt",
    ],
)
def test_traversal_and_unknown_paths_return_404(server, path):
    status, _, body = get(server, path)

    assert status == 404
    assert "outside" not in body


@pytest.mark.parametrize("field", ["metrics", "limitations", "assertions"])
@pytest.mark.parametrize("value", [None, 7, "text"])
def test_run_page_tolerates_malformed_result_fields(server, runs_dir, field, value):
    result_path = runs_dir / "run_a" / "result.json"
    result = json.loads(result_path.read_text())
    result[field] = value
    result_path.write_text(json.dumps(result))

    status, _, body = get(server, "/runs/run_a")

    assert status == 200
    assert "FAIL" in body


def test_ctrl_c_shuts_down_cleanly(tmp_path, monkeypatch, capsys):
    def interrupted(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(dashboard.ThreadingHTTPServer, "serve_forever", interrupted)

    assert main(["dashboard", "--runs-dir", str(tmp_path), "--port", "0"]) == 130
    assert "http://127.0.0.1:" in capsys.readouterr().out


def test_port_in_use_is_a_configuration_error(tmp_path, server, capsys):
    assert main(["dashboard", "--runs-dir", str(tmp_path), "--port", str(server.server_address[1])]) == 3
    assert "error" in capsys.readouterr().out


@pytest.mark.parametrize("failures", [["bad"], 5, "text", [None, {"expected": "e1", "actual": "a1"}]])
def test_run_page_tolerates_malformed_failure_samples(server, runs_dir, failures):
    result_path = runs_dir / "run_a" / "result.json"
    result = json.loads(result_path.read_text())
    result["assertions"] = [{"id": "x", "status": "fail", "failures": failures}]
    result_path.write_text(json.dumps(result))

    status, _, body = get(server, "/runs/run_a")

    assert status == 200
    assert "Failing samples" in body
    if isinstance(failures, list) and len(failures) > 1:
        assert "a1" in body


@pytest.mark.parametrize("port", ["70000", "-1", "65536"])
def test_out_of_range_port_is_a_usage_error(tmp_path, port, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["dashboard", "--runs-dir", str(tmp_path), "--port", port])

    assert exc.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_default_port_does_not_collide_with_example_servers():
    # Following the quickstart leaves a demo server on its port; the dashboard must still start.
    import re
    from pathlib import Path

    from litetraffic.cli import _parser

    examples = Path(__file__).resolve().parents[1] / "examples"
    example_ports = {
        int(port)
        for server in examples.glob("*/server.py")
        for port in re.findall(r'"--port", type=int, default=(\d+)', server.read_text())
    }
    assert example_ports
    assert _parser().parse_args(["dashboard"]).port not in example_ports


MALICIOUS = "%3Cscript%3Ealert(1)%3C%2Fscript%3E"


def _rows(body):
    return [row for row in body.split("<tr") if "<td" in row]


def test_index_has_run_checkboxes_and_a_disabled_compare_button(server):
    _, _, body = get(server, "/")

    assert '<input type="checkbox" name="run" value="run_a"' in body
    assert 'name="run" value="series_x"' not in body
    assert 'id="compare" disabled' in body
    assert "=== 2" in body  # the script enables Compare for exactly two selections


def test_diff_from_two_selected_runs_uses_the_older_as_baseline(server):
    # Rows are newest first, so the form submits the newer run first.
    status, _, body = get(server, "/diff?run=run_b&run=run_a")

    assert status == 200
    assert "run_a → run_b" in body


@pytest.mark.parametrize(
    ("query", "present", "absent"),
    [
        ("scenario=checkout", {"run_b", "run_c", "activity_x"}, {"run_a"}),
        ("verdict=fail", {"run_a"}, {"run_b", "run_c", "series_x"}),
        ("seed=42", {"run_a", "run_b", "series_x"}, {"activity_x"}),
        ("scenario=checkout&verdict=pass", {"run_b", "run_c"}, {"run_a", "activity_x"}),
    ],
)
def test_index_and_api_filter_by_query(server, query, present, absent):
    _, _, body = get(server, f"/?{query}")
    listed = {row.split('href="/runs/')[1].split('"')[0] for row in _rows(body) if 'href="/runs/' in row}
    listed |= {"series_x"} if "series_x" in "".join(_rows(body)) else set()
    assert present <= listed and not (absent & listed)

    _, _, api = get(server, f"/api/runs?{query}")
    ids = {entry["run_id"] for entry in json.loads(api)}
    assert present <= ids and not (absent & ids)


def test_filter_form_keeps_selected_values_escaped(server):
    _, _, body = get(server, f"/?scenario={MALICIOUS}&seed=%22%3E")

    assert "<script>alert(1)" not in body
    assert 'value="&quot;&gt;"' in body


def test_verdict_badges_carry_text(server):
    _, _, body = get(server, "/")

    assert '<span class="badge v-fail">FAIL</span>' in body
    assert '<span class="badge v-pass">PASS</span>' in body


def test_index_links_scenarios_to_trend_pages_escaped(server):
    _, _, body = get(server, "/")

    assert 'href="/scenarios/checkout"' in body
    assert 'href="/scenarios/%3Cscript%3Ealert%281%29%3C%2Fscript%3E"' in body


def test_run_page_shows_per_operation_metrics_and_artifact_links(server):
    _, _, body = get(server, "/runs/run_a")

    assert '<span class="badge v-fail">FAIL</span>' in body
    assert "create_payment" in body and "12.5" in body and "0.2" in body
    for name in ("run.json", "result.json", "report.html", "events/000001.jsonl"):
        assert f'href="/runs/run_a/{name}"' in body


@pytest.mark.parametrize(
    ("path", "content_type", "text"),
    [
        ("/runs/run_a/result.json", "application/json", "report_complete"),
        ("/runs/run_a/events/000001.jsonl", "text/plain", '"event": 1'),
    ],
)
def test_artifacts_are_served(server, path, content_type, text):
    status, served_type, body = get(server, path)

    assert status == 200
    assert served_type.startswith(content_type)
    assert text in body


def test_scenario_trend_page_has_accessible_chart_and_table(server):
    status, _, body = get(server, "/scenarios/checkout")

    assert status == 200
    assert '<svg role="img"' in body and "<title" in body and "<desc" in body
    assert "<polyline" in body and 'class="v-pass"' in body
    assert body.index("run_b") < body.index("run_c")  # oldest first
    assert "100" in body and "run_a" not in body


def test_scenario_trend_page_escapes_the_scenario_name(server):
    status, _, body = get(server, f"/scenarios/{MALICIOUS}")

    assert status == 200
    assert "<script>alert(1)" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "run_a" in body


def test_pages_support_light_dark_and_a_stored_toggle(server):
    for path in ("/", "/runs/run_a", "/scenarios/checkout", "/runs/activity_x"):
        _, _, body = get(server, path)
        assert 'name="viewport"' in body
        assert "prefers-color-scheme: dark" in body
        assert 'id="theme"' in body and "localStorage" in body and "try" in body


def test_index_auto_refreshes_from_the_api_and_can_be_disabled(server):
    _, _, body = get(server, "/")

    assert 'id="autorefresh"' in body
    assert "/api/runs" in body and "5000" in body
    assert "textContent" in body  # refreshed rows are built without innerHTML


def test_narrow_tables_scroll_instead_of_splitting_words_and_badges():
    # overflow-wrap:anywhere let cells shrink to one character at 375px ("PA|SS", "Lifecycl|e").
    from litetraffic.dashboard_assets import STYLE

    assert "overflow-wrap:anywhere" not in STYLE.split("h1{overflow-wrap:anywhere}")[1]
    assert "th,.badge{white-space:nowrap}" in STYLE


@pytest.mark.parametrize("path", ["/", "/runs/run_b", "/scenarios/checkout", "/diff?a=run_b&b=run_c", "/about", "/nope"])
def test_every_page_has_the_sidebar(server, path):
    _, _, body = get(server, path)

    assert '<aside class="sidebar"' in body and 'aria-label="Dashboard sections"' in body
    for href in ('href="/"', 'href="/?verdict=fail"', 'href="/?verdict=inconclusive"', 'href="/?verdict=pass"', 'href="/about"'):
        assert href in body
    # One trend link per scenario on disk; the hostile scenario name stays escaped in the sidebar too.
    assert 'href="/scenarios/checkout"' in body
    assert "<script>alert(1)</script>" not in body.split("<main")[0]


def test_misdirected_requests_get_no_sidebar_data(server, runs_dir):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/", headers={"Host": "attacker.example"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    body = exc.value.read().decode()

    assert exc.value.code == 421
    assert "checkout" not in body and str(runs_dir) not in body


def test_sidebar_marks_the_current_page(server):
    _, _, body = get(server, "/about")

    assert '<a href="/about" aria-current="page">' in body
    assert '<a href="/" aria-current="page">' not in body


def test_about_page_explains_the_dashboard(server, runs_dir):
    status, _, body = get(server, "/about")

    assert status == 200
    assert "What is this dashboard" in body
    for text in ("verdict", "Compare", "trend", "127.0.0.1", str(runs_dir)):
        assert text in body


def test_index_says_what_it_shows(server, runs_dir):
    _, _, body = get(server, "/")

    assert "Every LiteTraffic run, repeat series and background activity" in body
    assert str(runs_dir) in body
