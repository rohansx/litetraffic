import json
import os
from pathlib import Path

from litetraffic.cli import main
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


def test_doctor_json_returns_nonzero_when_k6_is_missing(monkeypatch, capsys):
    monkeypatch.setenv("PATH", os.devnull)
    status = main(["doctor", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert status == 3
    assert output["ok"] is False
    assert output["checks"][0]["name"] == "k6"


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
