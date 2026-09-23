import base64
import hashlib
import hmac
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from litetraffic.auth import AuthError, mint_tokens, redact, token_env_name
from litetraffic.cli import main
from litetraffic.models import Actor, ScenarioManifest
from litetraffic.observation import observe
from litetraffic.runner import verify
from test_runner import assertion, fake_k6
from test_command_fixture import py
from test_scenario import manifest, write_bundle

SECRET = "s3cr3t-signing-key-value"
AUTH = {"kind": "jwt_hs256", "secret_env": "LT_TEST_JWT_SECRET", "claims": {"sub": "buyer-${actor_index}", "run": "${run_id}", "role": "buyer"}, "ttl_seconds": 600}


def _decode(token: str, secret: str = SECRET) -> tuple[dict, dict]:
    header, payload, signature = token.split(".")
    expected = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected
    part = lambda value: json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    return part(header), part(payload)


def _actors(*extra):
    return [{"class": "buyer", "count": 10, "auth_recipe": "jwt", "auth": AUTH}, *extra]


def test_token_env_name_is_upper_snake():
    assert token_env_name("tenant-a.reader") == "LT_TOKEN_TENANT_A_READER"


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        (AUTH | {"ttl_seconds": 86401}, "less than or equal to 86400"),
        (AUTH | {"ttl_seconds": 0}, "greater than 0"),
        (AUTH | {"secret_env": "lower"}, "secret_env must name an uppercase environment variable"),
        (AUTH | {"kind": "rs256"}, "jwt_hs256"),
        (AUTH | {"claims": {"sub": "${tenant}"}}, "unknown placeholder ${tenant}"),
    ],
)
def test_auth_recipe_validation(auth, message):
    with pytest.raises(ValidationError, match=message.replace("$", r"\$").replace("{", r"\{").replace("}", r"\}")):
        Actor.model_validate({"class": "buyer", "count": 1, "auth_recipe": "jwt", "auth": auth})


def test_actor_classes_must_not_share_a_token_variable():
    data = manifest(actors=_actors({"class": "BUYER", "count": 1, "auth_recipe": "jwt", "auth": AUTH}))
    with pytest.raises(ValidationError, match="LT_TOKEN_BUYER"):
        ScenarioManifest.model_validate(data)


def test_mint_signs_hs256_with_substituted_claims_and_ttl():
    actors = ScenarioManifest.model_validate(manifest(actors=[{"class": "guest", "count": 1, "auth_recipe": "none"}, *_actors()])).actors

    tokens = mint_tokens(actors, "run_x", {"LT_TEST_JWT_SECRET": SECRET}, now=1000)

    assert list(tokens) == ["LT_TOKEN_BUYER"]
    header, claims = _decode(tokens["LT_TOKEN_BUYER"])
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert claims == {"sub": "buyer-1", "run": "run_x", "role": "buyer", "iat": 1000, "exp": 1600}


@pytest.mark.parametrize("environ", [{}, {"LT_TEST_JWT_SECRET": ""}])
def test_mint_names_only_the_missing_env(environ):
    actors = ScenarioManifest.model_validate(manifest(actors=_actors())).actors
    with pytest.raises(AuthError, match="^auth secret env LT_TEST_JWT_SECRET missing$"):
        mint_tokens(actors, "run_x", environ)


def test_verify_exports_token_to_k6_and_keeps_it_out_of_artifacts(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario", manifest(actors=_actors()))
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "k6-env.json"))
    monkeypatch.setenv("LT_TEST_JWT_SECRET", SECRET)
    monkeypatch.setenv("LT_TOKEN_STALE", "stale")
    k6 = fake_k6(tmp_path, events, echo_env=("LT_TOKEN_BUYER", "LT_TEST_JWT_SECRET"))

    result = verify("http://example.test", scenario, tmp_path / "runs", str(k6))

    assert result["verdict"] == "pass", result["limitations"]
    k6_env = json.loads((tmp_path / "k6-env.json").read_text())
    assert "LT_TOKEN_STALE" not in k6_env
    token = k6_env["LT_TOKEN_BUYER"]
    assert _decode(token)[1]["run"] == result["run_id"]
    for path in (tmp_path / "runs").rglob("*"):
        if path.is_file():
            text = path.read_text(errors="replace")
            assert token not in text, path.name
            assert SECRET not in text, path.name
    assert "[redacted]" in (tmp_path / "runs" / result["run_id"] / "engine.stdout.log").read_text()


def test_final_observation_can_authenticate_with_a_minted_actor_token(tmp_path, monkeypatch):
    seen = {}

    def respond(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"total": 1000})

    monkeypatch.setattr(
        "litetraffic.runner.observe",
        lambda *args, **kwargs: observe(*args, transport=httpx.MockTransport(respond), **kwargs),
    )
    data = manifest(
        actors=_actors(),
        assertions=["accepted_orders_persist", "ledger_total"],
        observation={"path": "/ledger", "assertion": "ledger_total", "expected": {"/total": 1000}, "bearer_token_env": "LT_TOKEN_BUYER"},
        budgets=manifest()["budgets"] | {"max_requests": 61},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setenv("LT_TEST_JWT_SECRET", SECRET)
    monkeypatch.delenv("LT_TOKEN_BUYER", raising=False)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))

    assert result["verdict"] == "pass", result["limitations"]
    token = seen["authorization"].removeprefix("Bearer ")
    assert _decode(token)[1]["run"] == result["run_id"]


