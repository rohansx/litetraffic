"""Assertions on how many journeys reached a declared stage."""

import json
import sys

import pytest
from pydantic import ValidationError

import litetraffic.runner as runner
from litetraffic.funnel import stage_assertion_results
from litetraffic.models import StageAssertion
from litetraffic.scenario import load_scenario
from test_funnel import stage
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle

COMMAND = {"setup": [sys.executable, "-c", "print('{\"stock\": 5}')"], "teardown": [sys.executable, "-c", "pass"], "timeout_seconds": 2}
BUDGETS = manifest()["budgets"] | {"max_seconds": 40}


def check(expected, assertion_id="paid_within_stock", stage_name="paid"):
    return StageAssertion(assertion=assertion_id, stage=stage_name, expected=expected)


def _data(expected, **overrides):
    journeys = [{"name": "purchase", "max_requests": 3, "max_writes": 1, "stages": ["paid"]}]
    data = manifest(
        journeys=journeys,
        stage_assertions=[{"assertion": "paid_within_stock", "stage": "paid", "expected": expected}],
        assertions=["accepted_orders_persist", "paid_within_stock"],
        fixtures={"recipe": "x", "command": COMMAND},
        budgets=BUDGETS,
    )
    return data | overrides


@pytest.mark.parametrize("expected", [{}, {"gt": 1}, {"lte": 1, "gte": 0}, {"lte": -1}, {"lte": 1.5}, {"lte": True}, {"len": 2}, {"lte": "${nope}"}, {"lte": "five"}])
def test_model_rejects_bad_expectations(expected):
    with pytest.raises(ValidationError):
        check(expected)


@pytest.mark.parametrize("expected", [{"eq": 0}, {"gte": 3}, {"lte": "${fixture.stock}"}, {"gte": "${planned_journeys - 1}"}])
def test_model_accepts_counts_and_expressions(expected):
    assert check(expected).expected == expected


def test_load_ties_each_check_to_a_declared_assertion_and_stage(tmp_path):
    assert load_scenario(write_bundle(tmp_path / "ok", _data({"lte": "${fixture.stock}"}))).manifest.stage_assertions
    with pytest.raises(ValidationError, match="declared in assertions"):
        load_scenario(write_bundle(tmp_path / "a", _data({"lte": 5}, assertions=["accepted_orders_persist"])))
    with pytest.raises(ValidationError, match="no journey declares stage"):
        load_scenario(write_bundle(tmp_path / "s", _data({"lte": 5}, journeys=manifest()["journeys"])))
    data = _data({"lte": 5})
    with pytest.raises(ValidationError, match="distinct"):
        load_scenario(write_bundle(tmp_path / "d", data | {"stage_assertions": data["stage_assertions"] * 2}))
    with pytest.raises(ValidationError, match="distinct"):
        load_scenario(write_bundle(tmp_path / "o", data | {"observation": {"path": "/x", "assertion": "paid_within_stock", "expected": {"/n": 1}}}))
    with pytest.raises(ValidationError, match="fixtures.command or fixtures.owned_http"):
        load_scenario(write_bundle(tmp_path / "f", _data({"lte": "${fixture.stock}"}, fixtures={"recipe": "x"}, budgets=manifest()["budgets"])))


FUNNELS = {"purchase": {"started": 20, "stages": [{"name": "paid", "reached": 8}], "stalled": [], "undeclared": []}}


@pytest.mark.parametrize(
    ("expected", "finished", "status", "recorded"),
    [
        ({"lte": 8}, True, "pass", {"lte": 8}),
        ({"lte": 5}, True, "fail", {"lte": 5}),
        ({"gte": 9}, True, "fail", {"gte": 9}),
        ({"eq": 8}, True, "pass", {"eq": 8}),
        ({"lte": "${fixture.stock + 3}"}, True, "pass", {"lte": 8}),
        ({"lte": "${planned_journeys}"}, True, "pass", {"lte": 20}),
        # an unfinished run has only partial counts: a ceiling already exceeded still fails, nothing else is proven
        ({"lte": 5}, False, "fail", {"lte": 5}),
        ({"eq": 5}, False, "fail", {"eq": 5}),
        ({"lte": 8}, False, "unknown", {"lte": 8}),
        ({"gte": 9}, False, "unknown", {"gte": 9}),
    ],
)
def test_results_compare_the_funnel_count(expected, finished, status, recorded):
    variables = {"planned_journeys": 20, "seed": 0, "fixture.stock": 5}

    (row,) = stage_assertion_results([check(expected)], FUNNELS, variables, finished)

    assert row["assertion"] == "paid_within_stock"
    assert row["status"] == status
    assert row["expected"] == recorded
    assert row["actual"] == 8


def test_results_without_the_fixture_value_are_unknown():
    (row,) = stage_assertion_results([check({"lte": "${fixture.stock}"})], FUNNELS, {"planned_journeys": 20, "seed": 0}, True)
    assert row["status"] == "unknown"
    assert "fixture.stock" in row["reason"]
    assert "actual" not in row


def _run(tmp_path, monkeypatch, expected, paid=8, **k6):
    scenario = write_bundle(tmp_path / "scenario", _data(expected))
    events = [assertion("accepted_orders_persist") for _ in range(20)] + [stage("paid", f"j{i}") for i in range(paid)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    return runner.verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, **k6)))


def _row(result):
    return next(row for row in result["assertions"] if row["id"] == "paid_within_stock")


def test_verify_fails_when_more_journeys_paid_than_the_fixture_stock(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, {"lte": "${fixture.stock}"})

    assert result["verdict"] == "fail"
    row = _row(result)
    assert (row["status"], row["expected"], row["actual"]) == ("fail", {"lte": 5}, 8)


def test_verify_passes_when_the_count_holds(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, {"lte": "${fixture.stock}"}, paid=5)
    assert result["verdict"] == "pass", result["limitations"]
    assert _row(result)["status"] == "pass"


def test_verify_without_a_finished_engine_is_never_a_pass(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, {"gte": 1}, returncode=1)
    assert _row(result)["status"] == "unknown"
    assert result["verdict"] != "pass"
    assert any("paid_within_stock" in limitation for limitation in result["limitations"])
