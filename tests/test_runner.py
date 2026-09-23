import itertools
import json
import shutil
import stat
from pathlib import Path

import pytest

from litetraffic.runner import RunnerError, _read_events, _read_metrics, verify
from litetraffic.series import repeat_verify
from litetraffic.human import format_verify
from litetraffic.scenario import load_scenario
from test_scenario import manifest, write_bundle


def fake_k6(
    tmp_path: Path,
    events: list[dict],
    returncode: int = 0,
    iterations: int = 20,
    sleep_seconds: int = 0,
    extra_metric_lines: tuple[str, ...] = (),
    failed_tags: tuple[dict, dict] = ({}, {}),
    echo_env: tuple[str, ...] = (),
) -> Path:
    path = tmp_path / "k6"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "if sys.argv[1] == 'version':\n"
        "    print('k6 v2.2.0')\n"
        "    raise SystemExit(0)\n"
        "if os.environ.get('FAKE_K6_ARGV'):\n"
        "    pathlib.Path(os.environ['FAKE_K6_ARGV']).write_text(json.dumps(sys.argv))\n"
        "if os.environ.get('FAKE_K6_ENV'):\n"
        "    pathlib.Path(os.environ['FAKE_K6_ENV']).write_text(json.dumps({k: v for k, v in os.environ.items() if k.startswith('LT_')}))\n"
        "if os.environ.get('FAKE_K6_CWD'):\n"
        "    helper = pathlib.Path('litetraffic/runtime.js')\n"
        "    pathlib.Path(os.environ['FAKE_K6_CWD']).write_text(json.dumps({'cwd': os.getcwd(), 'script': sys.argv[-1], "
        "'runtime': helper.read_text() if helper.is_file() else None}))\n"
        "console = pathlib.Path(sys.argv[sys.argv.index('--console-output') + 1])\n"
        "metrics = pathlib.Path(sys.argv[sys.argv.index('--out') + 1].split('=', 1)[1])\n"
        "events = json.loads(os.environ['FAKE_K6_EVENTS'])\n"
        "run_id = os.environ['LT_RUN_ID']\n"
        "console.write_text(''.join('LT_EVENT ' + json.dumps(e | {'run_id': run_id}) + '\\n' for e in events))\n"
        "metrics.write_text(json.dumps({'type':'Point','metric':'http_reqs','data':{'value':2}}) + '\\n' + "
        f"json.dumps({{'type':'Point','metric':'iterations','data':{{'value':{iterations}}}}}) + '\\n')\n"
        "with metrics.open('a') as stream:\n"
        "    stream.write(json.dumps({'type':'Point','metric':'http_req_duration','data':{'value':10}}) + '\\n')\n"
        "    stream.write(json.dumps({'type':'Point','metric':'http_req_duration','data':{'value':30}}) + '\\n')\n"
        f"    stream.write(json.dumps({{'type':'Point','metric':'http_req_failed','data':{{'value':{int(bool(failed_tags[0]))},'tags':{failed_tags[0]!r}}}}}) + '\\n')\n"
        f"    stream.write(json.dumps({{'type':'Point','metric':'http_req_failed','data':{{'value':1,'tags':{failed_tags[1]!r}}}}}) + '\\n')\n"
        f"    stream.write({''.join(line + chr(10) for line in extra_metric_lines)!r})\n"
        f"echo = ' '.join(os.environ.get(name, '') for name in {list(echo_env)!r})\n"
        "if echo:\n"
        "    print(echo); print(echo, file=sys.stderr)\n"
        "    with console.open('a') as stream: stream.write(echo + '\\n')\n"
        "    with metrics.open('a') as stream:\n"
        "        stream.write(json.dumps({'type':'Point','metric':'lt_echo','data':{'value':1,'tags':{'echo':echo}}}) + '\\n')\n"
        "console.chmod(0o644)\n"
        "metrics.chmod(0o644)\n"
        f"time.sleep({sleep_seconds})\n"
        f"raise SystemExit({returncode})\n"
    )
    path.chmod(0o755)
    return path


_journeys = itertools.count()


def assertion(name: str, passed: bool = True) -> dict:
    """One journey's event; each call is a distinct journey, as the bundled k6 helper keys them."""
    return {"schema_version": 1, "type": "assertion", "assertion": name, "passed": passed, "logical_key": f"j{next(_journeys)}"}


def test_verify_writes_complete_pass_evidence(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify(
        target="http://127.0.0.1:8000",
        scenario=scenario,
        output_dir=tmp_path / "runs",
        k6_path=str(fake_k6(tmp_path, events)),
        seed=42,
    )

    assert result["verdict"] == "pass"
    assert result["lifecycle"] == "finished"
    assert result["completeness"] == "complete"
    assert result["metrics"]["http_reqs"] == 2
    assert result["metrics"]["http_req_duration_ms"]["p95"] == 29.0
    assert result["metrics"]["http_req_failed_rate"] == {"failed": 1, "rate": 0.5, "samples": 2}
    assert result["metrics"]["elapsed_seconds"] > 0
    assert result["metrics"]["http_reqs_per_second"] > 0
    run_dir = tmp_path / "runs" / result["run_id"]
    assert json.loads((run_dir / "result.json").read_text()) == result
    run = json.loads((run_dir / "run.json").read_text())
    assert run["seed"] == 42
    assert run["lifecycle"] == "finished"
    report = (run_dir / "report.html").read_text()
    assert "checkout" in report
    assert "PASS" in report
    assert "accepted_orders_persist" in report
    assert "29.0 ms" in report
    assert "50.0%" in report
    assert "<dd>20 / 20</dd>" in report
    assert "<dt>HTTP requests</dt><dd>2</dd>" in report
    assert "<td>20</td>" in report
    counts = report.replace("<dd>k6 v2.2.0</dd>", "")  # the engine version legitimately ends in ".0"
    assert ".0 /" not in counts and ".0</dd>" not in counts and ".0</td>" not in counts
    first_event = (run_dir / "events" / "000001.jsonl").read_text().splitlines()[0]
    assert json.loads(first_event)["sequence"] == 1


def test_verify_records_run_context_rates_and_honesty_notes(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)), seed=42)

    assert result["mode"] == "verify"
    assert result["planned_journeys_per_second"] == 2.0
    assert result["metrics"]["iterations_per_second"] > 0
    assert "per-arrival lateness not measured" in result["notes"]
    assert "workload is synthetic (no traces supplied)" in result["notes"]
    assert result["limitations"] == [] and result["completeness"] == "complete"
    report = (tmp_path / "runs" / result["run_id"] / "report.html").read_text()
    assert "<dt>Seed</dt><dd>42</dd>" in report
    assert "<dt>Target</dt><dd>http://127.0.0.1:8000</dd>" in report
    assert "<dt>Engine</dt><dd>k6 v2.2.0</dd>" in report
    assert "<dt>HTTP avg</dt><dd>20.0 ms</dd>" in report
    assert "<dt>HTTP p50</dt><dd>20.0 ms</dd>" in report
    assert "<dt>HTTP p95</dt><dd>29.0 ms</dd>" in report
    assert "<dt>HTTP max</dt><dd>30.0 ms</dd>" in report
    assert "<dt>Latency samples</dt><dd>2</dd>" in report
    assert "per-arrival lateness not measured" in report