def test_missing_secret_is_an_error_before_k6_starts(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario", manifest(actors=_actors()))
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "k6-env.json"))
    monkeypatch.delenv("LT_TEST_JWT_SECRET", raising=False)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))

    assert result["verdict"] == "error"
    assert result["engine_exit_code"] is None
    assert not (tmp_path / "k6-env.json").exists()
    assert "auth secret env LT_TEST_JWT_SECRET missing" in result["limitations"]


def test_short_secret_is_refused_by_name(tmp_path, monkeypatch):
    with pytest.raises(AuthError, match="^auth secret env LT_TEST_JWT_SECRET is shorter than 8 characters$"):
        mint_tokens([Actor.model_validate(actor) for actor in _actors()], "run_x", {"LT_TEST_JWT_SECRET": "short"})

    scenario = write_bundle(tmp_path / "scenario", manifest(actors=_actors()))
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "k6-env.json"))
    monkeypatch.setenv("LT_TEST_JWT_SECRET", "true")

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [])))

    assert result["verdict"] == "error"
    assert not (tmp_path / "k6-env.json").exists()
    assert "auth secret env LT_TEST_JWT_SECRET is shorter than 8 characters" in result["limitations"]


def test_redact_never_substitutes_short_values():
    text = '{"passed": true, "run_id": "run_1"}'
    assert redact(text, ["true", "run_1"]) == text
    assert redact("key=s3cr3t-signing-key-value", [SECRET]) == "key=[redacted]"


def test_every_kept_output_is_scrubbed_and_still_parses(tmp_path, monkeypatch):
    leak = "import os, sys; print(os.environ['LT_TEST_JWT_SECRET'], file=sys.stderr)"
    data = manifest(
        actors=_actors(),
        fixtures={"recipe": "seeded", "command": {"setup": py(leak), "teardown": py(leak), "timeout_seconds": 2}},
        budgets=manifest()["budgets"] | {"max_seconds": 22},  # 10 s schedule + 2 x (2 s timeout + 4 s stop grace)
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    monkeypatch.setenv("FAKE_K6_ENV", str(tmp_path / "k6-env.json"))
    monkeypatch.setenv("LT_TEST_JWT_SECRET", SECRET)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, echo_env=("LT_TOKEN_BUYER", "LT_TEST_JWT_SECRET"))))

    assert result["verdict"] == "pass", result["limitations"]  # evidence full of `true` still parses
    token = json.loads((tmp_path / "k6-env.json").read_text())["LT_TOKEN_BUYER"]
    run_dir = tmp_path / "runs" / result["run_id"]
    for name in ("console.log", "metrics.jsonl", "engine.stdout.log", "engine.stderr.log", "fixture.json"):
        text = (run_dir / name).read_text()
        assert token not in text and SECRET not in text, name
        assert "[redacted]" in text, name
    metric_lines = [json.loads(line) for line in (run_dir / "metrics.jsonl").read_text().splitlines()]
    assert {"echo": "[redacted] [redacted]"} in [line["data"].get("tags") for line in metric_lines]
    fixture = json.loads((run_dir / "fixture.json").read_text())
    assert fixture["setup"]["stderr"] == fixture["teardown"]["stderr"] == "[redacted]\n"


def test_inspect_lists_auth_secret_env(tmp_path, capsys):
    scenario = write_bundle(tmp_path / "scenario", manifest(actors=_actors()))
    assert main(["inspect", str(scenario), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["secret_env"] == ["LT_TEST_JWT_SECRET"]


@pytest.mark.real_k6
@pytest.mark.skipif(shutil.which("k6") is None, reason="real k6 not installed")
def test_runtime_hmac_helpers_match_python(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "src" / "litetraffic" / "k6" / "runtime.js"
    (tmp_path / "litetraffic").mkdir()
    shutil.copy(helper, tmp_path / "litetraffic" / "runtime.js")
    (tmp_path / "sign.js").write_text(
        'import * as lt from "./litetraffic/runtime.js";\n'
        'export default function () {\n'
        '  console.log(`HEX ${lt.hmacSha256Hex("key", "body")}`);\n'
        '  console.log(`B64 ${lt.hmacSha256Base64("key", "body")}`);\n'
        "}\n"
    )
    run = subprocess.run(["k6", "run", "--quiet", "--console-output", "out.log", "sign.js"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    log = (tmp_path / "out.log").read_text()
    digest = hmac.new(b"key", b"body", hashlib.sha256).digest()
    assert f"HEX {digest.hex()}" in log
    assert f"B64 {base64.b64encode(digest).decode()}" in log
