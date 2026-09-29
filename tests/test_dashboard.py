import json
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from litetraffic import dashboard, explain
from litetraffic.cli import main
from test_compare import write_run
from test_runs import _finish

HOSTILE = "<script>alert(1)</script>"


@pytest.fixture
def runs_dir(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    run = _finish(write_run(root / "run_a", "run_a", verdict="fail"), scenario=HOSTILE, finished_at="2026-01-02T00:00:00+00:00")
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
    }
    (run / "result.json").write_text(json.dumps(result))
    (run / "report.html").write_text("<html>the report</html>")
    (run / "observation.json").write_text(json.dumps([{"orders": 3}]))
    (run / "fixture.json").write_text(json.dumps({"base_url": "http://x"}))
    (run / "events").mkdir()
    (run / "events" / "000001.jsonl").write_text('{"event": 1}\n')
    _finish(write_run(root / "run_b", "run_b"), scenario="checkout", finished_at="2026-01-03T00:00:00+00:00")
    _finish(write_run(root / "run_c", "run_c", scenario_sha256="other", p95=150), scenario="checkout", finished_at="2026-01-04T00:00:00+00:00")
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
def ui_dir(tmp_path):
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text('<!doctype html><div id="root"></div><script src="/assets/app-abc.js"></script>')
    (ui / "assets" / "app-abc.js").write_text("console.log(1)")
    (ui / "assets" / "app-abc.css").write_text("body{}")
    (ui / "favicon.svg").write_text("<svg/>")
    return ui


def _start(runs_dir, ui_dir):
    server = dashboard.make_server(runs_dir, 0, ui_dir)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return server


@pytest.fixture
def server(runs_dir, ui_dir):
    server = _start(runs_dir, ui_dir)
    yield server
    server.shutdown()
    server.server_close()


def raw(server, path, method="GET", host=None):
    headers = {"Host": host} if host else {}
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method=method, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def get(server, path):
    status, headers, body = raw(server, path)
    return status, headers.get("Content-Type"), body.decode()


def get_json(server, path):
    status, content_type, body = get(server, path)
    assert content_type.startswith("application/json")
    return status, json.loads(body)


# --- binding and CLI ---


def test_server_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_cli_does_not_accept_a_host_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["dashboard", "--host", "0.0.0.0"])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err and "--host" in err


