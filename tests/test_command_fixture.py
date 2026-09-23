import json
import sys

import pytest

import litetraffic.fixture as fixture_module
import litetraffic.runner as runner
from litetraffic.cli import main
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle

TARGET = "http://example.test"


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def record(name: str, extra: str = "") -> list[str]:
    # Writes the LT_* environment into the bundle directory (cwd), then runs `extra`.
    return py(
        "import json, os, pathlib, sys\n"
        f"pathlib.Path('{name}.json').write_text(json.dumps({{k: v for k, v in os.environ.items() if k.startswith('LT_')}}))\n"
        + extra
    )


def command_bundle(tmp_path, setup, teardown, timeout_seconds=2):
    data = manifest(
        fixtures={"recipe": "seeded", "command": {"setup": setup, "teardown": teardown, "timeout_seconds": timeout_seconds}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 1 + 2 + 2 * (timeout_seconds + 4)},
    )
    return write_bundle(tmp_path / "scenario", data)


def run(tmp_path, monkeypatch, scenario, **k6):
    events = [assertion("accepted_orders_persist")]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "k6-env.json"))
    result = runner.verify(TARGET, scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, iterations=1, **k6)), seed=7)
    fixture = json.loads((tmp_path / "runs" / result["run_id"] / "fixture.json").read_text())
    return result, fixture


def test_setup_runs_in_bundle_before_k6_and_exports_its_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_FIXTURE_JSON", "stale")
    scenario = command_bundle(tmp_path, record("setup", "print('seeding'); print(json.dumps({'tenant': 't1'}))"), record("teardown"))

    result, fixture = run(tmp_path, monkeypatch, scenario)

    assert result["verdict"] == "pass", result["limitations"]
    setup_env = json.loads((scenario / "setup.json").read_text())
    assert setup_env == {"LT_RUN_ID": result["run_id"], "LT_TARGET": TARGET, "LT_SEED": "7"}
    assert json.loads(json.loads((tmp_path / "k6-env.json").read_text())["LT_FIXTURE_JSON"]) == {"tenant": "t1"}
    assert json.loads((scenario / "teardown.json").read_text())["LT_RUN_ID"] == result["run_id"]
    assert fixture["setup"]["argv"][:2] == [sys.executable, "-c"]
    assert fixture["setup"]["exit_code"] == 0
    assert fixture["setup"]["duration_seconds"] >= 0
    assert fixture["teardown"]["exit_code"] == 0
    assert "fixture_requests" not in result["metrics"]


