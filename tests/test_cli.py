import json
import os
from pathlib import Path

from litetraffic.cli import main
import pytest

from test_compare import write_run
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle


def test_inspect_emits_json_summary(tmp_path, capsys):
    status = main(["inspect", str(write_bundle(tmp_path)), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["name"] == "checkout"
    assert output["planned_journeys"] == 20
    assert output["maximum_journey_requests"] == 60


def test_inspect_invalid_scenario_returns_configuration_error(tmp_path, capsys):
    status = main(["inspect", str(write_bundle(tmp_path, manifest(script="../outside.js"))), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert output["ok"] is False
    assert "stay inside" in output["error"]


def test_os_error_in_a_command_is_a_json_configuration_error(tmp_path, monkeypatch, capsys):
    def unreadable(path):
        raise PermissionError("permission denied: manifest.json")

    monkeypatch.setattr("litetraffic.cli.load_scenario", unreadable)

    status = main(["inspect", str(tmp_path), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert output == {"ok": False, "error": "permission denied: manifest.json"}


def test_doctor_json_returns_nonzero_when_k6_is_missing(monkeypatch, capsys):
    monkeypatch.setenv("PATH", os.devnull)
    status = main(["doctor", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert output["ok"] is False
    assert output["checks"][0]["name"] == "k6"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_doctor_checks_the_output_dir_argument(tmp_path, capsys):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        status = main(["doctor", "--output-dir", str(locked / "runs"), "--json"])
    finally:
        locked.chmod(0o700)
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert {item["name"]: item["ok"] for item in output["checks"]}["output_dir"] is False


@pytest.mark.parametrize("command", ["doctor", "verify"])
@pytest.mark.parametrize("target", ["http://169.254.169.254/", "http://[fe80::1]/"])
def test_metadata_targets_exit_with_configuration_error(tmp_path, capsys, command, target):
    extra = [str(write_bundle(tmp_path / "scenario")), "--output-dir", str(tmp_path / "runs")] if command == "verify" else []
    status = main([command, *extra, "--target", target, "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert "link-local/metadata address not allowed" in output["error"]


def test_repository_checkout_example_is_valid(capsys):
    scenario = Path(__file__).parents[1] / "examples" / "checkout"

    status = main(["inspect", str(scenario), "--json"])

    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["name"] == "checkout"
    assert output["planned_journeys"] == 12


def test_verify_returns_one_for_a_failed_experiment(tmp_path, monkeypatch, capsys):
    scenario = write_bundle(tmp_path / "scenario", manifest(assertions=["accepted_orders_persist"]))
    k6 = tmp_path / "k6"
    k6.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = version ] && { echo 'k6 v2.2.0'; exit 0; }\n"
        "while [ $# -gt 0 ]; do [ \"$1\" = --console-output ] && console=$2; [ \"$1\" = --out ] && metrics=${2#json=}; shift; done\n"
        "printf 'LT_EVENT {\"schema_version\":1,\"type\":\"assertion\",\"assertion\":\"accepted_orders_persist\",\"passed\":false,\"run_id\":\"%s\"}\\n' \"$LT_RUN_ID\" > \"$console\"\n"
        ": > \"$metrics\"\n"
    )
    k6.chmod(0o755)

    status = main(["verify", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 1
    assert output["verdict"] == "fail"


def test_inspect_resolves_a_seeded_profile(tmp_path, capsys):
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

    status = main(["inspect", str(write_bundle(tmp_path, data)), "--seed", "42", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["planned_journeys"] == 12
    assert sum(phase["seconds"] for phase in output["resolved_schedule"]) == 10


def test_repository_inventory_example_is_valid(capsys):
    scenario = Path(__file__).parents[1] / "examples" / "inventory"

    status = main(["inspect", str(scenario), "--seed", "42", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["name"] == "inventory-contention"
    assert output["planned_journeys"] == 8


def test_repository_reporting_example_is_valid(capsys):
    scenario = Path(__file__).parents[1] / "examples" / "reporting"

    status = main(["inspect", str(scenario), "--seed", "42", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["name"] == "report-completeness"
    assert output["planned_journeys"] == 6


def test_repository_cached_search_example_is_valid(capsys):
    scenario = Path(__file__).parents[1] / "examples" / "cached_search"

    status = main(["inspect", str(scenario), "--seed", "42", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["name"] == "cached-search-invalidation"
    assert output["planned_journeys"] == 12
    assert output["maximum_journey_requests"] == 84


def test_repository_tenant_api_example_is_valid(capsys):
    scenario = Path(__file__).parents[1] / "examples" / "tenant_api"

    status = main(["inspect", str(scenario), "--seed", "42", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["name"] == "tenant-isolation"
    assert output["planned_journeys"] == 10
    assert output["maximum_journey_requests"] == 30


def test_cancelled_verify_returns_shell_interrupt_status(monkeypatch, capsys):
    monkeypatch.setattr(
        "litetraffic.cli.verify",
        lambda *args: {"verdict": "inconclusive", "lifecycle": "cancelled"},
    )

    status = main(["verify", ".", "--target", "http://example.test", "--json"])

    assert status == 130
    assert json.loads(capsys.readouterr().out)["lifecycle"] == "cancelled"


def test_verify_repeat_emits_a_series_result(monkeypatch, capsys):
    calls = []

    def run_series(target, scenario, output_dir, k6_path, seed, repeats, same_seed=False):
        calls.append((seed, repeats, same_seed))
        return {"mode": "repeat", "lifecycle": "finished", "verdict": "fail", "runs": []}

    monkeypatch.setattr("litetraffic.cli.repeat_verify", run_series)

    status = main([
        "verify",
        "scenario",
        "--target",
        "http://example.test",
        "--seed",
        "42",
        "--repeat",
        "3",
        "--json",
    ])

    assert status == 1
    assert calls == [(42, 3, False)]
    assert json.loads(capsys.readouterr().out)["mode"] == "repeat"


def test_verify_same_seed_passes_through_to_the_series(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        "litetraffic.cli.repeat_verify",
        lambda *args, **kwargs: calls.append((args[4:], kwargs))
        or {"mode": "repeat", "lifecycle": "finished", "verdict": "pass", "runs": []},
    )

    status = main(["verify", "scenario", "--target", "http://example.test", "--seed", "42",
                   "--repeat", "5", "--same-seed", "--json"])

    assert status == 0
    assert calls == [((42, 5), {"same_seed": True})]


def test_verify_same_seed_requires_a_repeat(capsys):
    status = main(["verify", "scenario", "--target", "http://example.test", "--same-seed", "--json"])

    assert status == 3
    assert "--same-seed requires --repeat" in json.loads(capsys.readouterr().out)["error"]


def test_verify_rejects_zero_repetitions(capsys):
    status = main([
        "verify",
        "scenario",
        "--target",
        "http://example.test",
        "--repeat",
        "0",
        "--json",
    ])

    assert status == 3
    assert "repeat must be at least 1" in json.loads(capsys.readouterr().out)["error"]


def test_verify_resolves_an_existing_e2b_sandbox(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        "litetraffic.cli.verify",
        lambda target, *args: calls.append(target) or {"verdict": "pass", "lifecycle": "finished"},
    )

    status = main(["verify", "scenario", "--e2b-sandbox-id", "sandbox-42", "--e2b-port", "8767", "--json"])

    assert status == 0
    assert calls == ["https://8767-sandbox-42.e2b.app"]
    assert json.loads(capsys.readouterr().out)["verdict"] == "pass"


def test_diff_returns_failure_for_a_latency_regression(tmp_path, capsys):
    baseline = write_run(tmp_path / "baseline", "baseline", p95=100)
    candidate = write_run(tmp_path / "candidate", "candidate", p95=130)

    status = main([
        "diff",
        str(baseline),
        str(candidate),
        "--max-p95-regression-percent",
        "20",
        "--json",
    ])

    output = json.loads(capsys.readouterr().out)
    assert status == 1
    assert output["verdict"] == "fail"
    assert output["performance"]["p95"]["status"] == "regression"


def test_diff_returns_inconclusive_for_incompatible_runs(tmp_path, capsys):
    baseline = write_run(tmp_path / "baseline", "baseline", scenario_sha256="scenario-a")
    candidate = write_run(tmp_path / "candidate", "candidate", scenario_sha256="scenario-b")

    status = main(["diff", str(baseline), str(candidate), "--json"])

    assert status == 2
    assert json.loads(capsys.readouterr().out)["comparable"] is False


def test_diff_returns_failure_when_both_runs_fail(tmp_path, capsys):
    baseline = write_run(tmp_path / "baseline", "baseline", verdict="fail")
    candidate = write_run(tmp_path / "candidate", "candidate", verdict="fail")

    status = main(["diff", str(baseline), str(candidate), "--json"])

    assert status == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"


@pytest.mark.parametrize(
    ("k6_options", "expected_verdict", "expected_status"),
    [
        ({"iterations": 19}, "inconclusive", 2),
        ({"returncode": 7}, "error", 3),
        ({"failed_tags": ({"status": "0", "error_code": "1212"},) * 2, "passed": False}, "error", 3),
    ],
)
def test_verify_exit_status_distinguishes_inconclusive_and_error(tmp_path, monkeypatch, capsys, k6_options, expected_verdict, expected_status):
    scenario = write_bundle(tmp_path / "scenario")
    passed = k6_options.pop("passed", True)
    events = [assertion("accepted_orders_persist", passed) for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events, **k6_options)

    status = main(["verify", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6), "--json"])

    assert json.loads(capsys.readouterr().out)["verdict"] == expected_verdict
    assert status == expected_status


@pytest.mark.parametrize(
    ("verdict", "expected_status"),
    [("pass", 0), ("fail", 1), ("inconclusive", 2), ("error", 3)],
)
def test_verify_repeat_maps_aggregate_verdict_to_exit_status(monkeypatch, capsys, verdict, expected_status):
    monkeypatch.setattr(
        "litetraffic.cli.repeat_verify",
        lambda *args, **kwargs: {"mode": "repeat", "lifecycle": "finished", "verdict": verdict, "runs": []},
    )

    status = main(["verify", "scenario", "--target", "http://example.test", "--repeat", "2", "--json"])

    capsys.readouterr()
    assert status == expected_status


def test_verify_human_output_is_readable(tmp_path, monkeypatch, capsys):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist", True) for _ in range(19)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events, iterations=19)
    runs = tmp_path / "runs"

    status = main(["verify", str(scenario), "--target", "http://example.test", "--output-dir", str(runs), "--k6-path", str(k6)])
    out = capsys.readouterr().out

    assert status == 2
    assert "verdict: INCONCLUSIVE  lifecycle: finished  journeys: 19/20" in out
    assert "accepted_orders_persist  unknown  19" in out
    assert "\n- delivered journeys 19" in out
    report = next(runs.glob("run_*")) / "report.html"
    assert f"report: {report}" in out
    assert "{" not in out and "[" not in out


def test_verify_repeat_human_output_is_readable(monkeypatch, capsys):
    run = {"run_id": "run_1", "seed": 4, "verdict": "pass", "lifecycle": "finished", "planned_journeys": 2,
           "metrics": {"iterations": 2}, "assertions": [], "limitations": [], "report": "report.html"}
    monkeypatch.setattr(
        "litetraffic.cli.repeat_verify",
        lambda *args, **kwargs: {"mode": "repeat", "lifecycle": "finished", "verdict": "pass", "consistent": True,
                       "requested_runs": 2, "completed_runs": 1, "runs": [run], "result": "series_x.json"},
    )

    main(["verify", "scenario", "--target", "http://example.test", "--repeat", "2", "--output-dir", "out"])
    out = capsys.readouterr().out

    assert "verdict: PASS  lifecycle: finished  runs: 1/2  consistent: yes" in out
    assert "seed 4  PASS  finished  journeys: 2/2" in out
    assert "{" not in out and "[" not in out


def test_diff_human_output_is_readable(tmp_path, capsys):
    baseline = write_run(tmp_path / "baseline", "baseline", p95=100)
    candidate = write_run(tmp_path / "candidate", "candidate", p95=130, verdict="fail")

    status = main(["diff", str(baseline), str(candidate)])
    out = capsys.readouterr().out

    assert status == 1
    assert "verdict: FAIL" in out
    assert "compatibility: comparable" in out
    assert "regression: report_complete" in out
    assert "p95: 100.0ms -> 130.0ms (+30.0%)" in out
    assert "{" not in out and "[" not in out


def test_diff_human_output_lists_per_operation_p95(tmp_path, capsys):
    baseline = write_run(tmp_path / "baseline", "baseline")
    candidate = write_run(tmp_path / "candidate", "candidate")
    for run_dir, p95 in ((baseline, 40), (candidate, 50)):
        result = json.loads((run_dir / "result.json").read_text())
        result["metrics"]["by_operation"] = {"create_payment": {"samples": 3, "p95": p95, "failed_rate": 0}}
        (run_dir / "result.json").write_text(json.dumps(result))

    main(["diff", str(baseline), str(candidate)])
    out = capsys.readouterr().out

    assert "p95 create_payment: 40.0ms -> 50.0ms (+25.0%)" in out


def test_diff_human_output_names_incompatibilities(tmp_path, capsys):
    baseline = write_run(tmp_path / "baseline", "baseline", scenario_sha256="a")
    candidate = write_run(tmp_path / "candidate", "candidate", scenario_sha256="b")

    main(["diff", str(baseline), str(candidate)])
    out = capsys.readouterr().out

    assert "compatibility: incompatible (scenario_sha256)" in out
    assert "{" not in out and "[" not in out


def test_inspect_human_output_is_readable(tmp_path, capsys):
    status = main(["inspect", str(write_bundle(tmp_path))])
    out = capsys.readouterr().out

    assert status == 0
    assert "name: checkout" in out
    assert "planned journeys: 20" in out
    assert "assertion: accepted_orders_persist" in out
    assert "{" not in out and "[" not in out


OWNED_FIXTURES = {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}"}


@pytest.mark.parametrize(
    ("example", "actors", "recipe", "observer", "observation_path"),
    [
        (
            "tenant_api",
            [
                {"class": "tenant-a-reader", "count": 3, "auth_recipe": "fixture-tenant-header"},
                {"class": "tenant-b-reader", "count": 3, "auth_recipe": "fixture-tenant-header"},
            ],
            "owned-overlapping-tenant-records",
            "owned-tenant-state",
            "/tenant/state",
        ),
        (
            "cached_search",
            [{"class": "searcher", "count": 4, "auth_recipe": "run-scoped-header"}],
            "owned-product-catalog",
            "owned-cache-state",
            "/cache/state",
        ),
    ],
)
def test_inspect_explains_actors_budgets_fixture_and_observer(capsys, example, actors, recipe, observer, observation_path):
    scenario = Path(__file__).parents[1] / "examples" / example
    budgets = json.loads((scenario / "manifest.json").read_text())["budgets"]

    status = main(["inspect", str(scenario), "--seed", "42", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["actors"] == actors
    assert output["budgets"] == budgets
    assert set(output["budgets"]) == {"max_seconds", "max_requests", "max_write_attempts", "max_in_flight", "max_artifact_bytes"}
    assert output["fixture"] == {"recipe": recipe, "owned_http": OWNED_FIXTURES, "command": None}
    assert output["secret_env"] == []
    assert output["observer"] == observer
    assert output["observation_path"] == observation_path


def test_inspect_lists_secret_env_names_but_never_values(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FIXTURE_TOKEN", "fixture-secret-value")
    monkeypatch.setenv("OBSERVER_TOKEN", "observer-secret-value")
    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": OWNED_FIXTURES | {"id_pointer": "/id", "bearer_token_env": "FIXTURE_TOKEN"}},
        observation={
            "path": "/state",
            "assertion": "accepted_orders_persist",
            "expected": {"/ok": True},
            "bearer_token_env": "OBSERVER_TOKEN",
            "headers_env": {"apikey": "DB_KEY"},
        },
        budgets=manifest()["budgets"] | {"max_seconds": 40, "max_requests": 70, "max_write_attempts": 30},
    )

    status = main(["inspect", str(write_bundle(tmp_path, data)), "--json"])
    raw = capsys.readouterr().out

    assert status == 0
    assert json.loads(raw)["secret_env"] == ["DB_KEY", "FIXTURE_TOKEN", "OBSERVER_TOKEN"]
    assert "secret-value" not in raw


def test_inspect_without_owned_fixture_or_observation(tmp_path, capsys):
    status = main(["inspect", str(write_bundle(tmp_path)), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["fixture"] == {"recipe": "owned-shop", "owned_http": None, "command": None}
    assert output["observer"] == "owned-order-ledger"
    assert output["observation_path"] is None
    assert output["secret_env"] == []


def test_inspect_shows_command_fixture_argv_and_digest(tmp_path, capsys):
    command = {"setup": ["psql", "-f", "seed.sql"], "teardown": ["psql", "-f", "reset.sql"], "timeout_seconds": 2}
    data = manifest(fixtures={"recipe": "seeded", "command": command}, budgets=manifest()["budgets"] | {"max_seconds": 22})

    assert main(["inspect", str(write_bundle(tmp_path, data)), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["fixture"]["command"] == command | {"cwd": "bundle", "inputs": []}

    data["fixtures"]["command"]["setup"] = ["psql", "-f", "other.sql"]
    assert main(["inspect", str(write_bundle(tmp_path, data)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scenario_sha256"] != first["scenario_sha256"]

    assert main(["inspect", str(write_bundle(tmp_path, data))]) == 0
    out = capsys.readouterr().out
    assert "fixture setup: psql -f other.sql" in out
    assert "fixture teardown: psql -f reset.sql" in out


def test_inspect_shows_command_fixture_inputs(tmp_path, capsys):
    command = {"setup": ["psql", "-f", "seed.sql"], "teardown": ["psql", "-f", "reset.sql"], "timeout_seconds": 2, "inputs": ["seed.sql"]}
    data = manifest(fixtures={"recipe": "seeded", "command": command}, budgets=manifest()["budgets"] | {"max_seconds": 22})
    path = write_bundle(tmp_path, data)
    (path / "seed.sql").write_text("select 1;\n")

    assert main(["inspect", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fixture"]["command"]["inputs"] == ["seed.sql"]
    assert main(["inspect", str(path)]) == 0
    assert "fixture input: seed.sql" in capsys.readouterr().out


def test_verify_cancelled_during_fixture_create_exits_130(tmp_path, monkeypatch, capsys):
    import litetraffic.runner as runner

    data = manifest(fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}}, budgets=manifest()["budgets"] | {"max_requests": 62, "max_write_attempts": 22})
    scenario = write_bundle(tmp_path / "scenario", data)

    def cancel(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "create_fixture", cancel)
    status = main(["verify", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(fake_k6(tmp_path, [])), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert status == 130
    assert output["lifecycle"] == "cancelled"
    assert json.loads((tmp_path / "runs" / output["run_id"] / "result.json").read_text())["lifecycle"] == "cancelled"


def test_inspect_accepts_scenario_flag(tmp_path, capsys):
    status = main(["inspect", "--scenario", str(write_bundle(tmp_path)), "--json"])
    assert status == 0
    assert json.loads(capsys.readouterr().out)["name"] == "checkout"


def test_verify_scenario_flag_matches_positional_form(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("litetraffic.cli.verify", lambda *args: calls.append(args) or {"lifecycle": "finished", "verdict": "pass"})
    for spelling in (["scenario"], ["--scenario", "scenario"]):
        assert main(["verify", *spelling, "--target", "http://example.test", "--json"]) == 0
    assert calls[0] == calls[1]
    assert calls[0][1] == Path("scenario")


@pytest.mark.parametrize("command", ["inspect", "verify"])
@pytest.mark.parametrize("spelling", [[], ["a", "--scenario", "b"]])
def test_scenario_must_be_given_exactly_once(command, spelling, capsys):
    status = main([command, *spelling, "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert output["error"] == "give the scenario directory once, either positionally or with --scenario"


def test_diff_accepts_run_ids_from_runs_dir(tmp_path, capsys):
    write_run(tmp_path / "run_a", "run_a", p95=100)
    write_run(tmp_path / "run_b", "run_b", p95=130)

    status = main(
        ["diff", "run_a", "run_b", "--runs-dir", str(tmp_path), "--max-p95-regression-percent", "20", "--json"]
    )

    output = json.loads(capsys.readouterr().out)
    assert status == 1
    assert output["baseline_run_id"] == "run_a"
    assert output["candidate_run_id"] == "run_b"


def test_diff_unknown_run_id_exits_3(tmp_path, capsys):
    write_run(tmp_path / "run_a", "run_a")

    status = main(["diff", "run_a", "run_nope", "--runs-dir", str(tmp_path), "--json"])

    assert status == 3
    assert "run_nope" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("example", ["checkout", "inventory", "reporting", "cached_search", "tenant_api"])
def test_examples_use_the_bundled_runtime_helper(example, capsys):
    scenario = Path(__file__).parents[1] / "examples" / example
    script = (scenario / "journeys.js").read_text()

    assert 'from "./litetraffic/runtime.js"' in script
    assert "function evidence" not in script and "LT_SCHEDULE_JSON" not in script
    assert main(["inspect", str(scenario), "--json"]) == 0


def test_up_loops_slices_and_reports_activity_on_stderr(tmp_path, monkeypatch, capsys):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist", False) for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events)

    status = main(["up", "--scenario", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6), "--max-slices", "2", "--json"])
    captured = capsys.readouterr()

    assert status == 0  # a failing slice does not make a background activity fail
    payload = json.loads(captured.out)
    assert payload["mode"] == "background" and payload["status"] == "completed"
    assert "verdict" not in payload and len(payload["slices"]) == 2
    activity_path = tmp_path / "runs" / payload["activity_id"] / "activity.json"
    assert f"activity: {activity_path.resolve()}" in captured.err
    assert "status: completed" in captured.err


def test_up_ctrl_c_exits_zero_after_finalizing(tmp_path, monkeypatch, capsys):
    scenario = write_bundle(tmp_path / "scenario")
    k6 = fake_k6(tmp_path, [])

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("litetraffic.activity.verify", interrupted)

    status = main(["up", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6)])
    err = capsys.readouterr().err

    assert status == 0
    assert "status: stopped" in err
    saved = json.loads(next((tmp_path / "runs").glob("activity_*/activity.json")).read_text())
    assert saved["status"] == "stopped" and "verdict" not in saved


def test_up_rejects_zero_max_slices(tmp_path, capsys):
    status = main(["up", str(tmp_path), "--target", "http://example.test", "--max-slices", "0", "--json"])

    assert status == 3
    assert "max-slices" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("exc", [RuntimeError("engine exploded"), KeyError("engine exploded")])
def test_up_slice_raising_records_error_and_exits_three(tmp_path, monkeypatch, capsys, exc):
    scenario = write_bundle(tmp_path / "scenario")
    k6 = fake_k6(tmp_path, [])

    def boom(*args):
        raise exc

    monkeypatch.setattr("litetraffic.activity.verify", boom)

    status = main(["up", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6), "--json"])
    captured = capsys.readouterr()

    assert status == 3
    payload = json.loads(captured.out)
    assert payload["ok"] is False and "engine exploded" in payload["error"]
    assert "status: error" in captured.err
    saved = json.loads(next((tmp_path / "runs").glob("activity_*/activity.json")).read_text())
    assert saved["status"] == "error" and "engine exploded" in saved["error"] and saved["finished_at"]


def _expression_manifest(expected):
    return manifest(
        assertions=["accepted_orders_persist", "ledger_total"],
        observation={"path": "/ledger", "assertion": "ledger_total", "expected": expected},
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )


def test_inspect_shows_resolved_expected_values_for_the_seed(tmp_path, capsys):
    data = _expression_manifest({"/total": "${planned_journeys * 50 + seed}", "/n": {"gte": "${planned_journeys}"}})
    scenario = str(write_bundle(tmp_path, data))

    assert main(["inspect", scenario, "--seed", "7", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["observation_expected"] == {"/total": 1007, "/n": {"gte": 20}}

    assert main(["inspect", scenario, "--seed", "7"]) == 0
    assert '"/total": 1007' in capsys.readouterr().out


def test_inspect_rejects_unknown_expression_names(tmp_path, capsys):
    data = _expression_manifest({"/total": "${planned_journeys + run_id}"})
    assert main(["inspect", str(write_bundle(tmp_path, data)), "--json"]) == 3
    assert "run_id" in json.loads(capsys.readouterr().out)["error"]


def test_inspect_rejects_deeply_nested_expressions_with_exit_3(tmp_path, capsys):
    data = _expression_manifest({"/total": "${" + "(" * 60 + "1" + ")" * 60 + "}"})
    assert main(["inspect", str(write_bundle(tmp_path, data)), "--json"]) == 3
    assert "expression" in json.loads(capsys.readouterr().out)["error"]


def test_verify_passes_plan_variables_to_the_observer(tmp_path, monkeypatch, capsys):
    import litetraffic.runner as runner

    seen = {}

    def observe(*args, **kwargs):
        seen.update(kwargs["variables"])
        return {"assertion": "ledger_total", "status": "pass"}

    monkeypatch.setattr(runner, "observe", observe)
    scenario = write_bundle(tmp_path / "scenario", _expression_manifest({"/total": "${planned_journeys}"}))
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    main(["verify", str(scenario), "--target", "http://example.test", "--seed", "9", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(fake_k6(tmp_path, events)), "--json"])

    assert seen == {"planned_journeys": 20, "seed": 9}
