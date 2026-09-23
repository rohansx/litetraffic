import json

import httpx
import pytest
from pydantic import ValidationError

from litetraffic.models import FinalObservation
from litetraffic.observation import observe


def test_observer_checks_expected_json_pointers_with_bearer_auth(monkeypatch):
    monkeypatch.setenv("LT_TEST_TOKEN", "secret")

    def respond(request):
        assert request.url.path == "/reports/ledger"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["x-litetraffic-run"] == "run-1"
        return httpx.Response(200, json={"total": 1000, "regions": {"west": 100}})

    config = FinalObservation(
        path="/reports/ledger",
        assertion="ledger_complete",
        expected={"/total": 1000, "/regions/west": 100},
        bearer_token_env="LT_TEST_TOKEN",
    )

    result = observe("http://example.test", config, "run-1", transport=httpx.MockTransport(respond))

    assert result == {
        "assertion": "ledger_complete",
        "status": "pass",
        "expected": {"/total": 1000, "/regions/west": 100},
        "actual": {"/total": 1000, "/regions/west": 100},
        "checks": {
            "/total": {"matcher": {"eq": 1000}, "actual": 1000, "pass": True},
            "/regions/west": {"matcher": {"eq": 100}, "actual": 100, "pass": True},
        },
    }


def test_observer_can_read_only_the_run_owned_fixture():
    config = FinalObservation(path="/reports/ledger", assertion="ledger_complete", expected={"/total": 1000})
    def respond(request):
        assert request.headers["x-litetraffic-fixture"] == "owned-1"
        return httpx.Response(200, json={"total": 1000})

    result = observe("http://example.test", config, "run-1", transport=httpx.MockTransport(respond), fixture_id="owned-1")
    assert result["status"] == "pass"


def test_observer_missing_token_is_unknown_and_never_calls_target(monkeypatch):
    monkeypatch.delenv("LT_TEST_TOKEN", raising=False)
    config = FinalObservation(
        path="/reports/ledger", assertion="ledger_complete", expected={"/total": 1000}, bearer_token_env="LT_TEST_TOKEN"
    )
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("must not request")))

    result = observe("http://example.test", config, "run-1", transport=transport)

    assert result["status"] == "unknown"
    assert "token missing" in result["reason"]


def test_observer_rejects_partial_data_despite_http_200():
    config = FinalObservation(path="/reports/ledger", assertion="ledger_complete", expected={"/total": 1000, "/regions/west": 100})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"total": 900, "regions": {}}))

    result = observe("http://example.test", config, "run-1", transport=transport)

    assert result["status"] == "fail"
    assert result["actual"] == {"/total": 900, "/regions/west": None}


def test_observer_distinguishes_a_missing_field_from_expected_null():
    config = FinalObservation(path="/reports/ledger", assertion="nullable_field", expected={"/value": None})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    result = observe("http://example.test", config, "run-1", transport=transport)

    assert result["status"] == "fail"
    assert result["missing"] == ["/value"]