def test_verify_writes_a_real_lock_file_and_bundle_digest(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    (scenario / "journeys.js").write_text('import "./helper.js";\nexport default function () {}\n')
    (scenario / "helper.js").write_text("export const x = 1;\n")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    run_dir = tmp_path / "runs" / result["run_id"]
    bundle = load_scenario(scenario)
    lock = json.loads((run_dir / "scenario.lock.json").read_text())
    assert lock == {"manifest": manifest(), "engine": "k6 v2.2.0", "files": bundle.files}
    assert set(lock["files"]) == {"journeys.js", "helper.js"}
    assert json.loads((run_dir / "run.json").read_text())["scenario_sha256"] == bundle.digest


def test_verify_reports_dropped_iterations_as_a_limitation(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    dropped = json.dumps({"type": "Point", "metric": "dropped_iterations", "data": {"value": 3}})

    result = verify(
        "http://127.0.0.1:8000", scenario, tmp_path / "runs",
        str(fake_k6(tmp_path, events, extra_metric_lines=(dropped,))),
    )

    assert "k6 dropped 3 iterations (under-delivered load)" in result["limitations"]
    assert result["verdict"] == "inconclusive"


def test_verify_leaves_every_run_file_owner_only(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    run_dir = tmp_path / "runs" / result["run_id"]
    paths = [run_dir, *run_dir.rglob("*")]
    assert {"console.log", "metrics.jsonl"} <= {path.name for path in paths}
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600), path


def test_write_bytes_keeps_the_old_file_when_replace_fails(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    target = tmp_path / "result.json"
    target.write_text('{"verdict": "pass"}\n')
    seen = {}

    def failing_replace(src, dst):
        seen["mode"] = stat.S_IMODE(Path(src).stat().st_mode)
        seen["content"] = Path(src).read_bytes()
        raise OSError("disk full")

    monkeypatch.setattr(runner.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        runner._write_bytes(target, b'{"verdict": "error"}\n')

    assert target.read_text() == '{"verdict": "pass"}\n'
    assert seen == {"mode": 0o600, "content": b'{"verdict": "error"}\n'}
    assert [path.name for path in tmp_path.iterdir()] == ["result.json"]


def test_write_bytes_replaces_a_permissive_file_with_an_owner_only_one(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    target = tmp_path / "report.html"
    target.write_text("old")
    target.chmod(0o644)
    runner._write_bytes(target, b"new")

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_verify_writes_every_controller_artifact_atomically(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    written = set()
    original = runner._write_bytes

    def recording(path, data):
        written.add(path)
        original(path, data)

    monkeypatch.setattr(runner, "_write_bytes", recording)
    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    run_dir = tmp_path / "runs" / result["run_id"]
    controller_files = {path for path in run_dir.rglob("*") if path.is_file()} - {
        run_dir / "console.log",
        run_dir / "metrics.jsonl",
    }
    assert controller_files == written


def test_verify_reports_definite_assertion_failure(tmp_path, monkeypatch):
    data = manifest(assertions=["accepted_orders_persist"])
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist", False)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "fail"
    assert result["assertions"] == [
        {
            "id": "accepted_orders_persist",
            "status": "fail",
            "samples": 1,
            "failures": [{"sequence": 1, "logical_key": events[0]["logical_key"], "expected": None, "actual": None, "detail": None}],
        }
    ]


def test_verify_fails_when_final_observation_disagrees_with_fixture(tmp_path, monkeypatch):
    data = manifest(
        assertions=["accepted_orders_persist", "ledger_total"],
        observation={"path": "/reports/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setattr(
        "litetraffic.runner.observe",
        lambda *args, **kwargs: {"assertion": "ledger_total", "status": "fail", "expected": {"/total": 1000}, "actual": {"/total": 900}},
    )

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "fail"
    assert {row["id"]: row["status"] for row in result["assertions"]} == {
        "accepted_orders_persist": "pass",
        "ledger_total": "fail",
    }
    assert result["metrics"]["observer_requests"] == 1
    assert result["metrics"]["total_http_reqs"] == 3
    assert json.loads((tmp_path / "runs" / result["run_id"] / "observation.json").read_text())["actual"] == {"/total": 900}


def test_verify_is_inconclusive_when_final_observation_is_unavailable(tmp_path, monkeypatch):
    data = manifest(
        assertions=["accepted_orders_persist", "ledger_total"],
        observation={"path": "/reports/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setattr(
        "litetraffic.runner.observe",
        lambda *args, **kwargs: {"assertion": "ledger_total", "status": "unknown", "reason": "observer HTTP 503"},
    )

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "inconclusive"
    assert result["assertions"][-1] == {"id": "ledger_total", "status": "unknown", "samples": 0, "reason": "observer HTTP 503"}
    assert "ledger_total  unknown  0  (observer HTTP 503)" in format_verify(result, tmp_path / "runs")


def test_verify_missing_observer_header_env_is_inconclusive_without_a_request(tmp_path, monkeypatch):
    monkeypatch.delenv("LT_DB_KEY", raising=False)
    data = manifest(
        assertions=["accepted_orders_persist", "ledger_total"],
        observation={"path": "/rows", "assertion": "ledger_total", "expected": {"": {"len": 1}}, "headers_env": {"apikey": "LT_DB_KEY"}},
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "inconclusive"
    assert result["metrics"]["observer_requests"] == 0
    observation = json.loads((tmp_path / "runs" / result["run_id"] / "observation.json").read_text())
    assert observation["reason"] == "observer header env LT_DB_KEY missing"


def test_verify_escapes_scenario_names_in_the_html_report(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario", manifest(name="<script>alert(1)</script>"))
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    report = (tmp_path / "runs" / result["run_id"] / "report.html").read_text()
    assert "<script>" not in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report


def test_verify_counts_the_html_report_toward_the_artifact_budget(tmp_path, monkeypatch):
    budgets = manifest()["budgets"] | {"max_artifact_bytes": 1}
    scenario = write_bundle(tmp_path / "scenario", manifest(budgets=budgets))
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    report = (tmp_path / "runs" / result["run_id"] / "report.html").read_text()
    assert result["verdict"] == "error"
    assert "artifact budget exceeded" in result["limitations"][0]
    assert "ERROR" in report


def test_verify_enforces_the_observed_request_budget(tmp_path, monkeypatch):
    data = manifest(
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        journeys=[{"name": "purchase", "max_requests": 1, "max_writes": 1}],
        budgets=manifest()["budgets"] | {"max_requests": 1, "max_write_attempts": 1},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=1)))

    assert result["verdict"] == "error"
    assert any("request budget exceeded" in item for item in result["limitations"])


def test_verify_is_inconclusive_when_required_evidence_is_missing(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))

    assert result["verdict"] == "inconclusive"
    assert result["completeness"] == "incomplete"
    assert "missing assertion evidence" in result["limitations"][0]


def test_verify_is_inconclusive_when_delivery_differs_from_plan(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify(
        "http://example.test",
        scenario,
        tmp_path / "runs",
        str(fake_k6(tmp_path, events, iterations=19)),
    )

    assert result["verdict"] == "inconclusive"
    assert any("delivered journeys 19" in limitation for limitation in result["limitations"])


def test_verify_never_passes_when_k6_admits_more_journeys_than_planned(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=21)))

    assert result["verdict"] != "pass"
    assert any(item.startswith("delivered journeys 21") and item.endswith("planned journeys 20") for item in result["limitations"])


def test_verify_is_inconclusive_when_some_journeys_lack_assertions(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(19)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "inconclusive"
    assert "accepted_orders_persist (19/20)" in result["limitations"][0]


def test_verify_rejects_an_unpinned_engine(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")
    k6 = fake_k6(tmp_path, [])
    k6.write_text(k6.read_text().replace("k6 v2.2.0", "k6 v1.8.0"))

    with pytest.raises(RunnerError, match="expected v2.2.0"):
        verify("http://example.test", scenario, tmp_path / "runs", str(k6))


def test_verify_disables_k6_redirects(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")
    monkeypatch.setenv("FAKE_K6_ARGV", str(tmp_path / "argv.json"))

    verify("http://localhost:8000", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))

    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[argv.index("--max-redirects") + 1] == "0"


@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254/", "http://[fe80::1]/", "http://[::ffff:169.254.169.254]/", "http://metadata.google.internal/"],
)
def test_verify_rejects_link_local_and_metadata_targets(tmp_path, monkeypatch, target):
    scenario = write_bundle(tmp_path / "scenario")
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")

    with pytest.raises(RunnerError, match="link-local/metadata address not allowed"):
        verify(target, scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))
    assert not (tmp_path / "runs").exists()


def test_verify_freezes_the_seeded_resolved_schedule(tmp_path, monkeypatch):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "spiky",
            "duration_seconds": 10,
            "baseline_rate": 1,
            "spike_rate": 3,
            "spike_seconds": 1,
            "spikes": 1,
        },
    }
    data = manifest(
        schedule=schedule,
        budgets=manifest()["budgets"] | {"max_requests": 36, "max_write_attempts": 12},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(12)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify(
        "http://example.test",
        scenario,
        tmp_path / "runs",
        str(fake_k6(tmp_path, events, iterations=12)),
        seed=42,
    )

    run = json.loads((tmp_path / "runs" / result["run_id"] / "run.json").read_text())
    assert run["resolved_schedule"] == [
        phase.model_dump(exclude={"admitted_journeys"})
        for phase in load_scenario(scenario).manifest.schedule.resolve(seed=42)
    ]


def test_verify_finalizes_partial_evidence_on_timeout(tmp_path, monkeypatch):
    data = manifest(
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 3, "max_requests": 3, "max_write_attempts": 1},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify(
        "http://example.test",
        scenario,
        tmp_path / "runs",
        str(fake_k6(tmp_path, events, iterations=1, sleep_seconds=60)),
    )

    run_dir = tmp_path / "runs" / result["run_id"]
    assert result["lifecycle"] == "timed_out"
    assert result["verdict"] == "inconclusive"
    assert result["completeness"] == "incomplete"
    assert json.loads((run_dir / "result.json").read_text()) == result
    assert json.loads((run_dir / "run.json").read_text())["lifecycle"] == "timed_out"


def test_verify_records_an_engine_crash(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify(
        "http://example.test",
        scenario,
        tmp_path / "runs",
        str(fake_k6(tmp_path, events, returncode=7)),
    )

    assert result["lifecycle"] == "crashed"
    assert result["verdict"] == "error"
    assert result["engine_exit_code"] == 7
    assert "k6 exited with status 7" in result["limitations"]


def test_verify_treats_a_k6_threshold_breach_as_a_finished_failing_run(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, returncode=99)))

    assert result["lifecycle"] == "finished"
    assert result["engine_exit_code"] == 99
    assert result["limitations"] == ["k6 thresholds breached"]
    assert result["verdict"] == "fail"
    assert all(row["status"] == "pass" for row in result["assertions"])
    run_dir = tmp_path / "runs" / result["run_id"]
    assert json.loads((run_dir / "run.json").read_text())["lifecycle"] == "finished"


def test_verify_finalizes_when_the_user_cancels(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    calls = 0

    def interrupt_once(process, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return process.communicate(timeout=timeout)

    monkeypatch.setattr(runner, "_communicate", interrupt_once)
    result = runner.verify(
        "http://example.test",
        scenario,
        tmp_path / "runs",
        str(fake_k6(tmp_path, events, sleep_seconds=60)),
    )

    assert result["lifecycle"] == "cancelled"
    assert result["verdict"] == "inconclusive"


@pytest.mark.parametrize(("returncode", "sleep_seconds", "expected_lifecycle"), [(0, 0, "finished"), (7, 0, "crashed"), (0, 60, "timed_out")])
def test_owned_fixture_is_cleaned_after_engine_exit(tmp_path, monkeypatch, returncode, sleep_seconds, expected_lifecycle):
    import litetraffic.runner as runner

    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 13, "max_requests": 5, "max_write_attempts": 3},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    calls = []
    monkeypatch.setattr(runner, "create_fixture", lambda target, config, run_id: calls.append(("create", run_id)) or {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda target, config, run_id, fixture_id: calls.append(("delete", run_id, fixture_id)) or {"status": "deleted", "requests": 1})

    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, returncode=returncode, iterations=1, sleep_seconds=sleep_seconds)))

    assert result["lifecycle"] == expected_lifecycle
    if expected_lifecycle == "timed_out":
        assert "engine stopped after its 3-second share of the 13-second budget" in result["limitations"]
    assert result["metrics"]["fixture_requests"] == 2
    assert result["metrics"]["total_http_reqs"] == 4
    assert calls == [("create", result["run_id"]), ("delete", result["run_id"], "owned-1")]
    assert json.loads((tmp_path / "runs" / result["run_id"] / "fixture.json").read_text())["cleanup"]["status"] == "deleted"


def test_owned_fixture_setup_failure_prevents_traffic(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    data = manifest(fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}}, budgets=manifest()["budgets"] | {"max_requests": 62, "max_write_attempts": 22, "max_seconds": 22})
    scenario = write_bundle(tmp_path / "scenario", data)
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "error", "reason": "fixture create HTTP 503", "requests": 1})
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))
    assert result["verdict"] == "error"
    assert result["engine_exit_code"] is None
    assert "fixture create HTTP 503" in result["limitations"]


