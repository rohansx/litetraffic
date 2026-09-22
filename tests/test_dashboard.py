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
    (run / "result.json").write_text(json.dumps(result))
    (run / "report.html").write_text("<html>the report</html>")
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
        "/runs/run_a/run.json",
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
