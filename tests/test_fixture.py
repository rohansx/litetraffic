import httpx

from litetraffic.fixture import cleanup_fixture, create_fixture
from litetraffic.models import OwnedHttpFixture


CONFIG = OwnedHttpFixture(create_path="/fixtures", delete_path="/fixtures/{fixture_id}", id_pointer="/id", create_body={"total": 1000})


def test_owned_fixture_uses_run_scope_and_deletes_only_its_id():
    calls = []

    def target(request):
        calls.append((request.method, request.url.path, request.headers.get("X-LiteTraffic-Run")))
        if request.method == "POST":
            return httpx.Response(201, json={"id": "fixture-42"})
        return httpx.Response(204)

    transport = httpx.MockTransport(target)
    created = create_fixture("http://target.test", CONFIG, "run-42", transport)
    deleted = cleanup_fixture("http://target.test", CONFIG, "run-42", created["fixture_id"], transport)

    assert created == {"status": "created", "fixture_id": "fixture-42", "requests": 1}
    assert deleted == {"status": "deleted", "requests": 1}
    assert calls == [("POST", "/fixtures", "run-42"), ("DELETE", "/fixtures/fixture-42", "run-42")]


def test_owned_fixture_rejects_an_unsafe_id_without_cleanup():
    def target(request):
        return httpx.Response(201, json={"id": "../../all"})

    created = create_fixture("http://target.test", CONFIG, "run-42", httpx.MockTransport(target))
    assert created == {"status": "error", "reason": "invalid fixture id", "requests": 1}


def test_owned_fixture_missing_secret_does_not_send_request(monkeypatch):
    monkeypatch.delenv("LT_FIXTURE_TEST_TOKEN", raising=False)
    config = CONFIG.model_copy(update={"bearer_token_env": "LT_FIXTURE_TEST_TOKEN"})
    def unexpected(request):
        raise AssertionError("request should not be sent")

    assert create_fixture("http://target.test", config, "run-42", httpx.MockTransport(unexpected)) == {
        "status": "error", "reason": "fixture bearer token missing", "requests": 0,
    }