def test_owned_fixture_cleanup_failure_cannot_pass(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    data = manifest(fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}}, budgets=manifest()["budgets"] | {"max_requests": 62, "max_write_attempts": 22, "max_seconds": 22})
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: {"status": "error", "reason": "fixture cleanup HTTP 503", "requests": 1})
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))
    assert result["verdict"] == "error"
    assert "fixture cleanup HTTP 503" in result["limitations"]


def test_owned_fixture_is_cleaned_when_user_cancels(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 13, "max_requests": 5, "max_write_attempts": 3},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps([assertion("accepted_orders_persist")]))
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    cleaned = []
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: cleaned.append(args[-1]) or {"status": "deleted", "requests": 1})
    calls = 0
    def interrupt_once(process, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return process.communicate(timeout=timeout)
    monkeypatch.setattr(runner, "_communicate", interrupt_once)

    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], sleep_seconds=60)))
    assert result["lifecycle"] == "cancelled"
    assert cleaned == ["owned-1"]


def test_repeat_verify_runs_consecutive_seeds_and_exposes_mixed_results(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    calls = []

    def run_once(target, scenario, output_dir, k6_path, seed):
        calls.append(seed)
        verdict = "fail" if seed == 43 else "pass"
        return {
            "run_id": f"run-{seed}",
            "seed": seed,
            "verdict": verdict,
            "lifecycle": "finished",
            "report": "report.html",
        }

    monkeypatch.setattr(runner, "verify", run_once)

    result = repeat_verify("http://example.test", Path("scenario"), tmp_path, repeats=3, seed=42)

    assert calls == [42, 43, 44]
    assert result["verdict"] == "fail"
    assert result["consistent"] is False
    assert [run["verdict"] for run in result["runs"]] == ["pass", "fail", "pass"]
    assert json.loads((tmp_path / result["result"]).read_text()) == result


def test_repeat_verify_same_seed_repeats_one_seed_and_reports_dispersion(tmp_path, monkeypatch):
    import statistics

    import litetraffic.runner as runner

    calls = []
    p95s, failed, rps = [10.0, 20.0, 30.0, 40.0, 50.0], [0.0, 0.1, 0.0, 0.2, 0.0], [5.0, 6.0, 7.0, 8.0, 9.0]

    def run_once(target, scenario, output_dir, k6_path, seed):
        index = len(calls)
        calls.append(seed)
        return {
            "run_id": f"run-{index}",
            "seed": seed,
            "verdict": "pass",
            "lifecycle": "finished",
            "metrics": {
                "http_req_duration_ms": {"p95": p95s[index]},
                "http_req_failed_rate": {"rate": failed[index]},
                "http_reqs_per_second": rps[index],
            },
        }

    monkeypatch.setattr(runner, "verify", run_once)

    result = repeat_verify("http://example.test", Path("scenario"), tmp_path, repeats=5, seed=42, same_seed=True)

    assert calls == [42] * 5
    assert result["same_seed"] is True
    for key, values in (("p95_ms", p95s), ("http_req_failed_rate", failed), ("http_reqs_per_second", rps)):
        assert result["dispersion"][key] == {
            "min": min(values),
            "max": max(values),
            "mean": statistics.mean(values),
            "stdev": statistics.stdev(values),
        }


def test_repeat_verify_dispersion_skips_runs_without_the_metric(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    runs = iter([{"http_reqs_per_second": 4.0}, {}])
    monkeypatch.setattr(
        runner,
        "verify",
        lambda *args: {"run_id": "r", "seed": 0, "verdict": "pass", "lifecycle": "finished", "metrics": next(runs)},
    )

    result = repeat_verify("http://example.test", Path("scenario"), tmp_path, repeats=2)

    assert result["same_seed"] is False
    assert result["dispersion"]["http_reqs_per_second"] == {"min": 4.0, "max": 4.0, "mean": 4.0, "stdev": None}
    assert result["dispersion"]["p95_ms"] is None


def test_repeat_verify_restricts_the_series_output_dir(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    output = tmp_path / "runs"
    output.mkdir(mode=0o755)
    output.chmod(0o755)
    monkeypatch.setattr(
        runner, "verify", lambda *args: {"run_id": "r", "verdict": "pass", "lifecycle": "finished"}
    )

    result = repeat_verify("http://example.test", Path("scenario"), output, repeats=2)

    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / result["result"]).stat().st_mode) == 0o600


def test_repeat_verify_stops_after_user_cancellation(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    calls = []

    def cancel(target, scenario, output_dir, k6_path, seed):
        calls.append(seed)
        return {"run_id": "cancelled", "seed": seed, "verdict": "inconclusive", "lifecycle": "cancelled"}

    monkeypatch.setattr(runner, "verify", cancel)

    result = repeat_verify("http://example.test", Path("scenario"), tmp_path, repeats=3, seed=42)

    assert calls == [42]
    assert result["lifecycle"] == "cancelled"
    assert result["completed_runs"] == 1


NON_OBJECT_METRIC_LINES = ("[1]", "5", '{"type":"Point","data":null}')


def test_read_metrics_counts_non_object_lines_as_malformed(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text("\n".join(NON_OBJECT_METRIC_LINES) + "\n")

    _, malformed = _read_metrics(path)

    assert malformed == 3


def test_verify_survives_non_object_metric_lines(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events, extra_metric_lines=NON_OBJECT_METRIC_LINES)

    result = verify(
        target="http://127.0.0.1:8000",
        scenario=scenario,
        output_dir=tmp_path / "runs",
        k6_path=str(k6),
    )

    assert (tmp_path / "runs" / result["run_id"] / "result.json").exists()
    assert "ignored 3 malformed metric record(s)" in result["limitations"]


REFUSED = {"status": "0", "error_code": "1212"}


def test_verify_reports_an_unreachable_target_as_error(tmp_path, monkeypatch):
    data = manifest(assertions=["accepted_orders_persist"])
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist", False) for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events, failed_tags=(REFUSED, REFUSED))

    result = verify("http://127.0.0.1:9", scenario, tmp_path / "runs", str(k6))

    assert result["verdict"] == "error"
    assert "target unreachable: all 2 requests failed before an HTTP response" in result["limitations"]
    assert result["assertions"] == [{"id": "accepted_orders_persist", "status": "unknown", "samples": 20}]


def test_verify_still_fails_when_the_target_answers_with_http_500(tmp_path, monkeypatch):
    data = manifest(assertions=["accepted_orders_persist"])
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist", False) for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events, failed_tags=(REFUSED, {"status": "500"}))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(k6))

    assert result["verdict"] == "fail"
    assert not any("unreachable" in item for item in result["limitations"])


def test_verify_fails_when_every_request_gets_an_http_4xx(tmp_path, monkeypatch):
    # Real k6 also tags HTTP 4xx/5xx responses with an error_code (e.g. 1403); only status "0" means no response.
    data = manifest(assertions=["accepted_orders_persist"])
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist", False) for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    denied = {"status": "403", "error_code": "1403"}
    k6 = fake_k6(tmp_path, events, failed_tags=(denied, denied))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(k6))

    assert result["verdict"] == "fail"
    assert not any("unreachable" in item for item in result["limitations"])


def test_read_events_accepts_optional_evidence_fields_and_rejects_wrong_types(tmp_path):
    base = assertion("a", False) | {"run_id": "r"}
    records = [
        base | {"expected": {"total": 1250}, "actual": [1, None], "detail": "x" * 500, "logical_key": "k1"},
        base,
        base | {"detail": "x" * 501},
        base | {"detail": 5},
        base | {"logical_key": 7},
        base | {"logical_key": None},
    ]
    path = tmp_path / "console.log"
    path.write_text("".join(f"LT_EVENT {json.dumps(record)}\n" for record in records))

    events, malformed = _read_events(path, "r")

    assert malformed == 4
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["expected"] == {"total": 1250} and events[0]["actual"] == [1, None]


def test_verify_records_the_first_three_failing_samples(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario", manifest(assertions=["accepted_orders_persist"]))
    failing = [
        assertion("accepted_orders_persist", False)
        | {"logical_key": f"k{index}", "expected": 1250, "actual": index, "detail": "<b>total</b>"}
        for index in range(4)
    ]
    events = [assertion("accepted_orders_persist"), *failing, assertion("accepted_orders_persist", False)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    row = result["assertions"][0]
    assert row["status"] == "fail" and row["samples"] == 6
    assert row["failures"] == [
        {"sequence": index + 2, "logical_key": f"k{index}", "expected": 1250, "actual": index, "detail": "<b>total</b>"}
        for index in range(3)
    ]
    report = (tmp_path / "runs" / result["run_id"] / "report.html").read_text()
    assert "<th>Expected</th><th>Actual</th>" in report
    assert "&lt;b&gt;total&lt;/b&gt;" in report and "<b>total</b>" not in report
    assert "<td>1250</td><td>2</td>" in report


def test_verify_copies_observer_expected_and_actual_into_the_assertion(tmp_path, monkeypatch):
    data = manifest(
        assertions=["accepted_orders_persist", "ledger_total"],
        observation={"path": "/reports/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setattr(
        "litetraffic.runner.observe",
        lambda *args, **kwargs: {"assertion": "ledger_total", "status": "fail", "expected": {"/total": 1000}, "actual": {"/total": 900}},
    )

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["assertions"] == [
        {"id": "accepted_orders_persist", "status": "pass", "samples": 20},
        {"id": "ledger_total", "status": "fail", "samples": 1, "expected": {"/total": 1000}, "actual": {"/total": 900}},
    ]
    report = (tmp_path / "runs" / result["run_id"] / "report.html").read_text()
    assert "<td>{&quot;/total&quot;: 1000}</td><td>{&quot;/total&quot;: 900}</td>" in report


def two_journey_bundle(tmp_path: Path) -> Path:
    schedule = {"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 2}]}
    return write_bundle(tmp_path / "scenario", manifest(schedule=schedule, assertions=["accepted_orders_persist"]))


def test_verify_does_not_let_a_duplicate_logical_key_stand_in_for_a_missing_journey(tmp_path, monkeypatch):
    scenario = two_journey_bundle(tmp_path)
    events = [assertion("accepted_orders_persist") | {"logical_key": "A"} for _ in range(2)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=2)))

    assert result["assertions"][0]["status"] == "unknown"
    assert result["verdict"] == "inconclusive"
    assert "duplicate evidence for A" in result["limitations"]


def test_verify_passes_only_distinct_logical_keys_covering_every_planned_journey(tmp_path, monkeypatch):
    scenario = two_journey_bundle(tmp_path)
    events = [assertion("accepted_orders_persist") | {"logical_key": key} for key in ("A", "B")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=2)))

    assert result["assertions"][0]["status"] == "pass", result["limitations"]
    assert result["verdict"] == "pass"


@pytest.mark.parametrize("key", [None, ""])
def test_verify_does_not_pass_evidence_without_journey_identity(tmp_path, monkeypatch, key):
    scenario = two_journey_bundle(tmp_path)
    unkeyed = {k: v for k, v in assertion("accepted_orders_persist").items() if k != "logical_key"}
    events = [unkeyed if key is None else unkeyed | {"logical_key": key} for _ in range(2)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=2)))

    assert result["assertions"][0]["status"] == "unknown"
    assert result["verdict"] == "inconclusive"
    assert "evidence without journey identity for accepted_orders_persist" in result["limitations"]