def test_matchers_record_matcher_actual_and_pass_per_pointer():
    config = FinalObservation(
        path="/rows",
        assertion="rows_ok",
        expected={
            "/total": 1000,
            "/count": {"gte": 3},
            "/max": {"lte": 10},
            "/items": {"len": 2},
            "/name": {"len": 3},
            "/meta": {"len": 1},
            "/gone": {"exists": False},
            "/here": {"exists": True},
            "/status": {"eq": "done"},
        },
    )
    body = {"total": 1000, "count": 3, "max": 11, "items": [1, 2], "name": "abc", "meta": {"a": 1}, "here": None, "status": "done"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    result = observe("http://example.test", config, "run-1", transport=transport)

    assert result["status"] == "fail"
    assert result["checks"]["/total"] == {"matcher": {"eq": 1000}, "actual": 1000, "pass": True}
    assert result["checks"]["/count"] == {"matcher": {"gte": 3}, "actual": 3, "pass": True}
    assert result["checks"]["/max"] == {"matcher": {"lte": 10}, "actual": 11, "pass": False}
    assert result["checks"]["/gone"] == {"matcher": {"exists": False}, "actual": None, "pass": True}
    assert all(result["checks"][p]["pass"] for p in ("/items", "/name", "/meta", "/here", "/status"))


def test_matchers_pass_when_all_hold_and_reject_wrong_types():
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": {"gte": 1}, "/s": {"len": 1}, "/b": {"gte": 0}})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"n": 2, "s": 5, "b": True}))
    result = observe("http://example.test", config, "run-1", transport=transport)
    assert result["checks"]["/n"]["pass"] is True
    assert result["checks"]["/s"]["pass"] is False
    assert result["checks"]["/b"]["pass"] is False

    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": {"gte": 1}, "/gone": {"exists": False}})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"n": 2}))
    assert observe("http://example.test", config, "run-1", transport=transport)["status"] == "pass"


@pytest.mark.parametrize(
    "expected",
    [{"/n": {"gte": 1, "lte": 2}}, {"/n": {"gte": "1"}}, {"/n": {"len": -1}}, {"/n": {"len": 1.5}}, {"/n": {"exists": 1}}],
)
def test_invalid_matchers_are_rejected(expected):
    with pytest.raises(ValidationError, match="matcher"):
        FinalObservation(path="/rows", assertion="rows_ok", expected=expected)


def test_non_matcher_objects_stay_literal_equality():
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/obj": {"a": 1}})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"obj": {"a": 1}}))
    result = observe("http://example.test", config, "run-1", transport=transport)
    assert result["status"] == "pass"
    assert result["checks"]["/obj"]["matcher"] == {"eq": {"a": 1}}


def test_observer_reads_extra_origin_with_env_headers_and_never_records_values(monkeypatch):
    monkeypatch.setenv("PGRST_KEY", "super-secret-key")

    def respond(request):
        assert str(request.url) == "https://db.example.test/rest/v1/orders?select=id"
        assert request.headers["apikey"] == "super-secret-key"
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    config = FinalObservation(
        origin="https://db.example.test/",
        path="/rest/v1/orders?select=id",
        assertion="rows_ok",
        expected={"": {"len": 2}},
        headers_env={"apikey": "PGRST_KEY"},
    )
    result = observe("http://example.test", config, "run-1", transport=httpx.MockTransport(respond))

    assert result["status"] == "pass"
    assert "super-secret-key" not in json.dumps(result)


def test_missing_header_env_is_unknown_naming_the_variable_only(monkeypatch):
    monkeypatch.delenv("PGRST_KEY", raising=False)
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1}, headers_env={"apikey": "PGRST_KEY"})
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("must not request")))

    result = observe("http://example.test", config, "run-1", transport=transport)

    assert result["status"] == "unknown"
    assert "PGRST_KEY" in result["reason"]


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"origin": "ftp://db.example.test"}, "http or https"),
        ({"origin": "https://user:pw@db.example.test"}, "credentials"),
        ({"origin": "http://169.254.169.254"}, "link-local"),
        ({"headers_env": {"apikey": "lower_case"}}, "environment variable"),
        ({"headers_env": {"bad header": "KEY"}}, "header name"),
    ],
)
def test_unsafe_observation_origins_and_header_refs_are_rejected(fields, message):
    with pytest.raises(ValidationError, match=message):
        FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1}, **fields)


def test_expected_expressions_resolve_against_plan_and_record_both():
    config = FinalObservation(
        path="/rows",
        assertion="rows_ok",
        expected={
            "/balance": "${planned_journeys * 10 + seed}",
            "/count": {"gte": "${(planned_journeys - 1) * 2}"},
            "/debt": "${-planned_journeys}",
            "/note": "total ${planned_journeys}",
        },
    )
    body = {"balance": 203, "count": 38, "debt": -20, "note": "total ${planned_journeys}"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    result = observe("http://example.test", config, "run-1", transport=transport, variables={"planned_journeys": 20, "seed": 3})

    assert result["status"] == "pass"
    assert result["expected"] == {"/balance": 203, "/count": {"gte": 38}, "/debt": -20, "/note": "total ${planned_journeys}"}
    assert result["checks"]["/balance"]["matcher"] == {"eq": 203}
    assert result["expressions"] == {
        "/balance": "${planned_journeys * 10 + seed}",
        "/count": {"gte": "${(planned_journeys - 1) * 2}"},
        "/debt": "${-planned_journeys}",
    }


def test_observation_without_expressions_records_no_expressions():
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"n": 1}))
    assert "expressions" not in observe("http://example.test", config, "run-1", transport=transport)


@pytest.mark.parametrize(
    "value",
    ["${planned_journeys / 2}", "${os}", "${__import__('os')}", "${1 +}", "${2 ** 3}", "${}", "${1.5}", {"lte": "${seed.real}"}, "${(1}"],
)
def test_invalid_expected_expressions_are_rejected(value):
    with pytest.raises(ValidationError, match="expression"):
        FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": value})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("${" + "(" * 33 + "1" + ")" * 33 + "}", "nests deeper than 32"),
        ("${" + "(" * 1000 + "1" + ")" * 1000 + "}", "longer than 200"),
        ("${" + "-" * 150 + "1}", "nests deeper than 32"),
        ("${" + "1+" * 100 + "1}", "longer than 200"),
        ("${٣}", "expression"),
        ("${planned_journeys + １}", "expression"),
    ],
)
def test_oversized_deep_or_non_ascii_expressions_are_rejected(value, message):
    with pytest.raises(ValidationError, match=message):
        FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": value})


def test_expressions_at_the_limits_are_accepted():
    deep = "${" + "(" * 32 + "1" + ")" * 32 + "}"
    FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": deep, "/m": "${" + "1+" * 98 + "1}"})


def test_observe_with_expression_and_no_variables_raises_a_clear_value_error():
    config = FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": "${planned_journeys}"})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"n": 1}))
    with pytest.raises(ValueError, match="no value for 'planned_journeys'"):
        observe("http://example.test", config, "run-1", transport=transport)
