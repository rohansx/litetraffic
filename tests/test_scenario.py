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
        "budgets": {"max_seconds": 20, "max_requests": 60, "max_write_attempts": 20, "max_in_flight": 4, "max_artifact_bytes": 65536},
    }
    data.update(overrides)
    return data


def write_bundle(tmp_path: Path, data: dict | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def test_owned_fixture_reserves_setup_cleanup_requests_writes_and_time(tmp_path):
    owned = {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "create_body": {"total": 1000}, "id_pointer": "/id"}
    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": owned},
        budgets=manifest()["budgets"] | {"max_requests": 61, "max_write_attempts": 22, "max_seconds": 19},
    )
    with pytest.raises(ValidationError, match="fixture deadline"):
        load_scenario(write_bundle(tmp_path, data))
    data["budgets"]["max_seconds"] = 20
    with pytest.raises(ScenarioError, match="request budget"):
        load_scenario(write_bundle(tmp_path, data))
    data["budgets"]["max_requests"] = 62
    assert load_scenario(write_bundle(tmp_path, data)).manifest.fixtures.owned_http.id_pointer == "/id"


@pytest.mark.parametrize("delete_path", ["https://other.test/{fixture_id}", "/fixtures/all", "//other.test/{fixture_id}", "/fixtures/{fixture_id}/../all", "/fixtures/%2e%2e/{fixture_id}"])
def test_owned_fixture_requires_same_origin_scoped_cleanup(tmp_path, delete_path):
    data = manifest(
        fixtures={"recipe": "owned-shop", "owned_http": {"create_path": "/fixtures", "delete_path": delete_path, "id_pointer": "/id"}},
        budgets=manifest()["budgets"] | {"max_requests": 62, "max_write_attempts": 22},
    )
    with pytest.raises(ValidationError, match="delete_path"):
        load_scenario(write_bundle(tmp_path, data))