@pytest.mark.parametrize(
    ("keys", "status"),
    [((None, None), "unknown"), (("A", "A"), "unknown"), (("A", "B"), "pass"), (("A", None), "unknown"), (("A",), "unknown")],
)
def test_evaluate_assertions_requires_one_unique_key_per_planned_journey(keys, status):
    from litetraffic.evidence import evaluate_assertions

    events = [
        {"assertion": "a", "passed": True} | ({} if key is None else {"logical_key": key}) for key in keys
    ]
    rows = evaluate_assertions(["a"], events, [], 2)[0]
    assert rows[0]["status"] == status


@pytest.mark.parametrize("fixture_setup_seconds", [0, 10])
def test_throughput_is_measured_over_the_engine_window_only(tmp_path, monkeypatch, fixture_setup_seconds):
    from datetime import UTC, datetime, timedelta

    import litetraffic.runner as runner

    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 13, "max_requests": 5, "max_write_attempts": 3},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]

    def tick():
        clock[0] += timedelta(seconds=1)
        return clock[0]

    def slow_create(*args):
        clock[0] += timedelta(seconds=fixture_setup_seconds)
        return {"status": "created", "fixture_id": "owned-1", "requests": 1}

    monkeypatch.setattr(runner, "_now", tick, raising=False)
    monkeypatch.setattr(runner, "create_fixture", slow_create)
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: {"status": "deleted", "requests": 1})

    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=1)))

    run = json.loads((tmp_path / "runs" / result["run_id"] / "run.json").read_text())
    engine_seconds = (datetime.fromisoformat(run["engine_finished_at"]) - datetime.fromisoformat(run["engine_started_at"])).total_seconds()
    assert engine_seconds == 1
    assert result["metrics"]["iterations_per_second"] == 1.0
    assert result["metrics"]["http_reqs_per_second"] == 2.0


