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
        ({"endpoints": [{"method": "GET", "path": "/notes/1", "kind": "read"}]}, "placeholder"),
        ({"endpoints": [{"method": "GET", "path": "/notes/{note}", "kind": "read"}]}, "{note}"),
        ({"identities": [_config()["identities"][0] | {"resources": [{"id": "n1", "org": "o1"}]}, _config()["identities"][1]]}, "same keys"),
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


def test_resources_name_placeholders_for_path_and_body(tmp_path, capsys):
    identities = [
        {"name": "a", "token_env": "A_TOKEN", "resources": [{"id": "org-a", "position": "pos-a"}]},
        {"name": "b", "token_env": "B_TOKEN", "resources": [{"id": "org-b", "position": "pos-b"}]},
    ]
    endpoints = [
        {"method": "GET", "path": "/orgs/{id}", "kind": "read"},
        {"method": "POST", "path": "/positions", "kind": "write", "body": {"organization_id": "{id}", "note": "{literal}"}},
        {"method": "PUT", "path": "/positions/{position}", "kind": "write", "body": {"organization_id": "{id}"}},
    ]
    code, result = _init(tmp_path, _config(identities=identities, endpoints=endpoints), capsys)
    assert code == 0, result
    assert load_scenario(tmp_path / "out").manifest.journeys[0].max_writes == 4


def test_allowed_origins_pass_through_for_observations(tmp_path, capsys):
    observation = {"origin_env": "DB_URL", "path": "/rows", "assertion": "rows_intact", "expected": {"": {"len": 0}}}
    config = _config(observations=[observation], allowed_origins=["http://127.0.0.1:55321"], allowed_origins_env="EXTRA_ORIGINS")
    code, result = _init(tmp_path, config, capsys)
    assert code == 0, result
    manifest = load_scenario(tmp_path / "out").manifest
    assert manifest.allowed_origins == ["http://127.0.0.1:55321"]
    assert manifest.allowed_origins_env == "EXTRA_ORIGINS"


def _kit(tmp_path) -> dict:
    script = (tmp_path / "out" / "journeys.js").read_text()
    return json.loads(script.split("const KIT = ", 1)[1].split(";\nconst MAX_SAMPLES", 1)[0])


READS = [
    {"method": "GET", "path": "/notes/{id}", "kind": "read"},
    {"method": "GET", "path": "/notes/{id}/meta", "kind": "read", "name": "meta"},
]


@pytest.mark.parametrize(("read_back", "index"), [(None, 0), (1, 1), ("meta", 1)])
def test_write_endpoints_pick_their_read_back(tmp_path, capsys, read_back, index):
    write = {"method": "PUT", "path": "/notes/{id}/meta", "kind": "write", "body": {"m": 1}}
    if read_back is not None:
        write["read_back"] = read_back
    code, result = _init(tmp_path, _config(endpoints=[*READS, write]), capsys)
    assert code == 0, result
    assert _kit(tmp_path)["endpoints"][2]["read_back"] == index


@pytest.mark.parametrize(
    ("read_back", "message"),
    [(5, "read_back 5"), (2, "read_back 2"), ("nope", "read_back 'nope'"), (True, "read_back")],
)
def test_read_back_must_name_a_read_endpoint(tmp_path, capsys, read_back, message):
    write = {"method": "PUT", "path": "/notes/{id}", "kind": "write", "body": {}, "read_back": read_back}
    code, result = _init(tmp_path, _config(endpoints=[*READS, write]), capsys)
    assert code == 3 and message in result["error"], result


def test_read_back_is_only_for_writes_and_names_are_distinct(tmp_path, capsys):
    code, result = _init(tmp_path, _config(endpoints=[READS[0] | {"read_back": 0}]), capsys)
    assert code == 3 and "read_back" in result["error"], result
    code, result = _init(tmp_path, _config(endpoints=[READS[1], READS[1]]), capsys)
    assert code == 3 and "distinct" in result["error"], result


def test_unauthenticated_probe_adds_an_assertion_and_its_requests(tmp_path, capsys):
    code, result = _init(tmp_path, _config(unauthenticated_probe=True), capsys)
    assert code == 0, result
    manifest = load_scenario(tmp_path / "out").manifest
    assert "unauthenticated_rejected" in manifest.assertions
    assert manifest.journeys[0].max_requests == 15 + 3  # one anonymous read per resource and read endpoint
    assert manifest.journeys[0].expected_statuses["unauthenticated"] == [401, 403, 404]
    code, result = _init(tmp_path, _config(), capsys)
    assert "unauthenticated_rejected" not in load_scenario(tmp_path / "out").manifest.assertions