def test_ctrl_c_shuts_down_cleanly(tmp_path, monkeypatch, capsys):
    def interrupted(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(dashboard.ThreadingHTTPServer, "serve_forever", interrupted)

    assert main(["dashboard", "--runs-dir", str(tmp_path), "--port", "0"]) == 130
    assert "http://127.0.0.1:" in capsys.readouterr().out


def test_port_in_use_is_a_configuration_error(tmp_path, server, capsys):
    assert main(["dashboard", "--runs-dir", str(tmp_path), "--port", str(server.server_address[1])]) == 3
    assert "error" in capsys.readouterr().out


@pytest.mark.parametrize("port", ["70000", "-1", "65536"])
def test_out_of_range_port_is_a_usage_error(tmp_path, port, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["dashboard", "--runs-dir", str(tmp_path), "--port", port])

    assert exc.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_default_port_does_not_collide_with_example_servers():
    # Following the quickstart leaves a demo server on its port; the dashboard must still start.
    import re

    from litetraffic.cli import _parser

    examples = Path(__file__).resolve().parents[1] / "examples"
    example_ports = {
        int(port)
        for server in examples.glob("*/server.py")
        for port in re.findall(r'"--port", type=int, default=(\d+)', server.read_text())
    }
    assert example_ports
    assert _parser().parse_args(["dashboard"]).port not in example_ports


# --- Host check (DNS rebinding) ---


@pytest.mark.parametrize("path", ["/", "/api/meta", "/api/runs", "/api/scenarios", "/api/runs/run_a", "/api/runs/run_a/artifacts/report.html"])
def test_foreign_host_gets_421_and_no_data(server, runs_dir, path):
    status, headers, body = raw(server, path, host="attacker.example")

    assert status == 421
    text = body.decode()
    for secret in ("checkout", "run_a", str(runs_dir), "the report", "root"):
        assert secret not in text
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("host", ["localhost:{port}", "127.0.0.1:{port}", "[::1]:{port}", "LOCALHOST:{port}"])
def test_loopback_hosts_for_this_port_are_accepted(server, host):
    status, _, _ = raw(server, "/api/meta", host=host.format(port=server.server_address[1]))
    assert status == 200


@pytest.mark.parametrize("host", ["localhost", "localhost:1", "127.0.0.1.evil.example:{port}"])
def test_other_ports_or_hosts_are_rejected(server, host):
    status, _, _ = raw(server, "/api/meta", host=host.format(port=server.server_address[1]))
    assert status == 421


# --- security headers ---

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


@pytest.mark.parametrize("path", ["/", "/runs", "/assets/app-abc.js", "/api/runs", "/api/nope", "/api/runs/missing"])
def test_every_response_has_security_headers(server, path):
    _, headers, _ = raw(server, path)

    assert headers["Content-Security-Policy"] == CSP
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize(("method", "expected"), [("POST", 403), ("PUT", 501), ("DELETE", 501)])
def test_unsupported_methods_are_rejected_with_security_headers(server, method, expected):
    status, headers, body = raw(server, "/api/runs", method=method)

    assert status == expected and b"run_a" not in body
    assert headers["Content-Security-Policy"] == CSP and headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_misdirected_response_has_security_headers(server):
    _, headers, _ = raw(server, "/", host="attacker.example")

    assert headers["Content-Security-Policy"] == CSP and headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize("name", ["report.html", "result.json", "events/000001.jsonl"])
def test_artifacts_are_sandboxed(server, name):
    _, headers, _ = raw(server, f"/api/runs/run_a/artifacts/{name}")

    csp = headers["Content-Security-Policy"]
    assert csp.startswith("sandbox") and "script-src" not in csp and "default-src 'none'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["Referrer-Policy"] == "no-referrer"


# --- /api/meta, /api/runs, /api/scenarios ---


def test_meta_reports_runs_dir_and_version(server, runs_dir):
    from litetraffic import __version__

    status, body = get_json(server, "/api/meta")

    assert status == 200
    assert body == {"explain_cli": explain.explain_provider(), "runs_dir": str(runs_dir.resolve()), "version": __version__}


def test_api_runs_lists_every_kind_newest_first(server):
    status, entries = get_json(server, "/api/runs")

    assert status == 200
    dated = [entry["run_id"] for entry in entries if entry["run_id"] != "series_x"]  # series_x has no stamp: sorts by mtime
    assert dated == ["activity_x", "run_c", "run_b", "run_a"] and len(entries) == 5
    by_id = {entry["run_id"]: entry for entry in entries}
    assert by_id["activity_x"]["kind"] == "activity" and by_id["activity_x"]["verdict"] == "background"
    assert by_id["series_x"]["kind"] == "series"
    assert by_id["run_a"]["scenario"] == HOSTILE  # JSON carries the raw value; the UI escapes on render


@pytest.mark.parametrize(
    ("query", "present", "absent"),
    [
        ("scenario=checkout", {"run_b", "run_c", "activity_x"}, {"run_a"}),
        ("verdict=fail", {"run_a"}, {"run_b", "run_c", "series_x"}),
        ("seed=42", {"run_a", "run_b", "series_x"}, {"activity_x"}),
        ("scenario=checkout&verdict=pass", {"run_b", "run_c"}, {"run_a", "activity_x"}),
        (f"scenario={HOSTILE}", {"run_a"}, {"run_b"}),
        ("scenario=", {"run_a", "run_b"}, set()),
    ],
)
def test_api_runs_filters(server, query, present, absent):
    _, entries = get_json(server, f"/api/runs?{query}")
    ids = {entry["run_id"] for entry in entries}

    assert present <= ids and not (absent & ids)


def test_api_scenarios_lists_new_scenarios_sorted(server, runs_dir):
    _, scenarios = get_json(server, "/api/scenarios")
    assert scenarios == sorted([HOSTILE, "checkout"])

    _finish(write_run(runs_dir / "run_new", "run_new"), scenario="fresh", finished_at="2026-03-01T00:00:00+00:00")

    _, scenarios = get_json(server, "/api/scenarios")
    assert scenarios == sorted(scenarios) and "fresh" in scenarios


def test_empty_or_missing_runs_dir_lists_nothing(tmp_path, ui_dir):
    server = _start(tmp_path / "nope", ui_dir)
    try:
        assert get_json(server, "/api/runs") == (200, [])
        assert get_json(server, "/api/scenarios") == (200, [])
    finally:
        server.shutdown()
        server.server_close()


# --- /api/runs/{id} ---


def test_run_detail_has_run_result_observation_fixture_and_artifacts(server):
    status, body = get_json(server, "/api/runs/run_a")

    assert status == 200
    assert body["run_id"] == "run_a"
    assert body["run"]["scenario"] == HOSTILE
    assert body["result"]["verdict"] == "fail"
    assert body["result"]["assertions"][0]["failures"][0]["actual"] == "pending"
    assert body["observation"] == [{"orders": 3}]
    assert body["fixture"] == {"base_url": "http://x"}
    assert "activity" not in body
    artifacts = {item["path"]: item["size"] for item in body["artifacts"]}
    assert set(artifacts) >= {"run.json", "result.json", "report.html", "observation.json", "events/000001.jsonl"}
    assert artifacts["report.html"] == len("<html>the report</html>")
    assert [item["path"] for item in body["artifacts"]] == sorted(artifacts)


def test_run_detail_without_optional_files_omits_them(server):
    _, body = get_json(server, "/api/runs/run_b")

    assert "observation" not in body and "fixture" not in body and "activity" not in body


def test_activity_detail_has_activity_and_no_result(server):
    status, body = get_json(server, "/api/runs/activity_x")

    assert status == 200
    assert body["activity"]["slices"][0]["run_id"] == "run_s1"
    assert body["run"] is None and body["result"] is None


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"'])
def test_run_detail_tolerates_malformed_result(server, runs_dir, content):
    (runs_dir / "run_a" / "result.json").write_text(content)

    status, body = get_json(server, "/api/runs/run_a")

    assert status == 200 and body["result"] is None


def test_non_finite_numbers_are_sent_as_valid_json(server, runs_dir):
    (runs_dir / "run_a" / "result.json").write_text('{"verdict": "fail", "metrics": {"p": NaN, "q": Infinity}}')

    status, _, text = get(server, "/api/runs/run_a")

    assert status == 200
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["result"]["metrics"] == {"p": None, "q": None}


def test_symlinked_files_are_not_read_or_listed(server, runs_dir):
    outside = runs_dir.parent / "secret.txt"
    (runs_dir / "run_b" / "observation.json").symlink_to(outside)
    (runs_dir / "run_b" / "leak.txt").symlink_to(outside)
    (runs_dir / "run_b" / "linked").symlink_to(runs_dir.parent, target_is_directory=True)

    _, body = get_json(server, "/api/runs/run_b")

    assert "observation" not in body
    paths = [item["path"] for item in body["artifacts"]]
    assert "leak.txt" not in paths and not any(path.startswith("linked") for path in paths)
    assert raw(server, "/api/runs/run_b/artifacts/leak.txt")[0] == 404
    assert raw(server, "/api/runs/run_b/artifacts/linked/secret.txt")[0] == 404


def test_symlinked_run_directory_is_404(server, runs_dir, tmp_path):
    (runs_dir / "linked_run").symlink_to(runs_dir / "run_a", target_is_directory=True)

    assert raw(server, "/api/runs/linked_run")[0] == 404


# --- artifacts ---


@pytest.mark.parametrize(
    ("name", "content_type", "text"),
    [
        ("report.html", "text/html", "<html>the report</html>"),
        ("result.json", "application/json", "report_complete"),
        ("events/000001.jsonl", "text/plain", '"event": 1'),
    ],
)
def test_artifacts_are_served_raw(server, name, content_type, text):
    status, served_type, body = get(server, f"/api/runs/run_a/artifacts/{name}")

    assert status == 200
    assert served_type.startswith(content_type)
    assert text in body


def test_report_that_is_not_utf8_is_served_as_bytes(server, runs_dir):
    data = b"<html>\xff\xfe broken \xc3</html>"
    (runs_dir / "run_a" / "report.html").write_bytes(data)

    status, _, body = raw(server, "/api/runs/run_a/artifacts/report.html")

    assert status == 200 and body == data


def test_run_ids_with_hash_and_question_mark_work_when_quoted(server, runs_dir):
    name = "run#1?x"
    _finish(write_run(runs_dir / name, name), scenario="odd#scen?", finished_at="2026-02-01T00:00:00+00:00")
    (runs_dir / name / "report.html").write_text("<html>odd</html>")

    assert get_json(server, "/api/runs/run%231%3Fx")[1]["run_id"] == name
    assert raw(server, "/api/runs/run%231%3Fx/artifacts/report.html")[2] == b"<html>odd</html>"
    assert get_json(server, "/api/scenarios/odd%23scen%3F/trend")[1][0]["run_id"] == name


# --- traversal and unknown API paths ---


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/..",
        "/api/runs/%2E%2E",
        "/api/runs/..%2Fsecret.txt",
        "/api/runs/run_a%2F..%2F..",
        "/api/runs/../secret.txt",
        "/api/runs/%00",
        "/api/runs/.hidden",
        "/api/runs/missing",
        "/api/runs/series_x.json",
        "/api/runs/run_a/artifacts/../../secret.txt",
        "/api/runs/run_a/artifacts/%2E%2E%2F%2E%2E%2Fsecret.txt",
        "/api/runs/run_a/artifacts/..%2F..%2Fsecret.txt",
        "/api/runs/run_a/artifacts/missing.json",
        "/api/runs/run_a/artifacts/events",
        "/api/runs/run_a/artifacts/",
        "/api/runs/run_a/artifacts",
        "/api/runs/run_a/other",
        "/api/scenarios/nope/trend",
        "/api/scenarios/checkout",
        "/api/diff?baseline=run_a",
        "/api/diff?baseline=%00&candidate=run_a",
        "/api/diff?baseline=..&candidate=run_a",
        "/api/diff?baseline=run_a%2F..&candidate=run_a",
        "/api/nope",
        "/api",
        "/api/",
    ],
)
def test_bad_or_unknown_api_paths_are_json_404(server, path):
    status, content_type, body = get(server, path)

    assert status == 404
    assert content_type.startswith("application/json")
    assert "error" in json.loads(body)
    assert "outside" not in body