def owned_fixture_observed_bundle(tmp_path: Path) -> Path:
    data = manifest(
        assertions=["accepted_orders_persist", "ledger_total"],
        fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}},
        observation={"path": "/reports/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 18, "max_requests": 6, "max_write_attempts": 3},
    )
    return write_bundle(tmp_path / "scenario", data)


def cancel(*args, **kwargs):
    raise KeyboardInterrupt


def test_cancel_during_fixture_create_finalizes_without_traffic(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    cleaned = []
    monkeypatch.setattr(runner, "create_fixture", cancel)
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: cleaned.append(args) or {"status": "deleted", "requests": 1})
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))

    run_dir = tmp_path / "runs" / result["run_id"]
    assert result["lifecycle"] == "cancelled"
    assert result["verdict"] == "inconclusive"
    assert result["engine_exit_code"] is None
    assert "run cancelled by user" in result["limitations"]
    assert cleaned == []
    assert json.loads((run_dir / "result.json").read_text())["lifecycle"] == "cancelled"
    assert json.loads((run_dir / "run.json").read_text())["lifecycle"] == "cancelled"
    assert json.loads((run_dir / "fixture.json").read_text())["create"]["status"] == "cancelled"


def test_cancel_during_observe_still_cleans_up_the_fixture(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps([assertion("accepted_orders_persist")]))
    cleaned = []
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "observe", cancel)
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: cleaned.append(args[-1]) or {"status": "deleted", "requests": 1})
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1)))

    assert result["lifecycle"] == "cancelled"
    assert result["verdict"] == "inconclusive"
    assert cleaned == ["owned-1"]
    ledger = next(row for row in result["assertions"] if row["id"] == "ledger_total")
    assert ledger["status"] == "unknown"


