"""`${fixture.KEY}` in a final observation's path and expected values, resolved from the run's own fixture."""

import json
import sys

import httpx
import pytest
from pydantic import ValidationError

import litetraffic.runner as runner
from litetraffic.inspection import inspect_payload
from litetraffic.models import FinalObservation
from litetraffic.observation import observe
from litetraffic.scenario import load_scenario
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle

COMMAND = {"setup": [sys.executable, "-c", "print('{\"sku\": \"S 1/x\", \"stock\": 5}')"], "teardown": [sys.executable, "-c", "pass"], "timeout_seconds": 2}
OWNED = {"create_path": "/fixtures", "delete_path": "/fixtures/{fixture_id}", "id_pointer": "/id"}
BUDGETS = manifest()["budgets"] | {"max_seconds": 40, "max_requests": 70, "max_write_attempts": 30}


def _config(path="/items/${fixture.sku}", expected=None):
    return FinalObservation(path=path, assertion="a", expected=expected or {"/stock": 5})


def _bundle(tmp_path, fixtures, path="/items/${fixture.sku}", expected=None):
    obs = {"path": path, "assertion": "stock_left", "expected": expected or {"/stock": 5}}
    data = manifest(fixtures=fixtures, observation=obs, assertions=["accepted_orders_persist", "stock_left"], budgets=BUDGETS)
    return load_scenario(write_bundle(tmp_path, data))


def test_path_is_url_encoded_from_the_fixture_value():
    seen = []

    def respond(request):
        seen.append(request.url.raw_path)
        return httpx.Response(200, json={"stock": 5})

    result = observe("http://example.test", _config(), "run-1", transport=httpx.MockTransport(respond), variables={"fixture.sku": "S 1/x?y"})

    assert result["status"] == "pass"
    assert seen == [b"/items/S%201%2Fx%3Fy"]


def test_expected_values_use_fixture_values_as_literals_and_in_expressions():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"stock": 5, "sku": "S1", "left": 4}))
    config = _config("/items", {"/stock": "${fixture.stock}", "/left": {"lte": "${fixture.stock - 1}"}, "/sku": "${fixture.sku}"})

    result = observe("http://example.test", config, "r", transport=transport, variables={"fixture.stock": 5, "fixture.sku": "S1"})

    assert result["status"] == "pass"
    assert result["expected"] == {"/stock": 5, "/left": {"lte": 4}, "/sku": "S1"}
    assert result["expressions"] == {"/stock": "${fixture.stock}", "/left": {"lte": "${fixture.stock - 1}"}, "/sku": "${fixture.sku}"}


@pytest.mark.parametrize("variables", [{}, {"fixture.sku": ""}, {"fixture.sku": ".."}, {"fixture.sku": True}, {"fixture.sku": {"a": 1}}])
def test_missing_or_unusable_fixture_value_is_unknown_and_sends_nothing(variables):
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("must not request")))

    result = observe("http://example.test", _config(), "r", transport=transport, variables=variables)

    assert result["status"] == "unknown"
    assert result["reason"].startswith("observer fixture value")


def test_non_integer_fixture_value_in_an_expression_is_unknown():
    config = _config("/items", {"/stock": "${fixture.stock + 1}"})
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("must not request")))
    result = observe("http://example.test", config, "r", transport=transport, variables={"fixture.stock": "five"})
    assert result["status"] == "unknown"
    assert result["reason"].startswith("observer fixture value")


def test_load_rejects_references_the_manifest_cannot_provide(tmp_path):
    with pytest.raises(ValidationError, match="fixtures.command or fixtures.owned_http"):
        _bundle(tmp_path / "none", {"recipe": "x"})
    with pytest.raises(ValidationError, match=r"owned_http provides only \$\{fixture.id\}"):
        _bundle(tmp_path / "owned", {"recipe": "x", "owned_http": OWNED})
    with pytest.raises(ValidationError, match="fixture.sku"):
        _bundle(tmp_path / "expr", {"recipe": "x", "owned_http": OWNED}, path="/items", expected={"/n": "${fixture.sku + 1}"})
    with pytest.raises(ValidationError, match="malformed"):
        _bundle(tmp_path / "bad", {"recipe": "x", "command": COMMAND}, path="/items/${fixture.a-b}")
    with pytest.raises(ValidationError, match="same-origin"):
        _bundle(tmp_path / "host", {"recipe": "x", "command": COMMAND}, path="${fixture.sku}")


def test_load_accepts_any_command_key_and_the_owned_http_id(tmp_path):
    assert _bundle(tmp_path / "command", {"recipe": "x", "command": COMMAND}).manifest.observations
    assert _bundle(tmp_path / "owned", {"recipe": "x", "owned_http": OWNED}, path="/items/${fixture.id}").manifest.observations


def test_inspect_shows_fixture_references_as_written(tmp_path):
    bundle = _bundle(tmp_path, {"recipe": "x", "command": COMMAND}, expected={"/stock": "${fixture.stock}"})
    payload = inspect_payload(bundle)
    assert payload["observation_path"] == "/items/${fixture.sku}"
    assert payload["observation_expected"] == {"/stock": "${fixture.stock}"}


def _verify(tmp_path, monkeypatch, bundle, requests):
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))

    def respond(request):
        requests.append(str(request.url.raw_path, "ascii"))
        return httpx.Response(200, json={"stock": 5})

    real = runner.observe
    monkeypatch.setattr(runner, "observe", lambda *a, **k: real(*a, **k, transport=httpx.MockTransport(respond)))
    return runner.verify("http://example.test", bundle.root, tmp_path / "runs", str(fake_k6(tmp_path, events)))


def test_verify_resolves_command_fixture_setup_output(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path / "s", {"recipe": "x", "command": COMMAND}, expected={"/stock": "${fixture.stock}"})
    requests = []

    result = _verify(tmp_path, monkeypatch, bundle, requests)

    assert result["verdict"] == "pass", result["limitations"]
    assert requests == ["/items/S%201%2Fx"]
    observed = json.loads((tmp_path / "runs" / result["run_id"] / "observation.json").read_text())
    assert observed["expected"] == {"/stock": 5}


def test_verify_resolves_the_owned_http_fixture_id(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path / "s", {"recipe": "x", "owned_http": OWNED}, path="/items/${fixture.id}")
    monkeypatch.setattr(runner, "create_fixture", lambda *a, **k: {"status": "created", "fixture_id": "fx-9", "requests": 1})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda *a, **k: {"status": "deleted", "requests": 1})
    requests = []

    result = _verify(tmp_path, monkeypatch, bundle, requests)

    assert requests == ["/items/fx-9"]
    assert result["verdict"] == "pass", result["limitations"]


def test_verify_without_the_setup_key_is_unknown_not_a_pass(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path / "s", {"recipe": "x", "command": COMMAND}, path="/items/${fixture.nope}")
    requests = []

    result = _verify(tmp_path, monkeypatch, bundle, requests)

    assert requests == []
    assert result["verdict"] == "inconclusive"
    assert result["assertions"][1]["status"] == "unknown"
    assert result["assertions"][1]["reason"].startswith("observer fixture value")
    assert result["metrics"]["observer_requests"] == 0
