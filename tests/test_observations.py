"""Multiple observations, env-provided origins and compact recorded actuals."""

import json

import httpx
import pytest
from pydantic import ValidationError

from litetraffic.cli import main
from litetraffic.models import FinalObservation
from litetraffic.observation import observe
from litetraffic.runner import verify
from litetraffic.scenario import ScenarioError, load_scenario
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle


def _obs(assertion_id: str, **extra) -> dict:
    return {"path": f"/{assertion_id}", "assertion": assertion_id, "expected": {"/n": 1}} | extra


def test_multiple_observations_each_need_a_declared_assertion_budget_and_deadline(tmp_path):
    data = manifest(
        observations=[_obs("a"), _obs("b")],
        assertions=["accepted_orders_persist", "a", "b"],
        budgets=manifest()["budgets"] | {"max_requests": 62, "max_seconds": 22},
    )
    assert [o.assertion for o in load_scenario(write_bundle(tmp_path / "ok", data)).manifest.observations] == ["a", "b"]

    with pytest.raises(ScenarioError, match="request budget"):
        load_scenario(write_bundle(tmp_path / "req", data | {"budgets": data["budgets"] | {"max_requests": 61}}))
    with pytest.raises(ValidationError, match=r"observation \(10 s\) deadlines need 22 s, max_seconds is 21"):  # 10 s schedule + 2 s engine + 2 x 5 s
        load_scenario(write_bundle(tmp_path / "time", data | {"budgets": data["budgets"] | {"max_seconds": 21}}))
    with pytest.raises(ValidationError, match="declared in assertions"):
        load_scenario(write_bundle(tmp_path / "undeclared", data | {"assertions": ["accepted_orders_persist", "a"]}))
    with pytest.raises(ValidationError, match="distinct"):
        load_scenario(write_bundle(tmp_path / "dup", data | {"observations": [_obs("a"), _obs("a")]}))
    with pytest.raises(ValidationError, match="not both"):
        load_scenario(write_bundle(tmp_path / "both", data | {"observation": _obs("a"), "observations": [_obs("b")]}))


def test_legacy_single_observation_is_the_only_entry_in_observations(tmp_path):
    data = manifest(observation=_obs("a"), assertions=["accepted_orders_persist", "a"], budgets=manifest()["budgets"] | {"max_requests": 61})
    assert [o.assertion for o in load_scenario(write_bundle(tmp_path, data)).manifest.observations] == ["a"]


def test_origins_compare_scheme_and_host_case_insensitively_and_reject_query_or_fragment(tmp_path):
    data = manifest(
        observation=_obs("a", origin="https://DB.Example.test"),
        allowed_origins=["HTTPS://db.example.TEST/"],
        assertions=["accepted_orders_persist", "a"],
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    assert load_scenario(write_bundle(tmp_path / "ok", data)).manifest.observation.origin == "https://db.example.test"
    for n, bad in enumerate(["https://db.example.test/?x=1", "https://db.example.test#frag"]):
        with pytest.raises(ValidationError, match="query or fragment"):
            load_scenario(write_bundle(tmp_path / f"o{n}", data | {"observation": _obs("a", origin=bad)}))
        with pytest.raises(ValidationError, match="query or fragment"):
            load_scenario(write_bundle(tmp_path / f"a{n}", data | {"allowed_origins": [bad]}))


def test_origin_env_and_allowed_origins_env_name_uppercase_variables(tmp_path):
    base = manifest(assertions=["accepted_orders_persist", "a"], budgets=manifest()["budgets"] | {"max_requests": 61})
    ok = base | {"observation": _obs("a", origin_env="DB_ORIGIN"), "allowed_origins_env": "DB_ORIGINS"}
    loaded = load_scenario(write_bundle(tmp_path / "ok", ok)).manifest
    assert loaded.observation.origin_env == "DB_ORIGIN" and loaded.allowed_origins_env == "DB_ORIGINS"
    with pytest.raises(ValidationError, match="environment variable"):
        load_scenario(write_bundle(tmp_path / "lower", base | {"observation": _obs("a", origin_env="db_origin")}))
    with pytest.raises(ValidationError, match="environment variable"):
        load_scenario(write_bundle(tmp_path / "lower2", ok | {"allowed_origins_env": "db"}))
    with pytest.raises(ValidationError, match="origin or origin_env"):
        load_scenario(write_bundle(tmp_path / "both", ok | {"observation": _obs("a", origin="https://x.test", origin_env="DB_ORIGIN")}))


def _never(request):
    raise AssertionError("must not request")


def test_origin_env_reads_an_allowed_origin_case_insensitively():
    seen = []
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1}, origin_env="DB_ORIGIN")
    transport = httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200, json={"n": 1}))

    result = observe(
        "http://example.test", config, "run-1", transport=transport,
        environ={"DB_ORIGIN": "HTTPS://DB.Example.test/"}, allowed_origins=["https://db.example.test"],
    )

    assert result["status"] == "pass"
    assert seen == ["https://db.example.test/rows"]


def test_origin_env_may_be_allowed_by_allowed_origins_env():
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1}, origin_env="DB_ORIGIN")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"n": 1}))
    environ = {"DB_ORIGIN": "https://db.example.test", "DB_ORIGINS": "https://other.test, https://db.example.test"}

    result = observe("http://example.test", config, "run-1", transport=transport, environ=environ, allowed_origins_env="DB_ORIGINS")

    assert result["status"] == "pass"


