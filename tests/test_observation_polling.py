"""Eventual observations: `until` re-reads the observer until it converges or the deadline passes."""

import json

import httpx
import pytest
from pydantic import ValidationError

from litetraffic.models import FinalObservation
from litetraffic.observation import observe
from litetraffic.runner import verify
from litetraffic.scenario import ScenarioError, load_scenario
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle


def _config(deadline: float, interval: float = 0.5) -> FinalObservation:
    return FinalObservation(
        path="/ledger", assertion="a", expected={"/total": 1000},
        until={"deadline_seconds": deadline, "interval_seconds": interval},
    )


def _server(*responses: httpx.Response):
    """A fake observer answering `responses` in order, then repeating the last one."""
    calls = []

    def respond(request):
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.MockTransport(respond), calls


def test_until_polls_until_the_expectations_pass():
    stale = httpx.Response(200, json={"total": 900})
    transport, calls = _server(stale, stale, httpx.Response(200, json={"total": 1000}))

    result = observe("http://example.test", _config(10), "run-1", transport=transport)

    assert result["status"] == "pass"
    assert result["attempts"] == len(calls) == 3
    assert 1.0 <= result["elapsed_seconds"] < 3


def test_until_fails_only_after_the_deadline_with_the_last_reading():
    transport, calls = _server(httpx.Response(200, json={"total": 900}))

    result = observe("http://example.test", _config(1), "run-1", transport=transport)

    assert result["status"] == "fail"
    assert result["actual"] == {"/total": 900}
    assert result["attempts"] == len(calls) == 3  # t = 0, 0.5, 1.0
    assert result["elapsed_seconds"] >= 1.0


def test_until_is_unknown_when_the_observer_is_never_reachable():
    transport, calls = _server(httpx.Response(503))

    result = observe("http://example.test", _config(1), "run-1", transport=transport)

    assert result["status"] == "unknown"
    assert result["reason"] == "observer HTTP 503"
    assert result["attempts"] == len(calls) == 3


def test_until_keeps_the_failed_reading_when_a_later_attempt_is_unreachable():
    transport, _ = _server(httpx.Response(200, json={"total": 900}), httpx.Response(503))

    result = observe("http://example.test", _config(1), "run-1", transport=transport)

    assert result["status"] == "fail"
    assert result["attempts"] == 3


def test_until_stops_at_once_when_no_request_can_be_sent(monkeypatch):
    monkeypatch.delenv("LT_MISSING", raising=False)
    config = _config(10).model_copy(update={"bearer_token_env": "LT_MISSING"})
    transport, calls = _server(httpx.Response(200, json={"total": 1000}))

    result = observe("http://example.test", config, "run-1", transport=transport)

    assert result["reason"] == "observer bearer token missing"
    assert calls == [] and "attempts" not in result


@pytest.mark.parametrize("until", [
    {"deadline_seconds": 61, "interval_seconds": 1},
    {"deadline_seconds": 0, "interval_seconds": 0.5},
    {"deadline_seconds": 10, "interval_seconds": 0.4},
    {"deadline_seconds": 1, "interval_seconds": 2},
    {"deadline_seconds": 10},
])
def test_until_bounds_are_validated(until):
    with pytest.raises(ValidationError):
        FinalObservation(path="/x", assertion="a", expected={"/n": 1}, until=until)


def test_until_deadline_and_attempts_are_reserved_in_the_budgets(tmp_path):
    obs = {"path": "/a", "assertion": "a", "expected": {"/n": 1}, "until": {"deadline_seconds": 10, "interval_seconds": 2}}
    # 10 s schedule + 2 s engine + (10 s deadline + 5 s last request); 60 journey requests + 6 attempts
    data = manifest(observation=obs, assertions=["accepted_orders_persist", "a"],
                    budgets=manifest()["budgets"] | {"max_requests": 66, "max_seconds": 27})
    assert load_scenario(write_bundle(tmp_path / "ok", data)).manifest.observations[0].reserved_seconds == 15

    with pytest.raises(ValidationError, match=r"observation \(15 s\) deadlines need 27 s, max_seconds is 26"):
        load_scenario(write_bundle(tmp_path / "time", data | {"budgets": data["budgets"] | {"max_seconds": 26}}))
    with pytest.raises(ScenarioError, match="request budget"):
        load_scenario(write_bundle(tmp_path / "req", data | {"budgets": data["budgets"] | {"max_requests": 65}}))


def test_verify_counts_every_polling_attempt_as_an_observer_request(tmp_path, monkeypatch):
    obs = {"path": "/a", "assertion": "a", "expected": {"/n": 1}, "until": {"deadline_seconds": 10, "interval_seconds": 2}}
    data = manifest(observation=obs, assertions=["accepted_orders_persist", "a"],
                    budgets=manifest()["budgets"] | {"max_requests": 66, "max_seconds": 27})
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setattr(
        "litetraffic.runner.observe",
        lambda *a, **k: {"assertion": "a", "status": "pass", "expected": {"/n": 1}, "actual": {"/n": 1}, "attempts": 3, "elapsed_seconds": 4.0},
    )

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "pass"
    assert result["metrics"]["observer_requests"] == 3
    recorded = json.loads((tmp_path / "runs" / result["run_id"] / "observation.json").read_text())
    assert recorded["attempts"] == 3 and recorded["elapsed_seconds"] == 4.0