def test_verify_sends_all_traffic_to_the_canonical_target(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps([assertion("accepted_orders_persist")]))
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "env.json"))
    targets = []
    monkeypatch.setattr(runner, "create_fixture", lambda target, *args: targets.append(target) or {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda target, *args: targets.append(target) or {"status": "deleted", "requests": 1})
    monkeypatch.setattr(runner, "observe", lambda target, *args, **kwargs: targets.append(target) or {"assertion": "ledger_total", "status": "pass"})

    runner.verify("http://app。example。test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1)))

    assert json.loads((tmp_path / "env.json").read_text())["LT_TARGET"] == "http://app.example.test"
    assert targets == ["http://app.example.test"] * 3


def test_cancel_during_fixture_cleanup_is_an_error(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps([assertion("accepted_orders_persist")]))
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "observe", lambda *args, **kwargs: {"assertion": "ledger_total", "status": "pass"})
    monkeypatch.setattr(runner, "cleanup_fixture", cancel)
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1)))

    assert result["lifecycle"] == "cancelled"
    assert result["verdict"] == "error"
    assert "fixture cleanup cancelled; fixture owned-1 may remain" in result["limitations"]


@pytest.mark.parametrize(
    ("stage", "name"),
    [
        ("fixture", "fixture_pool"),
        ("engine", "staged"),
        ("parsing", "_read_events"),
        ("observing", "observe"),
        ("finalizing", "evaluate_assertions"),
        ("redaction", "redact"),
        ("report", "render_report"),
    ],
)
def test_cancel_at_any_stage_finalizes_the_run_as_cancelled(tmp_path, monkeypatch, stage, name):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps([assertion("accepted_orders_persist")]))
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "observe", lambda *args, **kwargs: {"assertion": "ledger_total", "status": "pass"})
    cleaned = []
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: cleaned.append(args[-1]) or {"status": "deleted", "requests": 1})
    original, interrupted = getattr(runner, name), []

    def interrupt_once(*args, **kwargs):
        if not interrupted:
            interrupted.append(stage)
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, name, interrupt_once)
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1)))

    run_dir = tmp_path / "runs" / result["run_id"]
    assert interrupted == [stage]
    assert result["lifecycle"] == "cancelled"
    assert result["verdict"] == "inconclusive"
    assert "run cancelled by user" in result["limitations"]
    assert cleaned == ([] if stage == "fixture" else ["owned-1"])  # the pool is built before the fixture exists
    assert json.loads((run_dir / "result.json").read_text()) == result
    assert json.loads((run_dir / "run.json").read_text())["lifecycle"] == "cancelled"


def test_run_json_records_each_lifecycle_stage_as_it_happens(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps([assertion("accepted_orders_persist")]))
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "observe", lambda *args, **kwargs: {"assertion": "ledger_total", "status": "pass"})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: {"status": "deleted", "requests": 1})
    stages = []
    write_json = runner._write_json

    def record(path, value):
        if path.name == "run.json":
            stages.append(value["lifecycle"])
        write_json(path, value)

    monkeypatch.setattr(runner, "_write_json", record)
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1)))

    assert result["lifecycle"] == "finished"
    assert stages == ["preparing", "running", "observing", "finalizing", "finished"]


def test_verify_writes_artifacts_json_last_with_every_file_hashed(tmp_path, monkeypatch):
    import hashlib

    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    run_dir = tmp_path / "runs" / result["run_id"]
    manifest_path = run_dir / "artifacts.json"
    artifacts = json.loads(manifest_path.read_text())
    files = {path.relative_to(run_dir).as_posix(): path for path in run_dir.rglob("*") if path.is_file()}
    del files["artifacts.json"]
    assert artifacts["schema_version"] == 1
    assert artifacts["run_id"] == result["run_id"]
    assert {entry["path"] for entry in artifacts["files"]} == set(files)
    assert "events/000001.jsonl" in files
    for entry in artifacts["files"]:
        data = files[entry["path"]].read_bytes()
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert artifacts["total_bytes"] == sum(path.stat().st_size for path in files.values())
    assert all(path.stat().st_mtime_ns <= manifest_path.stat().st_mtime_ns for path in files.values())
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600


def test_artifact_budget_excludes_the_artifacts_manifest(tmp_path):
    from litetraffic.artifacts import artifact_files

    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "000001.jsonl").write_bytes(b"abc")
    (tmp_path / "result.json").write_bytes(b"{}")
    (tmp_path / "artifacts.json").write_bytes(b"ignored")

    files = artifact_files(tmp_path)

    assert [entry["path"] for entry in files] == ["events/000001.jsonl", "result.json"]
    assert sum(entry["bytes"] for entry in files) == 5


