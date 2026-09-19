import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from litetraffic.scenario import ScenarioError, load_scenario


def manifest(**overrides):
    data = {
        "schema_version": 1,
        "name": "checkout",
        "script": "journeys.js",
        "actors": [{"class": "buyer", "count": 10, "auth_recipe": "test-login"}],
        "fixtures": {"recipe": "owned-shop"},
        "journeys": [{"name": "purchase", "max_requests": 3, "max_writes": 1}],
        "schedule": {"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 10, "rate": 2}]},
        "assertions": ["accepted_orders_persist"],
        "observer": "owned-order-ledger",
        "budgets": {"max_seconds": 20, "max_requests": 60, "max_write_attempts": 20, "max_in_flight": 4, "max_artifact_bytes": 1024},
    }
    data.update(overrides)
    return data


def write_bundle(tmp_path: Path, data: dict | None = None) -> Path:
    (tmp_path / "manifest.json").write_text(json.dumps(data or manifest()))
    (tmp_path / "journeys.js").write_text("export default function () {}\n")
    return tmp_path


def test_loads_valid_bundle_and_calculates_bounds(tmp_path):
    bundle = load_scenario(write_bundle(tmp_path))
    assert bundle.manifest.planned_journeys == 20
    assert bundle.manifest.maximum_journey_requests == 60
    assert bundle.manifest.maximum_journey_writes == 20
    assert bundle.script_path == (tmp_path / "journeys.js").resolve()


def test_fixture_parameters_are_explicitly_supported(tmp_path):
    data = manifest(fixtures={"recipe": "owned-shop", "parameters": {"small_carts": 80, "large_carts": 20}})

    bundle = load_scenario(write_bundle(tmp_path, data))

    assert bundle.manifest.fixtures.parameters["large_carts"] == 20


def test_rejects_unknown_manifest_fields(tmp_path):
    with pytest.raises(ValidationError, match="unexpected"):
        load_scenario(write_bundle(tmp_path, manifest(unexpected=True)))


def test_rejects_script_path_escape(tmp_path):
    (tmp_path.parent / "outside.js").write_text("export default function () {}\n")
    with pytest.raises(ScenarioError, match="stay inside"):
        load_scenario(write_bundle(tmp_path, manifest(script="../outside.js")))


def test_rejects_missing_script(tmp_path):
    path = write_bundle(tmp_path)
    (path / "journeys.js").unlink()
    with pytest.raises(ScenarioError, match="does not exist"):
        load_scenario(path)


def test_rejects_schedule_longer_than_duration_budget(tmp_path):
    budgets = manifest()["budgets"] | {"max_seconds": 9}

    with pytest.raises(ValidationError, match="scheduled duration"):
        load_scenario(write_bundle(tmp_path, manifest(budgets=budgets)))


@pytest.mark.parametrize(("field", "value", "message"), [("max_requests", 59, "request budget"), ("max_write_attempts", 19, "write budget")])
def test_rejects_insufficient_budgets(tmp_path, field, value, message):
    budgets = manifest()["budgets"] | {field: value}
    with pytest.raises(ScenarioError, match=message):
        load_scenario(write_bundle(tmp_path, manifest(budgets=budgets)))
