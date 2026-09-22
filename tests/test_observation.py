import httpx

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
    }


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