def test_non_object_last_line_is_not_exported(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_FIXTURE_JSON", "stale")
    scenario = command_bundle(tmp_path, py("print('[1, 2]')"), py("pass"))
    run(tmp_path, monkeypatch, scenario)
    assert "LT_FIXTURE_JSON" not in json.loads((tmp_path / "k6-env.json").read_text())


@pytest.mark.parametrize(
    ("setup", "limitation"),
    [
        (py("import sys; sys.stderr.write('x' * 10000 + 'boom'); raise SystemExit(3)"), "fixture setup failed: exit 3"),
        (py("import time; time.sleep(10)"), "fixture setup failed: timed out after 1s"),
    ],
)
def test_setup_failure_is_an_error_without_traffic_and_still_tears_down(tmp_path, monkeypatch, setup, limitation):
    scenario = command_bundle(tmp_path, setup, record("teardown"), timeout_seconds=1)

    result, fixture = run(tmp_path, monkeypatch, scenario)

    assert result["verdict"] == "error"
    assert result["engine_exit_code"] is None
    assert not (tmp_path / "k6-env.json").exists()
    assert limitation in result["limitations"]
    assert (scenario / "teardown.json").exists()
    assert len(fixture["setup"]["stderr"].encode()) <= 4096
    if "exit" in limitation:
        assert fixture["setup"]["stderr"].endswith("boom")


@pytest.mark.parametrize(("returncode", "sleep_seconds"), [(0, 0), (7, 0), (0, 60)])
def test_teardown_runs_after_any_engine_exit(tmp_path, monkeypatch, returncode, sleep_seconds):
    scenario = command_bundle(tmp_path, py("pass"), record("teardown"))
    result, fixture = run(tmp_path, monkeypatch, scenario, returncode=returncode, sleep_seconds=sleep_seconds)
    assert (scenario / "teardown.json").exists()
    assert fixture["teardown"]["exit_code"] == 0
    if sleep_seconds:
        assert "engine stopped after its 3-second share of the 15-second budget" in result["limitations"]


def test_teardown_runs_when_user_cancels(tmp_path, monkeypatch):
    scenario = command_bundle(tmp_path, py("pass"), record("teardown"))

    def interrupt(process, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_communicate", interrupt)
    result, _ = run(tmp_path, monkeypatch, scenario, sleep_seconds=60)
    assert result["lifecycle"] == "cancelled"
    assert (scenario / "teardown.json").exists()


def test_teardown_failure_cannot_pass(tmp_path, monkeypatch):
    scenario = command_bundle(tmp_path, py("pass"), py("raise SystemExit(4)"))
    result, fixture = run(tmp_path, monkeypatch, scenario)
    assert result["verdict"] == "error"
    assert "fixture teardown failed: exit 4" in result["limitations"]
    assert fixture["teardown"]["exit_code"] == 4


def test_missing_setup_executable_is_an_error(tmp_path, monkeypatch):
    scenario = command_bundle(tmp_path, ["/nonexistent/litetraffic-seed"], py("pass"))
    result, fixture = run(tmp_path, monkeypatch, scenario)
    assert result["verdict"] == "error"
    assert any(item.startswith("fixture setup failed: ") for item in result["limitations"])
    assert fixture["setup"]["exit_code"] is None


def k6_env(tmp_path):
    return json.loads((tmp_path / "k6-env.json").read_text())


def with_pool_file(scenario, items: str):
    data = json.loads((scenario / "manifest.json").read_text())
    data["fixtures"]["pool"] = "pool.json"
    (scenario / "manifest.json").write_text(json.dumps(data))
    (scenario / "pool.json").write_text(items)
    return scenario


def test_pool_file_is_exported_to_k6(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_FIXTURE_POOL_JSON", "stale")
    scenario = with_pool_file(command_bundle(tmp_path, py("pass"), py("pass")), '[{"session": "s0"}]')
    result, _ = run(tmp_path, monkeypatch, scenario)
    assert result["verdict"] == "pass", result["limitations"]
    assert json.loads(k6_env(tmp_path)["LT_FIXTURE_POOL_JSON"]) == [{"session": "s0"}]


def test_setup_pool_key_is_exported_to_k6(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_FIXTURE_POOL_JSON", "stale")
    scenario = command_bundle(tmp_path, py("print('{\"pool\": [\"p0\", \"p1\"], \"tenant\": \"t\"}')"), py("pass"))
    result, _ = run(tmp_path, monkeypatch, scenario)
    assert result["verdict"] == "pass", result["limitations"]
    assert json.loads(k6_env(tmp_path)["LT_FIXTURE_POOL_JSON"]) == ["p0", "p1"]


def test_no_pool_means_no_pool_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_FIXTURE_POOL_JSON", "stale")
    run(tmp_path, monkeypatch, command_bundle(tmp_path, py("pass"), py("pass")))
    assert "LT_FIXTURE_POOL_JSON" not in k6_env(tmp_path)


@pytest.mark.parametrize(
    ("pool", "limitation"),
    [
        ("[]", "fixture pool has 0 items but 1 journeys are planned"),
        ('"x"', "fixture pool must be a JSON array"),
    ],
)
def test_unusable_setup_pool_is_an_error_without_traffic(tmp_path, monkeypatch, pool, limitation):
    scenario = command_bundle(tmp_path, py(f"print('{{\"pool\": {pool}}}')"), record("teardown"))
    result, _ = run(tmp_path, monkeypatch, scenario)
    assert result["verdict"] == "error"
    assert limitation in result["limitations"]
    assert not (tmp_path / "k6-env.json").exists()
    assert (scenario / "teardown.json").exists()


def test_setup_pool_conflicts_with_a_pool_file(tmp_path, monkeypatch):
    scenario = with_pool_file(command_bundle(tmp_path, py("print('{\"pool\": [1]}')"), py("pass")), "[1]")
    result, _ = run(tmp_path, monkeypatch, scenario)
    assert result["verdict"] == "error"
    assert "fixture pool is set by both fixtures.pool and the setup output" in result["limitations"]


def cli_verify(tmp_path, scenario):
    k6 = fake_k6(tmp_path, [assertion("accepted_orders_persist")], iterations=1)
    return main(["verify", str(scenario), "--target", TARGET, "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6), "--json"])


def test_teardown_runs_when_an_artifact_write_fails(tmp_path, monkeypatch, capsys):
    scenario = command_bundle(tmp_path, py("pass"), record("teardown"))
    write_text = runner._write_text

    def failing(path, text):
        if path.name == "engine.stdout.log":
            raise OSError("disk full")
        write_text(path, text)

    monkeypatch.setattr(runner, "_write_text", failing)
    assert cli_verify(tmp_path, scenario) == 3
    assert "disk full" in json.loads(capsys.readouterr().out)["error"]
    assert (scenario / "teardown.json").exists()


def test_cancel_during_setup_skips_k6_and_still_tears_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "k6-env.json"))
    scenario = command_bundle(tmp_path, py("import time; time.sleep(10)"), record("teardown"))
    communicate = fixture_module._communicate
    calls = []

    def interrupt_setup(process, timeout):
        calls.append(process)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return communicate(process, timeout)

    monkeypatch.setattr(fixture_module, "_communicate", interrupt_setup)
    assert cli_verify(tmp_path, scenario) == 130
    assert json.loads(capsys.readouterr().out)["lifecycle"] == "cancelled"
    assert not (tmp_path / "k6-env.json").exists()
    assert (scenario / "teardown.json").exists()


def test_setup_and_teardown_use_their_own_timeouts(tmp_path, monkeypatch):
    calls = []
    real = runner.run_fixture_command

    def spy(stage, argv, cwd, env, timeout, secrets=()):
        calls.append((stage, timeout))
        return real(stage, argv, cwd, env, timeout, secrets)

    monkeypatch.setattr(runner, "run_fixture_command", spy)
    data = manifest(
        fixtures={"recipe": "seeded", "command": {"setup": py("pass"), "teardown": py("pass"), "setup_timeout_seconds": 3, "teardown_timeout_seconds": 1}},
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 1 + 2 + 3 + 4 + 1 + 4},
    )
    run(tmp_path, monkeypatch, write_bundle(tmp_path / "scenario", data))
    assert calls == [("setup", 3), ("teardown", 1)]