def point(metric: str, value: float, **tags: str) -> str:
    return json.dumps({"type": "Point", "metric": metric, "data": {"value": value, "tags": tags}})


def test_read_metrics_counts_write_requests_and_peak_vus(tmp_path):
    path = tmp_path / "metrics.jsonl"
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    lines = [point("http_reqs", 1, method=method) for method in methods]
    lines += [point("vus_max", 3), point("vus_max", 5), point("vus_max", 4)]
    path.write_text("\n".join(lines) + "\n")

    metrics, malformed = _read_metrics(path)

    assert malformed == 0
    assert metrics["http_reqs"] == 7
    assert metrics["write_attempts"] == 4
    assert metrics["vus_max"] == 5


def test_verify_reports_zero_write_attempts_and_passes_without_writes(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, extra_metric_lines=(point("vus_max", 4),))))

    assert result["verdict"] == "pass"
    assert result["metrics"]["write_attempts"] == 0
    assert result["metrics"]["vus_max"] == 4


def test_verify_enforces_the_observed_write_budget(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")  # max_write_attempts 20
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    writes = tuple(point("http_reqs", 1, method="POST") for _ in range(21))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, extra_metric_lines=writes)))

    assert result["verdict"] == "error"
    assert result["metrics"]["write_attempts"] == 21
    assert "write budget exceeded: 21 > 20" in result["limitations"]


def test_write_attempts_include_fixture_create_and_cleanup(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 13, "max_requests": 6, "max_write_attempts": 3},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: {"status": "deleted", "requests": 1})
    journey_writes = (point("http_reqs", 1, method="POST"), point("http_reqs", 1, method="PUT"))

    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=1, extra_metric_lines=journey_writes)))

    assert result["metrics"]["write_attempts"] == 4
    assert result["verdict"] == "error"
    assert "write budget exceeded: 4 > 3" in result["limitations"]


def test_verify_enforces_the_in_flight_budget_from_vus_max(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")  # max_in_flight 4
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, extra_metric_lines=(point("vus_max", 5),))))

    assert result["verdict"] == "error"
    assert "in-flight budget exceeded: 5 > 4" in result["limitations"]


def test_read_metrics_breaks_down_latency_and_failures_by_operation(tmp_path):
    path = tmp_path / "metrics.jsonl"
    lines = [point("http_req_duration", value, operation="create_payment") for value in (10, 20, 30)]
    lines += [point("http_req_failed", value, operation="create_payment") for value in (0, 0, 1, 1)]
    lines += [point("http_req_duration", 5, operation="observe_payment"), point("http_req_duration", 7)]
    lines += [point("http_req_failed", 0), point("http_req_failed", 1)]
    path.write_text("\n".join(lines) + "\n")

    metrics, _ = _read_metrics(path)

    assert metrics["by_operation"] == {
        "create_payment": {"samples": 3, "p95": 29.0, "failed_rate": 0.5},
        "observe_payment": {"samples": 1, "p95": 5.0, "failed_rate": None},
        "_untagged": {"samples": 1, "p95": 7.0, "failed_rate": 0.5},
    }


def test_read_metrics_omits_operation_breakdown_without_http_points(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text(point("vus_max", 3) + "\n")

    metrics, _ = _read_metrics(path)

    assert "by_operation" not in metrics


RUNTIME = Path(__file__).parents[1] / "src" / "litetraffic" / "k6" / "runtime.js"
HELPER_SCRIPT = 'import * as lt from "./litetraffic/runtime.js";\nexport const options = lt.options();\nexport default function () {}\n'


def test_verify_runs_a_staged_copy_that_contains_the_bundled_runtime(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    (scenario / "journeys.js").write_text(HELPER_SCRIPT)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setenv("FAKE_K6_CWD", str(tmp_path / "cwd.json"))

    result = verify("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    seen = json.loads((tmp_path / "cwd.json").read_text())
    assert seen["runtime"] == RUNTIME.read_text()
    assert Path(seen["script"]) == Path(seen["cwd"]) / "journeys.js"
    assert not Path(seen["cwd"]).is_relative_to(scenario.resolve())
    assert not Path(seen["cwd"]).exists()  # the staged copy is removed after the run
    assert not (scenario / "litetraffic").exists()
    lock = json.loads((tmp_path / "runs" / result["run_id"] / "scenario.lock.json").read_text())
    assert set(lock["files"]) == {"journeys.js", "litetraffic/runtime.js"}
    assert result["verdict"] == "pass"


@pytest.mark.skipif(shutil.which("k6") is None, reason="real k6 not installed")
def test_bundled_runtime_under_real_k6(tmp_path):
    scenario = write_bundle(
        tmp_path / "scenario",
        manifest(
            schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 3}]},
            budgets=manifest()["budgets"] | {"max_artifact_bytes": 1048576},
        ),
    )
    (scenario / "journeys.js").write_text(
        'import * as lt from "./litetraffic/runtime.js";\n'
        "export const options = lt.options();\n"
        "export default function () {\n"
        "  const first = lt.rng()();\n"
        "  const again = lt.rng()();\n"
        '  lt.evidence("accepted_orders_persist", first === again && first >= 0 && first < 1, '
        "{ expected: 1, actual: first, detail: String(first) });\n"
        "}\n"
    )

    result = verify("http://127.0.0.1:9", scenario, tmp_path / "runs", seed=7)

    assert result["verdict"] == "pass", result["limitations"]
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / result["run_id"] / "events" / "000001.jsonl").read_text().splitlines()
    ]
    keys = {event["logical_key"] for event in events}
    assert len(keys) == 3 and all(key.startswith(f"{result['run_id']}-traffic-") for key in keys)
    assert len({event["detail"] for event in events}) == 3  # each iteration draws its own seeded stream


@pytest.mark.skipif(shutil.which("k6") is None, reason="real k6 not installed")
def test_pool_item_under_real_k6(tmp_path):
    data = manifest(
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 3}]},
        budgets=manifest()["budgets"] | {"max_artifact_bytes": 1048576},
    )
    data["fixtures"]["pool"] = "pool.json"
    scenario = write_bundle(tmp_path / "scenario", data)
    (scenario / "pool.json").write_text('["a", "b", "c"]')
    (scenario / "journeys.js").write_text(
        'import exec from "k6/execution";\n'
        'import * as lt from "./litetraffic/runtime.js";\n'
        "export const options = lt.options();\n"
        "export default function () {\n"
        "  const item = lt.poolItem();\n"
        "  let past = 'no error';\n"
        "  try { lt.poolItem(3); } catch (e) { past = e.message; }\n"
        '  lt.evidence("accepted_orders_persist", item === "abc"[exec.scenario.iterationInTest], '
        "{ expected: 1, actual: 1, detail: item + '|' + past });\n"
        "}\n"
    )

    result = verify("http://127.0.0.1:9", scenario, tmp_path / "runs", seed=7)

    assert result["verdict"] == "pass", result["limitations"]
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / result["run_id"] / "events" / "000001.jsonl").read_text().splitlines()
    ]
    past = "fixture pool has no item for journey 3 (pool size 3)"
    assert sorted(event["detail"] for event in events) == [f"{item}|{past}" for item in "abc"]


