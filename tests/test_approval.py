import json

import pytest

from litetraffic.approval import origin
from litetraffic.cli import main
from litetraffic.scenario import load_scenario

from test_scenario import write_bundle

TARGET = "http://localhost:3000"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "litetraffic.cli.verify",
        lambda *args: {"verdict": "pass", "lifecycle": "finished"},
    )
    return tmp_path


def approve(scenario, target=TARGET, profile="local-test"):
    return main(["approve", str(scenario), "--target-profile", profile, "--target", target, "--json"])


def verify(scenario, *extra, target=TARGET):
    return main(["verify", str(scenario), "--target", target, *extra, "--json"])


def test_approve_writes_digest_profile_origin_and_time(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")

    status = approve(scenario, target="HTTP://LocalHost:3000/api/")

    assert status == 0
    output = json.loads(capsys.readouterr().out)
    records = json.loads((workspace / ".litetraffic" / "approvals.json").read_text())["approvals"]
    assert len(records) == 1
    record = records[0]
    assert record["digest"] == load_scenario(scenario).digest
    assert record["profile"] == "local-test"
    assert record["target_origin"] == "http://localhost:3000"
    assert record["approved_at"].endswith("Z")
    assert output["ok"] is True and output["digest"] == record["digest"]


def test_reapproving_the_same_binding_keeps_one_record(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")
    approve(scenario)
    approve(scenario)
    approve(scenario, profile="staging", target="https://staging.example.test")

    records = json.loads((workspace / ".litetraffic" / "approvals.json").read_text())["approvals"]
    assert [(r["profile"], r["target_origin"]) for r in records] == [
        ("local-test", "http://localhost:3000"),
        ("staging", "https://staging.example.test"),
    ]


def test_verify_require_approval_exits_3_without_an_approval(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")

    status = verify(scenario, "--require-approval")

    assert status == 3
    assert "not approved" in json.loads(capsys.readouterr().out)["error"]


def test_verify_require_approval_runs_when_digest_and_origin_match(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")
    approve(scenario)
    capsys.readouterr()

    assert verify(scenario, "--require-approval", target="http://localhost:3000/") == 0


def test_verify_require_approval_rejects_another_origin(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")
    approve(scenario)
    capsys.readouterr()

    assert verify(scenario, "--require-approval", target="http://localhost:4000") == 3


def test_editing_the_script_after_approval_invalidates_it(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")
    approve(scenario)
    (scenario / "journeys.js").write_text("export default function () { /* changed */ }\n")
    capsys.readouterr()

    status = verify(scenario, "--require-approval")

    assert status == 3
    assert "not approved" in json.loads(capsys.readouterr().out)["error"]


def test_approved_digest_is_accepted_when_it_matches(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")
    digest = load_scenario(scenario).digest

    assert verify(scenario, "--require-approval", "--approved-digest", digest) == 0
    assert not (workspace / ".litetraffic" / "approvals.json").exists()


def test_approved_digest_mismatch_exits_3(workspace, capsys):
    scenario = write_bundle(workspace / "traffic")

    status = verify(scenario, "--approved-digest", "0" * 64)

    assert status == 3
    assert "does not match" in json.loads(capsys.readouterr().out)["error"]


def test_verify_without_approval_flags_does_not_check(workspace, capsys):
    assert verify(write_bundle(workspace / "traffic")) == 0


@pytest.mark.parametrize("target", ["ftp://host", "http://user:pw@host", "localhost:3000"])
def test_origin_rejects_non_http_targets(target):
    with pytest.raises(ValueError):
        origin(target)


def test_origin_drops_default_ports():
    assert origin("https://Example.test:443/x") == "https://example.test"
    assert origin("http://[::1]:8080") == "http://[::1]:8080"


MALFORMED_APPROVALS = [
    "not json",
    "[]",
    '{"other": []}',
    '{"approvals": {"a": 1}}',
    '{"approvals": [1]}',
    '{"approvals": [{"x": 1}]}',
    '{"approvals": [{"digest": 1, "profile": "p", "target_origin": "http://localhost:3000"}]}',
]


@pytest.mark.parametrize("content", MALFORMED_APPROVALS)
@pytest.mark.parametrize("command", ["verify", "approve"])
def test_malformed_approvals_file_exits_3(workspace, capsys, content, command):
    scenario = write_bundle(workspace / "traffic")
    (workspace / ".litetraffic").mkdir()
    (workspace / ".litetraffic" / "approvals.json").write_text(content)

    status = verify(scenario, "--require-approval") if command == "verify" else approve(scenario)

    assert status == 3
    assert "approvals file" in json.loads(capsys.readouterr().out)["error"]