@pytest.mark.parametrize(
    "environ, reason",
    [
        ({}, "observer origin env DB_ORIGIN missing"),
        ({"DB_ORIGIN": "https://evil.test"}, "observer origin env DB_ORIGIN is not an allowed origin"),
        ({"DB_ORIGIN": "https://db.example.test/?x=1"}, "observer origin env DB_ORIGIN is not an allowed origin"),
        ({"DB_ORIGIN": "ftp://db.example.test"}, "observer origin env DB_ORIGIN is not an allowed origin"),
    ],
)
def test_origin_env_problems_are_unknown_naming_only_the_variable(environ, reason):
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1}, origin_env="DB_ORIGIN")

    result = observe(
        "http://example.test", config, "run-1", transport=httpx.MockTransport(_never),
        environ=environ, allowed_origins=["https://db.example.test"],
    )

    assert result == {"assertion": "rows_ok", "status": "unknown", "reason": reason}


def test_len_and_exists_record_a_compact_actual_never_the_body():
    body = [{"id": index, "blob": "x" * 100} for index in range(50)]
    config = FinalObservation(
        path="/rows",
        assertion="rows_ok",
        expected={"": {"len": 50}, "/0": {"exists": True}, "/0/blob": {"len": 100}, "/99": {"exists": False}, "/0/id": {"len": 1}},
    )
    result = observe("http://example.test", config, "run-1", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))

    assert result["actual"] == {
        "": {"type": "array", "length": 50},
        "/0": True,
        "/0/blob": {"type": "string", "length": 100},
        "/99": False,
        "/0/id": {"type": "number"},
    }
    assert result["checks"][""]["actual"] == {"type": "array", "length": 50}
    assert "xxxx" not in json.dumps(result)


def test_large_recorded_actuals_are_truncated_to_2kb_with_a_marker():
    body = {"big": ["y" * 100] * 100}
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/big": {"eq": []}})
    result = observe("http://example.test", config, "run-1", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))

    actual = result["actual"]["/big"]
    assert isinstance(actual, str) and actual.endswith("...[truncated]")
    assert len(actual.encode()) <= 2048
    assert result["checks"]["/big"]["actual"] == actual
    assert result["status"] == "fail"


def test_verify_runs_each_observation_after_k6_and_records_each(tmp_path, monkeypatch):
    data = manifest(
        observations=[_obs("a"), _obs("b", origin_env="DB_ORIGIN")],
        assertions=["accepted_orders_persist", "a", "b"],
        allowed_origins=["https://db.example.test"],
        allowed_origins_env="DB_ORIGINS",
        budgets=manifest()["budgets"] | {"max_requests": 62, "max_seconds": 22},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    calls = []

    def fake_observe(target, config, run_id, **kwargs):
        calls.append((config.assertion, kwargs["allowed_origins"], kwargs["allowed_origins_env"]))
        if config.assertion == "a":
            return {"assertion": "a", "status": "pass", "expected": {"/n": 1}, "actual": {"/n": 1}}
        return {"assertion": "b", "status": "fail", "expected": {"/n": 1}, "actual": {"/n": 2}}

    monkeypatch.setattr("litetraffic.runner.observe", fake_observe)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert calls == [("a", ["https://db.example.test"], "DB_ORIGINS"), ("b", ["https://db.example.test"], "DB_ORIGINS")]
    assert result["verdict"] == "fail"
    assert {row["id"]: row["status"] for row in result["assertions"]} == {"accepted_orders_persist": "pass", "a": "pass", "b": "fail"}
    assert result["assertions"][2]["actual"] == {"/n": 2}
    assert result["metrics"]["observer_requests"] == 2
    recorded = json.loads((tmp_path / "runs" / result["run_id"] / "observation.json").read_text())
    assert [entry["assertion"] for entry in recorded] == ["a", "b"]


def test_verify_counts_no_request_for_an_observation_stopped_before_sending(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_ORIGIN", raising=False)
    data = manifest(
        observations=[_obs("a", origin_env="DB_ORIGIN"), _obs("b")],
        assertions=["accepted_orders_persist", "a", "b"],
        budgets=manifest()["budgets"] | {"max_requests": 62, "max_seconds": 22},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    real_observe = observe

    def fake_observe(target, config, run_id, **kwargs):
        if config.assertion == "a":
            return real_observe(target, config, run_id, **kwargs)
        return {"assertion": "b", "status": "unknown", "reason": "observer HTTP 503"}

    monkeypatch.setattr("litetraffic.runner.observe", fake_observe)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "inconclusive"
    assert result["metrics"]["observer_requests"] == 1
    assert result["assertions"][1]["reason"] == "observer origin env DB_ORIGIN missing"


def test_inspect_lists_every_observation(tmp_path, capsys):
    data = manifest(
        observations=[_obs("a", bearer_token_env="A_TOKEN"), _obs("b", expected={"/n": "${planned_journeys}"}, headers_env={"apikey": "B_KEY"})],
        assertions=["accepted_orders_persist", "a", "b"],
        budgets=manifest()["budgets"] | {"max_requests": 62, "max_seconds": 22},
    )

    assert main(["inspect", str(write_bundle(tmp_path, data)), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["maximum_observation_requests"] == 2
    assert output["secret_env"] == ["A_TOKEN", "B_KEY"]
    assert output["observations"] == [
        {"assertion": "a", "path": "/a", "expected": {"/n": 1}},
        {"assertion": "b", "path": "/b", "expected": {"/n": 20}},
    ]
    assert main(["inspect", str(tmp_path)]) == 0
    assert "observation b expected: {\"/n\": 20}" in capsys.readouterr().out