@pytest.mark.skipif(shutil.which("k6") is None, reason="real k6 not installed")
def test_deep_equal_under_real_k6(tmp_path):
    scenario = write_bundle(
        tmp_path / "scenario",
        manifest(
            schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
            budgets=manifest()["budgets"] | {"max_artifact_bytes": 1048576},
        ),
    )
    (scenario / "journeys.js").write_text(
        'import * as lt from "./litetraffic/runtime.js";\n'
        "export const options = lt.options();\n"
        "const cases = [\n"
        "  [{ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1 }, true],\n"
        "  [[1, 2], [2, 1], false],\n"
        "  [{ a: 1 }, { a: 1, b: undefined }, false],\n"
        "  [{ a: null }, { a: undefined }, false],\n"
        "  [null, null, true],\n"
        "  [null, {}, false],\n"
        "  [[], {}, false],\n"
        "  ['1', 1, false],\n"
        "  [NaN, NaN, true],\n"
        "  [{ a: [1, 2, 3] }, { a: [1, 2] }, false],\n"
        "  ['x', 'x', true],\n"
        "];\n"
        "export default function () {\n"
        "  const wrong = cases.map((c, i) => (lt.deepEqual(c[0], c[1]) === c[2] ? null : i)).filter((i) => i !== null);\n"
        '  lt.evidence("accepted_orders_persist", wrong.length === 0, { expected: [], actual: wrong });\n'
        "}\n"
    )

    result = verify("http://127.0.0.1:9", scenario, tmp_path / "runs", seed=7)

    assert result["verdict"] == "pass", result


def timed(operation: str, end: str, duration_ms: float) -> str:
    # k6 stamps an http_req_duration point when the request finishes.
    return json.dumps(
        {"type": "Point", "metric": "http_req_duration", "data": {"time": f"2026-01-01T00:00:{end}Z", "value": duration_ms, "tags": {"operation": operation}}}
    )


def test_read_metrics_records_peak_in_flight_overlap_per_operation(tmp_path):
    path = tmp_path / "metrics.jsonl"
    lines = [
        # create: [0.0,1.0] [0.5,1.5] [0.9,1.2] overlap 3; [1.5,2.0] only touches the second one
        timed("create", "01.000000000", 1000), timed("create", "01.500000000", 1000),
        timed("create", "01.200000000", 300), timed("create", "02.000000000", 500),
        # read: back to back, never overlapping
        timed("read", "01.000", 1000), timed("read", "02.000", 1000),
        point("http_req_duration", 5, operation="untimed"),
    ]
    path.write_text("\n".join(lines) + "\n")

    metrics, malformed = _read_metrics(path)

    assert malformed == 0
    assert metrics["overlap"] == {"create": 3, "read": 1}


def test_read_metrics_omits_overlap_without_timestamps(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text(point("http_req_duration", 5, operation="create") + "\n")

    metrics, _ = _read_metrics(path)

    assert "overlap" not in metrics


def _overlap_bundle(tmp_path, minimum: dict) -> Path:
    data = manifest(journeys=[{"name": "purchase", "max_requests": 3, "max_writes": 1, "min_overlap": minimum}])
    return write_bundle(tmp_path / "scenario", data)


def test_verify_is_inconclusive_when_declared_overlap_is_not_observed(tmp_path, monkeypatch):
    scenario = _overlap_bundle(tmp_path, {"create": 2, "refund": 2})
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    sequential = (timed("create", "01.000", 1000), timed("create", "02.000", 1000))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, extra_metric_lines=sequential)))

    assert result["metrics"]["overlap"] == {"create": 1}
    assert result["verdict"] == "inconclusive"
    assert "concurrency not achieved for create: observed 1 < 2" in result["limitations"]
    assert "concurrency not achieved for refund: observed 0 < 2" in result["limitations"]
    report = (tmp_path / "runs" / result["run_id"] / "report.html").read_text()
    assert "Concurrency" in report and "<td>create</td><td>1</td>" in report


def test_verify_passes_when_declared_overlap_is_observed(tmp_path, monkeypatch):
    scenario = _overlap_bundle(tmp_path, {"create": 2})
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    overlapping = (timed("create", "01.000", 1000), timed("create", "01.500", 1000))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, extra_metric_lines=overlapping)))

    assert result["metrics"]["overlap"] == {"create": 2}
    assert result["verdict"] == "pass"


@pytest.mark.parametrize("minimum", [{"create": 0}, {"": 2}])
def test_min_overlap_rejects_invalid_declarations(tmp_path, minimum):
    with pytest.raises(Exception, match="min_overlap"):
        load_scenario(_overlap_bundle(tmp_path, minimum))


def test_restrict_never_follows_symlinks_out_of_the_run_dir(tmp_path):
    from litetraffic.runner import _restrict

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_dir.chmod(0o755)
    outside_file = outside_dir / "shared.txt"
    outside_file.write_text("keep my mode\n")
    outside_file.chmod(0o644)
    run_dir = tmp_path / "run"
    (run_dir / "events").mkdir(parents=True)
    (run_dir / "result.json").write_text("{}")
    (run_dir / "events" / "link.txt").symlink_to(outside_file)
    (run_dir / "linkdir").symlink_to(outside_dir, target_is_directory=True)

    _restrict(run_dir)

    assert stat.S_IMODE(outside_file.stat().st_mode) == 0o644
    assert stat.S_IMODE(outside_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE((run_dir / "result.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((run_dir / "events").stat().st_mode) == 0o700


def test_fixture_cleanup_runs_when_an_artifact_write_fails(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    scenario = owned_fixture_observed_bundle(tmp_path)
    cleaned = []
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *args: cleaned.append(args[-1]) or {"status": "deleted", "requests": 1})

    write_text = runner._write_text

    def failing(path, text):
        if path.name == "engine.stdout.log":
            raise OSError("disk full")
        write_text(path, text)

    monkeypatch.setattr(runner, "_write_text", failing)
    with pytest.raises(OSError, match="disk full"):
        runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1)))
    assert cleaned == ["owned-1"]