# --- /api/diff ---


def test_diff_returns_compare_runs_output(server, runs_dir):
    from litetraffic.compare import compare_runs

    status, body = get_json(server, "/api/diff?baseline=run_b&candidate=run_a")

    assert status == 200
    assert body == compare_runs(runs_dir / "run_b", runs_dir / "run_a")
    assert body["verdict"] == "fail" and body["correctness"]["regression"] is True


def test_diff_of_incompatible_runs_is_inconclusive(server):
    _, body = get_json(server, "/api/diff?baseline=run_b&candidate=run_c")

    assert body["comparable"] is False and "scenario_sha256" in body["incompatibilities"]


def test_diff_of_unreadable_run_is_409(server, runs_dir):
    (runs_dir / "run_a" / "result.json").write_text("not json")

    status, body = get_json(server, "/api/diff?baseline=run_b&candidate=run_a")

    assert status == 409
    assert "cannot read run artifacts" in body["error"]


# --- /api/scenarios/{name}/trend ---


def test_trend_lists_scenario_runs_oldest_first(server):
    status, body = get_json(server, "/api/scenarios/checkout/trend")

    assert status == 200
    assert body == [
        {"run_id": "run_b", "finished_at": "2026-01-03T00:00:00+00:00", "p95": 100.0, "verdict": "pass"},
        {"run_id": "run_c", "finished_at": "2026-01-04T00:00:00+00:00", "p95": 150.0, "verdict": "pass"},
    ]


