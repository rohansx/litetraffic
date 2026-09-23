import json
from pathlib import Path

import pytest

from litetraffic.cli import main
from litetraffic.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "examples" / "tenant_api" / "kit.json"


def _config(**changes) -> dict:
    config = {
        "name": "notes-isolation",
        "identities": [
            {"name": "alice", "token_env": "ALICE_TOKEN", "resources": ["n1"]},
            {"name": "bob", "auth": {"secret_env": "JWT_SECRET", "claims": {"sub": "bob"}, "ttl_seconds": 300}, "resources": ["n2", "n3"]},
        ],
        "endpoints": [
            {"method": "GET", "path": "/notes/{id}", "kind": "read"},
            {"method": "PUT", "path": "/notes/{id}", "kind": "write", "body": {"text": "x"}},
        ],
    }
    return config | changes


def _init(tmp_path, config, capsys) -> tuple[int, dict]:
    path = tmp_path / "kit.json"
    path.write_text(json.dumps(config))
    code = main(["init", "tenant-isolation", "--config", str(path), "--out", str(tmp_path / "out"), "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_example_kit_generates_a_bundle_that_inspects(tmp_path, capsys):
    out = tmp_path / "scenario"
    assert main(["init", "tenant-isolation", "--config", str(KIT), "--out", str(out), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    bundle = load_scenario(out)
    assert result["ok"] and result["scenario_sha256"] == bundle.digest
    assert 'from "./litetraffic/runtime.js"' in (out / "journeys.js").read_text()
    manifest = bundle.manifest
    assert {"own_access", "cross_tenant_read_blocked", "tenant_fixture_intact"} <= set(manifest.assertions)
    assert manifest.journeys[0].expected_statuses["cross_tenant"] == [401, 403, 404]
    assert main(["inspect", str(out), "--json"]) == 0


def test_generation_is_deterministic(tmp_path, capsys):
    outputs = []
    for name in ("one", "two"):
        assert main(["init", "tenant-isolation", "--config", str(KIT), "--out", str(tmp_path / name)]) == 0
        outputs.append([(tmp_path / name / file).read_bytes() for file in ("manifest.json", "journeys.js")])
    assert outputs[0] == outputs[1]


def test_writes_add_read_back_checks_and_budgets(tmp_path, capsys):
    code, result = _init(tmp_path, _config(), capsys)
    assert code == 0, result
    manifest = load_scenario(tmp_path / "out").manifest
    assert {"cross_tenant_write_blocked", "victim_unchanged"} <= set(manifest.assertions)
    # own reads 3; cross reads 3; cross writes 3 x (before, write, after) = 9
    assert manifest.journeys[0].max_requests == 15
    assert manifest.journeys[0].max_writes == 3
    assert [actor.auth_recipe for actor in manifest.actors] == ["kit-bearer-env", "kit-jwt"]
    script = (tmp_path / "out" / "journeys.js").read_text()
    assert "lt.deepEqual(" in script and "LT_TOKEN_BOB" in script and "ALICE_TOKEN" in script


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"identities": _config()["identities"][:1]}, "identities"),
        ({"endpoints": [{"method": "GET", "path": "/notes/1", "kind": "read"}]}, "{id}"),
        ({"endpoints": [{"method": "PUT", "path": "/notes/{id}", "kind": "write"}]}, "read endpoint"),
        ({"endpoints": [{"method": "POST", "path": "/notes/{id}", "kind": "read"}]}, "GET"),
        ({"identities": [_config()["identities"][0], _config()["identities"][0]]}, "distinct"),
        ({"identities": [{"name": "x", "resources": ["1"]}, _config()["identities"][1]]}, "auth, token_env or headers"),
        ({"identities": [_config()["identities"][0] | {"auth": _config()["identities"][1]["auth"]}, _config()["identities"][1]]}, "not both"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_config_is_rejected_with_a_clear_error(tmp_path, capsys, changes, message):
    code, result = _init(tmp_path, _config(**changes), capsys)
    assert code == 3
    assert result["error"].startswith("invalid kit config") and message in result["error"], result
    assert not (tmp_path / "out").exists()