def test_final_observation_consumes_its_own_request_budget(tmp_path):
    data = manifest(
        observation={"path": "/reports/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        assertions=["accepted_orders_persist", "ledger_total"],
    )
    with pytest.raises(ScenarioError, match="request budget"):
        load_scenario(write_bundle(tmp_path, data))


def test_final_observation_requires_a_relative_path(tmp_path):
    data = manifest(
        observation={"path": "https://elsewhere.test/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        assertions=["accepted_orders_persist", "ledger_total"],
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    with pytest.raises(ValidationError, match="relative path"):
        load_scenario(write_bundle(tmp_path, data))


def test_final_observation_reserves_time_for_its_deadline(tmp_path):
    data = manifest(
        observation={"path": "/reports/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}},
        assertions=["accepted_orders_persist", "ledger_total"],
        budgets=manifest()["budgets"] | {"max_seconds": 12, "max_requests": 61},
    )
    with pytest.raises(ValidationError, match="observation deadline"):
        load_scenario(write_bundle(tmp_path, data))


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


def test_rejects_fractional_journey_rates_until_the_engine_contract_supports_them(tmp_path):
    schedule = manifest()["schedule"] | {"phases": [{"name": "measure", "seconds": 10, "rate": 1.5}]}

    with pytest.raises(ValidationError, match="valid integer"):
        load_scenario(write_bundle(tmp_path, manifest(schedule=schedule)))


def test_rejects_a_schedule_that_never_admits_a_journey(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "phases": [{"name": "idle", "seconds": 10, "rate": 0}],
    }

    with pytest.raises(ValidationError, match="at least one journey"):
        load_scenario(write_bundle(tmp_path, manifest(schedule=schedule)))


def test_compiles_a_seeded_spiky_profile_into_bounded_phases(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "spiky",
            "duration_seconds": 12,
            "baseline_rate": 1,
            "spike_rate": 4,
            "spike_seconds": 2,
            "spikes": 2,
        },
    }
    data = manifest(schedule=schedule, budgets=manifest()["budgets"] | {"max_requests": 72, "max_write_attempts": 24})

    scenario = load_scenario(write_bundle(tmp_path, data)).manifest
    first = scenario.schedule.resolve(seed=42)

    assert first == scenario.schedule.resolve(seed=42)
    assert first != scenario.schedule.resolve(seed=43)
    assert sum(phase.seconds for phase in first) == 12
    assert sum(phase.admitted_journeys for phase in first) == 24
    assert scenario.planned_journeys == 24
    assert any(phase.rate == 4 for phase in first)


def test_rejects_spikes_that_cannot_fit_without_overlap(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "spiky",
            "duration_seconds": 5,
            "baseline_rate": 1,
            "spike_rate": 4,
            "spike_seconds": 2,
            "spikes": 3,
        },
    }

    with pytest.raises(ValidationError, match="spikes do not fit"):
        load_scenario(write_bundle(tmp_path, manifest(schedule=schedule)))


def test_compiles_seeded_random_bursts_with_idle_periods(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "random_bursts",
            "duration_seconds": 8,
            "quiet_rate": 0,
            "burst_rate": 3,
            "burst_seconds": 1,
            "bursts": 2,
        },
    }
    data = manifest(
        schedule=schedule,
        budgets=manifest()["budgets"] | {"max_requests": 18, "max_write_attempts": 6},
    )

    scenario = load_scenario(write_bundle(tmp_path, data)).manifest
    first = scenario.schedule.resolve(seed=42)

    assert first == scenario.schedule.resolve(seed=42)
    assert first != scenario.schedule.resolve(seed=43)
    assert sum(phase.seconds for phase in first) == 8
    assert sum(phase.admitted_journeys for phase in first) == 6
    assert scenario.planned_journeys == 6
    assert any(phase.rate == 0 for phase in first)


def test_rejects_random_bursts_that_cannot_fit_without_overlap(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "random_bursts",
            "duration_seconds": 5,
            "quiet_rate": 0,
            "burst_rate": 3,
            "burst_seconds": 2,
            "bursts": 3,
        },
    }

    with pytest.raises(ValidationError, match="bursts do not fit"):
        load_scenario(write_bundle(tmp_path, manifest(schedule=schedule)))


def test_compiles_a_sustained_burst_with_ramp_plateau_and_drop(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "sustained_burst",
            "baseline_rate": 1,
            "plateau_rate": 4,
            "ramp_seconds": 3,
            "plateau_seconds": 2,
            "recovery_seconds": 2,
        },
    }
    data = manifest(
        schedule=schedule,
        budgets=manifest()["budgets"] | {"max_requests": 57, "max_write_attempts": 19},
    )

    scenario = load_scenario(write_bundle(tmp_path, data)).manifest
    phases = scenario.schedule.resolve(seed=42)

    assert [(phase.name, phase.seconds, phase.rate) for phase in phases] == [
        ("ramp-1", 1, 2),
        ("ramp-2", 1, 3),
        ("ramp-3", 1, 4),
        ("plateau", 2, 4),
        ("recovery", 2, 1),
    ]
    assert scenario.planned_journeys == 19


def test_rejects_a_sustained_burst_without_a_higher_plateau(tmp_path):
    schedule = {
        "unit": "journeys_per_second",
        "profile": {
            "kind": "sustained_burst",
            "baseline_rate": 4,
            "plateau_rate": 4,
            "ramp_seconds": 3,
            "plateau_seconds": 2,
            "recovery_seconds": 2,
        },
    }

    with pytest.raises(ValidationError, match="plateau_rate must exceed baseline_rate"):
        load_scenario(write_bundle(tmp_path, manifest(schedule=schedule)))


@pytest.mark.parametrize(("field", "value", "message"), [("max_requests", 59, "request budget"), ("max_write_attempts", 19, "write budget")])
def test_rejects_insufficient_budgets(tmp_path, field, value, message):
    budgets = manifest()["budgets"] | {field: value}
    with pytest.raises(ScenarioError, match=message):
        load_scenario(write_bundle(tmp_path, manifest(budgets=budgets)))