def test_trend_of_hostile_scenario_name(server):
    _, body = get_json(server, "/api/scenarios/%3Cscript%3Ealert(1)%3C%2Fscript%3E/trend")

    assert [point["run_id"] for point in body] == ["run_a"]


@pytest.mark.parametrize("metrics", [None, {"http_req_duration_ms": {"p95": "x"}}, {"http_req_duration_ms": {"p95": float("nan")}}])
def test_trend_p95_is_null_when_missing_or_malformed(server, runs_dir, metrics):
    path = runs_dir / "run_b" / "result.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"metrics": metrics}))

    _, body = get_json(server, "/api/scenarios/checkout/trend")

    assert body[0]["run_id"] == "run_b" and body[0]["p95"] is None


@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "not json"])
def test_trend_tolerates_a_result_that_is_not_an_object(server, runs_dir, content):
    (runs_dir / "run_b" / "result.json").write_text(content)

    status, body = get_json(server, "/api/scenarios/checkout/trend")

    assert status == 200 and {point["run_id"]: point["p95"] for point in body}["run_b"] is None


# --- SPA serving ---


@pytest.mark.parametrize("path", ["/", "/runs", "/runs/run_a", "/compare?baseline=a&candidate=b", "/scenarios/checkout", "/about"])
def test_client_routes_serve_index_html(server, path):
    status, headers, body = raw(server, path)

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b'<div id="root"></div>' in body
    assert "no-cache" in headers["Cache-Control"]


