import json
from pathlib import Path

import pytest

from litetraffic.runner import RunnerError, _read_metrics, repeat_verify, verify
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
) -> Path:
    path = tmp_path / "k6"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "if sys.argv[1] == 'version':\n"
        "    print('k6 v2.2.0')\n"
        "    raise SystemExit(0)\n"
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
        f"time.sleep({sleep_seconds})\n"
        f"raise SystemExit({returncode})\n"
    )
    path.chmod(0o755)
    return path


def assertion(name: str, passed: bool = True) -> dict:
    return {"schema_version": 1, "type": "assertion", "assertion": name, "passed": passed}


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
    first_event = (run_dir / "events" / "000001.jsonl").read_text().splitlines()[0]
    assert json.loads(first_event)["sequence"] == 1


def test_verify_reports_definite_assertion_failure(tmp_path, monkeypatch):
    data = manifest(assertions=["accepted_orders_persist"])
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist", False)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "fail"
    assert result["assertions"] == [{"id": "accepted_orders_persist", "status": "fail", "samples": 1}]


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
    assert result["assertions"][-1] == {"id": "ledger_total", "status": "unknown", "samples": 0}


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
        budgets=manifest()["budgets"] | {"max_seconds": 1, "max_requests": 3, "max_write_attempts": 1},
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
        budgets=manifest()["budgets"] | {"max_seconds": 11, "max_requests": 5, "max_write_attempts": 3},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    calls = []
    monkeypatch.setattr(runner, "create_fixture", lambda target, config, run_id: calls.append(("create", run_id)) or {"status": "created", "fixture_id": "owned-1", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda target, config, run_id, fixture_id: calls.append(("delete", run_id, fixture_id)) or {"status": "deleted", "requests": 1})

    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, returncode=returncode, iterations=1, sleep_seconds=sleep_seconds)))

    assert result["lifecycle"] == expected_lifecycle
    assert result["metrics"]["fixture_requests"] == 2
    assert result["metrics"]["total_http_reqs"] == 4
    assert calls == [("create", result["run_id"]), ("delete", result["run_id"], "owned-1")]
    assert json.loads((tmp_path / "runs" / result["run_id"] / "fixture.json").read_text())["cleanup"]["status"] == "deleted"


def test_owned_fixture_setup_failure_prevents_traffic(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    data = manifest(fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}}, budgets=manifest()["budgets"] | {"max_requests": 62, "max_write_attempts": 22})
    scenario = write_bundle(tmp_path / "scenario", data)
    monkeypatch.setattr(runner, "create_fixture", lambda *args: {"status": "error", "reason": "fixture create HTTP 503", "requests": 1})
    result = runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))
    assert result["verdict"] == "error"
    assert result["engine_exit_code"] is None
    assert "fixture create HTTP 503" in result["limitations"]


def test_owned_fixture_cleanup_failure_cannot_pass(tmp_path, monkeypatch):
    import litetraffic.runner as runner

    data = manifest(fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}}, budgets=manifest()["budgets"] | {"max_requests": 62, "max_write_attempts": 22})
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
        budgets=manifest()["budgets"] | {"max_seconds": 11, "max_requests": 5, "max_write_attempts": 3},
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
