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
    assert output["planned_journeys"] == 270