def test_hashed_assets_are_served_with_long_cache(server):
    status, headers, body = raw(server, "/assets/app-abc.js")

    assert status == 200 and body == b"console.log(1)"
    assert headers["Content-Type"].startswith("text/javascript")
    assert "immutable" in headers["Cache-Control"] and "max-age=31536000" in headers["Cache-Control"]
    assert raw(server, "/assets/app-abc.css")[1]["Content-Type"].startswith("text/css")


def test_top_level_static_files_are_served(server):
    status, headers, body = raw(server, "/favicon.svg")

    assert status == 200 and body == b"<svg/>" and headers["Content-Type"].startswith("image/svg+xml")


@pytest.mark.parametrize("path", ["/assets/missing.js", "/assets/../../runs/run_a/result.json", "/assets/%2E%2E%2Fsecret.txt"])
def test_missing_assets_are_404_not_the_spa(server, path):
    status, _, body = raw(server, path)

    assert status == 404 and b"root" not in body and b"outside" not in body


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2E%2E/secret.txt", "/..%2Fsecret.txt", "/secret.txt"])
def test_spa_never_serves_files_outside_the_bundle(server, path):
    status, _, body = raw(server, path)

    assert b"outside" not in body
    assert status in (200, 404)


def test_symlink_in_bundle_is_not_followed(server, ui_dir):
    (ui_dir / "leak.txt").symlink_to(ui_dir.parent / "secret.txt")

    _, _, body = raw(server, "/leak.txt")

    assert b"outside" not in body


@pytest.mark.parametrize("path", ["/", "/api/runs", "/api/runs/run_a/artifacts/report.html", "/assets/app-abc.js", "/api/nope"])
def test_head_matches_get_without_a_body(server, path):
    get_status, get_headers, get_body = raw(server, path)
    head_status, head_headers, head_body = raw(server, path, "HEAD")

    assert head_status == get_status and head_body == b""
    for name in ("Content-Type", "Content-Length", "Content-Security-Policy"):
        assert head_headers[name] == get_headers[name]
    assert int(head_headers["Content-Length"]) == len(get_body)


def test_missing_bundle_explains_how_to_build(runs_dir, tmp_path):
    server = _start(runs_dir, tmp_path / "no-bundle")
    try:
        status, headers, body = raw(server, "/runs/run_a")
        assert status == 503
        assert headers["Content-Type"].startswith("text/plain")
        assert b"pnpm -C web build" in body
        assert get_json(server, "/api/runs")[0] == 200  # the API still works
    finally:
        server.shutdown()
        server.server_close()


# --- packaging ---


def test_default_bundle_location_is_the_packaged_dashboard_ui():
    assert dashboard.UI_DIR == Path(dashboard.__file__).parent / "dashboard_ui"
    assert (dashboard.UI_DIR / "index.html").is_file()


def test_pyproject_ships_the_dashboard_bundle():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["litetraffic"]
    package = root / "src" / "litetraffic"
    shipped = {path for pattern in patterns for path in package.glob(pattern) if path.is_file()}
    bundle = {path for path in (package / "dashboard_ui").rglob("*") if path.is_file()}

    assert bundle and bundle <= shipped
