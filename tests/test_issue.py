"""`litetraffic report issue`: drafts, routing, redaction and filing, against a fake `gh` only."""

import hashlib
import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from litetraffic import issue, proof
from litetraffic.artifacts import artifact_files
from litetraffic.cli import main
from litetraffic.kits import tenant_isolation

DATA = Path(__file__).parent / "data" / "issue"
FAIL_ID = "run_20261017T105826Z_54eaa342"
PASS_ID = "run_20261017T105857Z_4508abfb"
SCENARIO = DATA / "hievents_scenario"
ORIGIN = "http://127.0.0.1:8081"
SECRET = "supersecretvalue123"
REAL_AVAILABLE = proof.available

FAKE_GH = """#!{python}
import json, os, sys
from pathlib import Path
here = Path(__file__).resolve().parent.parent
argv = sys.argv[1:]
record = {{"argv": argv, "gh_repo": os.environ.get("GH_REPO"), "cwd": os.getcwd(), "files": {{}}}}
for flag in ("-F", "--input"):
    if flag in argv:
        path = Path(argv[argv.index(flag) + 1])
        record["files"][flag] = (path if path.is_absolute() else Path.cwd() / path).read_text()
with open(here / "calls.jsonl", "a") as log:
    log.write(json.dumps(record) + "\\n")
for rule in json.loads((here / "gh_rules.json").read_text()):
    if all(token in argv for token in rule["match"]):
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))
sys.stderr.write("fake gh: no rule for %r\\n" % argv)
sys.exit(1)
"""

CREATED = "https://github.com/owner/app/issues/7"
ADVISORY = "https://github.com/owner/app/security/advisories/GHSA-abcd-efgh-ijkl"


def default_rules() -> list[dict]:
    return [
        {"match": ["auth", "status"]},
        {"match": ["issue", "create", "--help"], "stdout": "      --attach file   Attach an image or video file\n"},
        {"match": ["search", "issues"], "stdout": "[]"},
        {"match": ["api", "user"], "stdout": "tester\n"},
        {"match": ["api", "repos/owner/app/issues"], "stdout": ""},
        {"match": ["repo", "view"], "stdout": "WRITE\n"},
        {"match": ["issue", "create"], "stdout": CREATED + "\n"},
        {"match": ["issue", "comment"], "stdout": "https://github.com/owner/app/issues/3#issuecomment-1\n"},
        {"match": ["issue", "view"], "stdout": "OPEN\n"},
        {"match": ["issue", "edit"], "stdout": CREATED + "\n"},
        {"match": ["api", "repos/owner/app/private-vulnerability-reporting"], "stdout": "true\n"},
        {"match": ["api", "repos/owner/app/security-advisories/reports"], "stdout": json.dumps({"html_url": ADVISORY})},
    ]


class FakeGh:
    def __init__(self, root: Path) -> None:
        self.root, self.rules = root, default_rules()
        (root / "bin").mkdir()
        script = root / "bin" / "gh"
        script.write_text(FAKE_GH.format(python=sys.executable))
        script.chmod(0o755)
        self.save()

    def save(self) -> None:
        (self.root / "gh_rules.json").write_text(json.dumps(self.rules))

    def respond(self, match: list[str], **reply) -> None:
        self.rules.insert(0, {"match": match, **reply})
        self.save()

    def calls(self) -> list[dict]:
        log = self.root / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.calls()]


FIXTURE_TOKEN = "lt-fixture-org-token-0000"


@pytest.fixture(autouse=True)
def gh(tmp_path, monkeypatch):
    """Every test runs with a fake gh first on PATH; the real one must never run."""
    root = tmp_path / "ghroot"
    root.mkdir()
    fake = FakeGh(root)
    monkeypatch.setenv("PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "ghp_fakefakefakefakefakefakefakefake00")
    for name in ("GH_REPO", "GITHUB_TOKEN", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    # the fixture scenario declares this credential; a report only files once every declared one is set (and so scrubbed)
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", FIXTURE_TOKEN)
    for name in ("GITEA_TOKEN_ALICE", "GITEA_TOKEN_BOB"):  # the kit fixture's identities
        monkeypatch.setenv(name, f"lt-fixture-{name.lower()}-0000")
    monkeypatch.setattr(proof, "available", lambda: None)  # video tests opt back in
    assert shutil.which("gh") == str(root / "bin" / "gh")
    return fake


def reseal(run_dir: Path) -> None:
    files = artifact_files(run_dir)
    data = {"files": files, "run_id": run_dir.name, "schema_version": 1, "total_bytes": sum(f["bytes"] for f in files)}
    (run_dir / "artifacts.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def reseal_scenario_script(run_dir: Path, scenario: Path, extra: str) -> None:
    """Append to the scenario's script and record its new digest in the run's lock, as if the run had used it."""
    script = scenario / "journeys.js"
    script.write_text(script.read_text() + extra)
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    edit_json(run_dir, "scenario.lock.json", lambda lock: lock["files"].update({"journeys.js": digest}))


def edit_json(run_dir: Path, name: str, change) -> None:
    path = run_dir / name
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    reseal(run_dir)


@pytest.fixture
def runs(tmp_path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture
def fail_run(runs) -> Path:
    return Path(shutil.copytree(DATA / "fail_run" / FAIL_ID, runs / FAIL_ID))


@pytest.fixture
def pass_run(runs) -> Path:
    return Path(shutil.copytree(DATA / "pass_run" / PASS_ID, runs / PASS_ID))


KIT = {
    "name": "gitea-isolation",
    "identities": [
        {"name": "alice", "token_env": "GITEA_TOKEN_ALICE", "resources": [{"owner": "alice", "repo": "r"}], "markers": ["alice-private-marker-1"]},
        {"name": "bob", "token_env": "GITEA_TOKEN_BOB", "resources": [{"owner": "bob", "repo": "s"}], "markers": ["bob-private-marker-2"]},
    ],
    "endpoints": [{"method": "GET", "path": "/api/v1/repos/{owner}/{repo}", "kind": "read"}],
}
KIT_ID = "run_20261017T120000Z_0000c1d2"
KIT_SAMPLE = {
    "identity": "bob", "method": "GET", "path": "/api/v1/repos/{owner}/{repo}", "id": {"owner": "alice", "repo": "r"},
    "status": 200, "markers": {"identity": "alice", "indexes": [0]},
}


def write_kit_run(runs: Path, tmp_path: Path, failing: dict[str, list[dict]] | None = None) -> tuple[Path, Path]:
    """A synthetic tenant-isolation run whose failures default to bob reading alice's repo with alice's marker."""
    config = tmp_path / "kit.json"
    config.write_text(json.dumps(KIT))
    scenario = tmp_path / "kit_scenario"
    bundle, _ = tenant_isolation.generate(config, scenario)
    failing = {"cross_tenant_read_blocked": [KIT_SAMPLE]} if failing is None else failing
    rows = []
    for name in bundle.manifest.assertions:
        if name in failing:
            entry = {"logical_key": f"{KIT_ID}-traffic-0", "sequence": 3, "detail": None,
                     "expected": {"statuses": [401, 403, 404], "body": "none of the owner's markers"},
                     "actual": {"failures": len(failing[name]), "samples": failing[name]}}
            rows.append({"id": name, "samples": 10, "status": "fail", "failed": 1, "failures": [entry]})
        else:
            rows.append({"id": name, "samples": 10, "status": "pass"})
    run_dir = runs / KIT_ID
    (run_dir / "events").mkdir(parents=True)
    run = {"run_id": KIT_ID, "scenario": bundle.manifest.name, "scenario_sha256": bundle.digest, "seed": 7, "schema_version": 1,
           "engine": "k6 v2.2.0 (commit/x, go1.26.5, linux/amd64)", "target": "http://127.0.0.1:3000", "mode": "verify",
           "lifecycle": "finished", "started_at": "2026-10-17T12:00:00+00:00", "finished_at": "2026-10-17T12:00:09+00:00",
           "resolved_schedule": [{"name": "measure", "rate": 2, "seconds": 4}]}
    result = {"run_id": KIT_ID, "verdict": "fail", "completeness": "complete", "limitations": [], "lifecycle": "finished",
              "assertions": rows, "planned_journeys": 10, "planned_journeys_per_second": 2.0, "seed": 7, "schema_version": 1,
              "metrics": {"iterations": 10.0, "http_reqs": 40.0, "overlap": {"cross_tenant": 2, "own": 2},
                          "by_operation": {"cross_tenant": {"failed_rate": 0.0, "p95": 9.1, "samples": 20}}}}
    (run_dir / "run.json").write_text(json.dumps(run, indent=2, sort_keys=True))
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    (run_dir / "scenario.lock.json").write_text(json.dumps({"manifest": bundle.manifest_data, "engine": run["engine"], "files": bundle.files}))
    (run_dir / "events" / "000001.jsonl").write_text(json.dumps({"type": "assertion", "assertion": "cross_tenant_read_blocked", "passed": False}) + "\n")
    point = {"metric": "http_reqs", "type": "Point", "data": {"time": "2026-10-17T12:00:01Z", "value": 1, "tags": {
        "method": "GET", "operation": "cross_tenant", "status": "200", "url": "http://127.0.0.1:3000/api/v1/repos/alice/r"}}}
    (run_dir / "metrics.jsonl").write_text(json.dumps(point) + "\n")
    reseal(run_dir)
    return run_dir, scenario


def report(*args: str) -> int:
    return main(["report", "issue", *args])


def bundle_text(run_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(run_dir / "issue")): path.read_text(errors="replace")
        for path in sorted((run_dir / "issue").rglob("*"))
        if path.is_file() and path.suffix not in {".mp4", ".gif"}
    }


def draft_json(capsys, *args: str) -> dict:
    assert report(*args, "--json") == 0, capsys.readouterr()
    return json.loads(capsys.readouterr().out)


# --- gate and classification -------------------------------------------------------------------


def test_pass_run_exits_zero_with_message_and_writes_nothing(pass_run, runs, capsys, gh):
    assert report(PASS_ID, "--runs-dir", str(runs)) == 0
    assert "passed; nothing to report" in capsys.readouterr().out
    assert not (pass_run / "issue").exists()
    assert gh.calls() == []


@pytest.mark.parametrize("verdict", ["inconclusive", "error"])
def test_inconclusive_and_error_runs_refuse_exit_3(fail_run, runs, capsys, verdict):
    edit_json(fail_run, "result.json", lambda r: r.update(verdict=verdict))
    assert report(str(fail_run)) == 3
    assert "missing evidence is not a bug report" in capsys.readouterr().out
    assert not (fail_run / "issue").exists()


def test_tampered_result_json_refuses(fail_run, capsys):
    data = json.loads((fail_run / "result.json").read_text())
    data["assertions"][2]["actual"]["/data/0/order_usage_count"] = 99
    (fail_run / "result.json").write_text(json.dumps(data))
    assert report(str(fail_run)) == 3
    assert "run artifacts changed after the run" in capsys.readouterr().out


def test_missing_artifacts_manifest_refuses(fail_run, capsys):
    (fail_run / "artifacts.json").unlink()
    assert report(str(fail_run)) == 3
    assert "run artifacts changed after the run" in capsys.readouterr().out


def test_hievents_fail_classifies_correctness(fail_run, capsys):
    result = draft_json(capsys, str(fail_run))
    assert result["kind"] == "correctness"
    assert (fail_run / "issue" / "issue.md").exists()
    assert not (fail_run / "issue" / "security.md").exists()


def test_kit_failure_classifies_security(runs, tmp_path):
    run_dir, _ = write_kit_run(runs, tmp_path)
    evidence = issue.load_run(str(run_dir), runs)
    classification = issue.classify(evidence, force_security=False)
    assert classification.kind == "security"
    assert any("cross_tenant_read_blocked" in reason for reason in classification.reasons)
    assert any("tenant-isolation-kit" in reason for reason in classification.reasons)


def test_security_id_regex_fails_closed(fail_run, runs):
    edit_json(fail_run, "result.json", lambda r: r["assertions"][2].update(id="tenant_b_balance_read_blocked"))
    classification = issue.classify(issue.load_run(str(fail_run), runs), force_security=False)
    assert classification.kind == "security"


def test_security_flag_upgrades_never_downgrades(fail_run, runs, tmp_path):
    assert issue.classify(issue.load_run(str(fail_run), runs), force_security=True).kind == "security"
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert issue.classify(issue.load_run(str(run_dir), runs), force_security=False).kind == "security"


def test_own_access_only_failure_is_correctness(runs, tmp_path):
    sample = {"identity": "alice", "method": "GET", "path": "/api/v1/repos/{owner}/{repo}", "id": {"owner": "alice", "repo": "r"}, "status": 500}
    run_dir, _ = write_kit_run(runs, tmp_path, {"own_access": [sample]})
    assert issue.classify(issue.load_run(str(run_dir), runs), force_security=False).kind == "correctness"


def test_marker_in_failing_sample_is_security(runs, tmp_path):
    sample = dict(KIT_SAMPLE, identity="alice")
    run_dir, _ = write_kit_run(runs, tmp_path, {"own_access": [sample]})
    assert issue.classify(issue.load_run(str(run_dir), runs), force_security=False).kind == "security"


# --- bundle content ----------------------------------------------------------------------------


def test_draft_writes_issue_md_repro_evidence_and_sends_nothing(fail_run, capsys, gh):
    assert report(str(fail_run)) == 0
    out = capsys.readouterr().out
    bundle = fail_run / "issue"
    for name in ("issue.md", "check-effect.sh", "evidence.json"):  # no scenario given: the script can only check the effect
        assert (bundle / name).is_file(), name
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    assert stat.S_IMODE((bundle / "issue.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((bundle / "evidence.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((bundle / "check-effect.sh").stat().st_mode) == 0o700
    assert gh.calls() == []
    assert "nothing was sent" in out
    assert "would run: gh issue create -R OWNER/NAME" in out
    assert "video: unavailable" in out and "litetraffic[video]" in out


def test_draft_does_not_change_artifacts_json(fail_run, capsys):
    before = (fail_run / "artifacts.json").read_bytes()
    assert report(str(fail_run)) == 0
    assert (fail_run / "artifacts.json").read_bytes() == before
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    expected = hashlib.sha256((fail_run / "result.json").read_bytes()).hexdigest()
    assert evidence["source_sha256"]["result.json"] == expected


def test_issue_md_sections_and_fingerprint(fail_run, capsys):
    result = draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    fingerprint = result["fingerprint"]
    assert fingerprint.startswith("lt") and len(fingerprint) == 14
    from litetraffic.github import carries_fingerprint

    assert carries_fingerprint(body, fingerprint) and body.count(fingerprint) == 1  # one searchable line, no HTML comment
    summary = body.split("## Summary", 1)[1].split("\n## ", 1)[0]
    assert "order_usage_count" in summary and "10" in summary and "≤ 1" in summary
    assert "| Check | Expected | Actual |" in body
    environment = body.split("## Environment", 1)[1].split("\n## ", 1)[0]
    assert "0.1.0.dev0" in environment and "42" in environment
    assert "5ee64a14f5063505e67beb4a1c811f1773a6a5debf005806a5b5e8f706a25af0" in environment
    assert "## What this does and does not establish" in body
    assert "does not know the root cause" in body
    assert "found the root cause" not in body
    assert "check-effect.sh" in body  # no scenario given, so the script only checks the effect and is named for it
    assert result["title"].startswith("hievents-promo-oversell: promo_usage_within_limit failed")
    assert len(result["title"]) <= 100


def test_fingerprint_stable_and_sensitive(fail_run, runs, capsys):
    evidence = issue.load_run(str(fail_run), runs)
    first = issue.fingerprint(evidence)
    assert first == issue.fingerprint(issue.load_run(str(fail_run), runs))
    assert issue.origin_hash("http://localhost:80") == issue.origin_hash("HTTP://LOCALHOST")
    assert issue.origin_hash("https://Example.com:443/") == issue.origin_hash("https://example.com")
    assert issue.origin_hash("http://localhost:8080") != issue.origin_hash("http://localhost")
    edit_json(fail_run, "run.json", lambda r: r.update(target="http://127.0.0.1:9999"))
    assert issue.fingerprint(issue.load_run(str(fail_run), runs)) != first
    edit_json(fail_run, "run.json", lambda r: r.update(target=ORIGIN, scenario="other"))
    assert issue.fingerprint(issue.load_run(str(fail_run), runs)) != first
    edit_json(fail_run, "run.json", lambda r: r.update(scenario="hievents-promo-oversell"))
    assert issue.fingerprint(issue.load_run(str(fail_run), runs)) == first
    edit_json(fail_run, "result.json", lambda r: r["assertions"][0].update(status="fail"))
    assert issue.fingerprint(issue.load_run(str(fail_run), runs)) != first


def test_security_bundle_writes_security_md_not_issue_md(runs, tmp_path, capsys):
    run_dir, scenario = write_kit_run(runs, tmp_path)
    result = draft_json(capsys, str(run_dir), "--scenario", str(scenario))
    bundle = run_dir / "issue"
    assert result["kind"] == "security"
    assert (bundle / "security.md").is_file() and not (bundle / "issue.md").exists()
    payload = json.loads((bundle / "report.json").read_text())
    assert payload["summary"] and payload["description"] == (bundle / "security.md").read_text()
    assert "CWE-639" in payload["cwe_ids"]
    assert "Private vulnerability report — do not post publicly" in payload["description"]
    for text in bundle_text(run_dir).values():
        assert "alice-private-marker-1" not in text and "bob-private-marker-2" not in text


def test_submit_blockers_listed_in_draft_for_429_failures(fail_run, capsys):
    def throttle(result):
        result["assertions"][0] = {"id": "reservation_accepted", "samples": 10, "status": "fail", "failed": 4, "failures": [
            {"sequence": 7, "logical_key": "k-3", "expected": {"status": 201}, "actual": {"status": 429, "short_id": None}, "detail": None}]}

    edit_json(fail_run, "result.json", throttle)
    assert report(str(fail_run)) == 0
    body = (fail_run / "issue" / "issue.md").read_text()
    assert "## Caveats" in body and "rate-limiting or overloaded" in body


# --- redaction ---------------------------------------------------------------------------------


def plant(fail_run: Path, text: str) -> None:
    def put(result):
        result["assertions"][0] = {"id": "reservation_accepted", "samples": 10, "status": "fail", "failed": 1, "failures": [
            {"sequence": 2, "logical_key": "k-0", "expected": {"status": 201}, "actual": {"status": 500}, "detail": text}]}

    edit_json(fail_run, "result.json", put)


def test_declared_secret_in_env_is_scrubbed_everywhere(fail_run, monkeypatch, capsys):
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", SECRET)
    plant(fail_run, f"server echoed Bearer {SECRET} back")
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    out = capsys.readouterr().out
    files = bundle_text(fail_run)
    assert all(SECRET not in text for text in files.values())
    assert SECRET not in out
    assert "[redacted]" in files["issue.md"] and "[redacted]" in files["evidence.json"]


def test_jwt_and_gh_tokens_scrubbed(fail_run, capsys):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2lnbmF0dXJlLXZhbHVl"
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    plant(fail_run, f"jwt {jwt} and {token} and github_pat_{'x' * 30}")
    assert report(str(fail_run)) == 0
    for text in bundle_text(fail_run).values():
        assert jwt not in text and token not in text and "github_pat_" not in text


def test_target_origin_and_home_paths_replaced(fail_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    plant(fail_run, f"see {fail_run}/console.log and {ORIGIN}/api and http%3A%2F%2F127.0.0.1%3A8081")
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    for name, text in bundle_text(fail_run).items():
        assert "127.0.0.1:8081" not in text and "127.0.0.1%3A8081" not in text, name
        assert str(tmp_path) not in text, name
    assert "$TARGET" in bundle_text(fail_run)["issue.md"]


def test_kit_markers_replaced_in_security_bundle(runs, tmp_path, capsys):
    run_dir, scenario = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--scenario", str(scenario)) == 0
    files = bundle_text(run_dir)
    assert "scenario/journeys.js" in files
    assert "[marker:alice#0]" in files["scenario/journeys.js"]
    for text in files.values():
        assert "alice-private-marker-1" not in text


def test_redaction_verify_failure_deletes_bundle_exit_3(fail_run, monkeypatch, capsys):
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", SECRET)
    plant(fail_run, f"leaked {SECRET}")
    monkeypatch.setattr(issue.Scrubber, "text", lambda self, value: value)
    assert report(str(fail_run)) == 3
    assert "redaction check failed" in capsys.readouterr().out
    assert not (fail_run / "issue").exists()


def test_repro_reads_credentials_from_env_never_inline(fail_run, monkeypatch, capsys):
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", SECRET)
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    script = script_of(fail_run).read_text()
    assert "${LT_HIEVENTS_ORG_TOKEN}" in script
    # the guards exit 2 (could not check), not the 1 a `:?` guard gives under set -e
    assert '[ -n "${LT_HIEVENTS_ORG_TOKEN:-}" ] || {' in script and '[ -n "${TARGET:-}" ] || {' in script
    assert SECRET not in script


# --- repro -------------------------------------------------------------------------------------


def script_of(run_dir: Path) -> Path:
    """The bundle's script: repro.sh when it replays the cause, check-effect.sh when it can only check the end state."""
    name = json.loads((run_dir / "issue" / "evidence.json").read_text())["script"]["file"]
    return run_dir / "issue" / name


def test_effect_only_run_writes_check_effect_sh_not_repro_sh(fail_run, capsys):
    result = draft_json(capsys, str(fail_run))
    assert "check-effect.sh" in result["files"] and "repro.sh" not in result["files"]
    script = (fail_run / "issue" / "check-effect.sh").read_text()
    assert "It does NOT reproduce the cause" in script
    assert "REPRODUCED" not in script and "BAD STATE PRESENT" in script
    doc = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert doc["script"] == {"file": "check-effect.sh", "replays_cause": False, "source": "effect check only"}


def test_author_repro_keeps_the_repro_sh_name(fail_run, capsys):
    result = draft_json(capsys, str(fail_run), "--scenario", str(SCENARIO))
    assert "repro.sh" in result["files"] and "check-effect.sh" not in result["files"]
    doc = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert doc["script"] == {"file": "repro.sh", "replays_cause": True, "source": "scenario author"}
    assert "REPRODUCED" in script_of(fail_run).read_text()


def test_kit_scenario_replay_is_a_repro(runs, tmp_path, capsys):
    run_dir, scenario = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--scenario", str(scenario)) == 0
    doc = json.loads((run_dir / "issue" / "evidence.json").read_text())
    assert doc["script"]["file"] == "repro.sh" and doc["script"]["replays_cause"] is True


def test_issue_md_is_honest_about_an_effect_only_script(fail_run, capsys):
    draft_json(capsys, str(fail_run))
    steps = section((fail_run / "issue" / "issue.md").read_text(), "Steps to reproduce")
    assert "check-effect.sh" in steps and "repro.sh" not in steps
    assert "does not reproduce the cause" in steps
    assert "Exit 1 means the bad state is present" in steps and "the failure reproduced" not in steps
    # plain-language steps from the scenario: each journey's operations in order, with the stage they lead to
    assert "`buy`" in steps
    assert steps.index("`reserve_order`") < steps.index("`complete_order`")
    assert "POST $TARGET/api/public/events/{id}/order" in steps and "`reserved`" in steps and "`completed`" in steps
    assert "10 journeys" in steps


def test_issue_md_with_author_repro_says_it_replays(fail_run, capsys):
    draft_json(capsys, str(fail_run), "--scenario", str(SCENARIO))
    steps = section((fail_run / "issue" / "issue.md").read_text(), "Steps to reproduce")
    assert "repro.sh" in steps and "Exit 1 means the failure reproduced" in steps
    assert "does not reproduce the cause" not in steps


def test_repro_is_valid_bash(fail_run, runs, tmp_path, capsys):
    kit_run, scenario = write_kit_run(runs, tmp_path)
    assert report(str(fail_run)) == 0
    assert report(str(kit_run), "--scenario", str(scenario)) == 0
    shutil.rmtree(fail_run / "issue")
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    for run_dir in (fail_run, kit_run):
        script = script_of(run_dir)
        assert script.read_text().startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
        checked = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr


class _Canned(http.server.BaseHTTPRequestHandler):
    body = b""
    seen: list = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize(("count", "code"), [(10, 1), (1, 0)])
def test_repro_effect_check_detects_violation(fail_run, capsys, count, code):
    assert report(str(fail_run)) == 0
    _Canned.body = json.dumps({"data": [{"max_allowed_usages": 1, "order_usage_count": count}]}).encode()
    _Canned.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = dict(os.environ, TARGET=f"http://127.0.0.1:{server.server_port}", LT_HIEVENTS_ORG_TOKEN="local-test-token",
                   REPRO_DEADLINE_SECONDS="0")
        done = subprocess.run(["bash", str(script_of(fail_run))], capture_output=True, text=True, env=env, timeout=60)
    finally:
        server.shutdown()
    assert done.returncode == code, done.stdout + done.stderr
    assert ("VIOLATED" in done.stdout) == (code == 1)
    assert _Canned.seen[0] == ("/api/events/1/promo-codes", "Bearer local-test-token")


def test_repro_kit_emits_curl_per_failing_sample(runs, tmp_path, capsys):
    run_dir, scenario = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--scenario", str(scenario)) == 0
    script = script_of(run_dir).read_text()
    assert '"$TARGET/api/v1/repos/alice/r"' in script
    assert "Authorization: Bearer ${GITEA_TOKEN_BOB}" in script
    assert "401 403 404" in script
    assert "LT_MARKERS_ALICE" in script
    assert "alice-private-marker-1" not in script


def test_kit_run_without_scenario_still_drafts_and_says_why(runs, tmp_path, capsys):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir)) == 0
    script = script_of(run_dir).read_text()
    assert "--scenario" in script


def test_repro_without_scenario_has_honest_timeline_note(fail_run, capsys):
    assert report(str(fail_run)) == 0
    script = script_of(fail_run).read_text()
    assert "LiteTraffic does not record request bodies" in script
    assert "reserve_order" in script and "POST" in script and "/api/public/events/{id}/order" in script
    assert "19d90ac2d7b16360ab7b16e3e366acc36384b54df2d9ec9ca02fc59a2c909ec1" in script


def test_scenario_sha_mismatch_refuses(fail_run, tmp_path, capsys):
    scenario = Path(shutil.copytree(SCENARIO, tmp_path / "scenario"))
    (scenario / "journeys.js").write_text((scenario / "journeys.js").read_text() + "\n// edited\n")
    assert report(str(fail_run), "--scenario", str(scenario)) == 3
    assert "does not match the run's scenario.lock.json" in capsys.readouterr().out
    assert not (fail_run / "issue").exists()


def test_author_repro_included_from_scenario_dir(fail_run, capsys):
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    script = script_of(fail_run).read_text()
    digest = hashlib.sha256((SCENARIO / "repro.sh").read_bytes()).hexdigest()
    assert f"# --- provided by the scenario author (sha256 {digest}) ---" in script
    assert "max_allowed_usages\\\":1" in script
    assert "LiteTraffic does not record request bodies" not in script
    assert (fail_run / "issue" / "scenario" / "journeys.js").is_file()


# --- submit: public route ----------------------------------------------------------------------


def test_submit_requires_repo(fail_run, capsys, gh):
    assert report(str(fail_run), "--submit", "--public") == 3
    assert "--submit needs --repo" in capsys.readouterr().out
    assert gh.calls() == []


@pytest.mark.parametrize("repo", ["owner", "owner/app/extra", "-x/app", "owner/app;rm", "https://github.com/owner/app"])
def test_submit_rejects_malformed_repo(fail_run, capsys, gh, repo):
    assert report(str(fail_run), "--submit", "--public", "--repo", repo) == 3
    assert "--repo must be OWNER/NAME" in capsys.readouterr().out
    assert gh.calls() == []


def test_gh_missing_clear_error(fail_run, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    assert "Install GitHub CLI" in capsys.readouterr().out


def test_gh_unauthenticated_clear_error(fail_run, capsys, gh):
    gh.respond(["auth", "status"], exit=1, stderr="You are not logged into any GitHub hosts.")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    assert "gh is not authenticated" in capsys.readouterr().out
    assert not any("create" in argv for argv in gh.argvs())


def test_submit_creates_issue_with_body_file_and_repo_flag(fail_run, runs, capsys, gh, monkeypatch):
    monkeypatch.setenv("GH_REPO", "evil/elsewhere")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    out = capsys.readouterr().out
    create = next(call for call in gh.calls() if call["argv"][:2] == ["issue", "create"] and "--help" not in call["argv"])
    argv = create["argv"]
    assert argv[argv.index("-R") + 1] == "owner/app"
    assert argv[argv.index("-t") + 1].startswith("hievents-promo-oversell:")
    assert argv[argv.index("-F") + 1].endswith("issue.md")
    assert "litetraffic-fingerprint" in create["files"]["-F"]
    assert all(call["gh_repo"] is None for call in gh.calls())
    assert CREATED in out
    filed = json.loads((fail_run / "issue" / "filed.json").read_text())
    assert filed["url"] == CREATED and filed["repo"] == "owner/app" and filed["kind"] == "correctness"
    ledger = [json.loads(line) for line in (runs / "issues.jsonl").read_text().splitlines()]
    assert ledger[0]["url"] == CREATED and ledger[0]["fingerprint"] == filed["fingerprint"]


def test_duplicate_open_issue_refuses_and_prints_url(fail_run, runs, capsys, gh):
    fingerprint = issue.fingerprint(issue.load_run(str(fail_run), runs))
    existing = [{"number": 3, "url": "https://github.com/owner/app/issues/3", "body": f"x\nlitetraffic-fingerprint: {fingerprint}"}]
    gh.respond(["search", "issues"], stdout=json.dumps(existing))
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    out = capsys.readouterr().out
    assert "already reported: https://github.com/owner/app/issues/3" in out and "--comment" in out
    assert not any(argv[:2] == ["issue", "create"] and "--help" not in argv for argv in gh.argvs())
    search = next(argv for argv in gh.argvs() if argv[:2] == ["search", "issues"])
    assert fingerprint in search and "owner/app" in search


def test_duplicate_found_via_rest_list_when_search_lags(fail_run, runs, capsys, gh):
    fingerprint = issue.fingerprint(issue.load_run(str(fail_run), runs))
    line = json.dumps({"number": 5, "url": "https://github.com/owner/app/issues/5", "body": f"litetraffic-fingerprint: {fingerprint}"})
    gh.respond(["api", "repos/owner/app/issues"], stdout=line + "\n")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    assert "already reported: https://github.com/owner/app/issues/5" in capsys.readouterr().out
    listing = next(argv for argv in gh.argvs() if "repos/owner/app/issues" in argv)
    assert "creator=tester" in listing


def test_duplicate_with_comment_adds_comment(fail_run, runs, capsys, gh):
    fingerprint = issue.fingerprint(issue.load_run(str(fail_run), runs))
    existing = [{"number": 3, "url": "https://github.com/owner/app/issues/3", "body": f"litetraffic-fingerprint: {fingerprint}"}]
    gh.respond(["search", "issues"], stdout=json.dumps(existing))
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--comment") == 0
    comment = next(call for call in gh.calls() if call["argv"][:2] == ["issue", "comment"])
    assert comment["argv"][2] == "https://github.com/owner/app/issues/3"
    assert comment["argv"][comment["argv"].index("-F") + 1].endswith("comment.md")
    assert FAIL_ID in comment["files"]["-F"]
    assert not any(argv[:2] == ["issue", "create"] and "--help" not in argv for argv in gh.argvs())


def test_ledger_blocks_refile(fail_run, runs, capsys, gh):
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    capsys.readouterr()
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    assert f"already reported: {CREATED}" in capsys.readouterr().out
    creates = [argv for argv in gh.argvs() if argv[:2] == ["issue", "create"] and "--help" not in argv]
    assert len(creates) == 1


def fake_video(monkeypatch) -> None:
    def render(evidence, out_dir):
        path = Path(out_dir) / "proof.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
        return [path]

    monkeypatch.setattr(proof, "available", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(proof, "render", render)


def _create_argv(gh) -> list[str]:
    return next(argv for argv in gh.argvs() if argv[:2] == ["issue", "create"] and "--help" not in argv)


@pytest.mark.parametrize("variant", ["read", "old-gh", "installation-token"])
def test_attach_not_used_without_write_permission_or_support(fail_run, capsys, gh, monkeypatch, variant):
    fake_video(monkeypatch)
    if variant == "read":
        gh.respond(["repo", "view"], stdout="READ\n")
    elif variant == "old-gh":
        gh.respond(["issue", "create", "--help"], stdout="  -F, --body-file file\n")
    else:
        monkeypatch.setenv("GH_TOKEN", "ghs_installationtokeninstallationtoken")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    out = capsys.readouterr().out
    assert "--attach" not in _create_argv(gh)
    assert f"drag {fail_run / 'issue' / 'proof.mp4'} into {CREATED}" in out


def test_attach_used_with_write_permission_and_supported_gh(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    create = next(call for call in gh.calls() if call["argv"][:2] == ["issue", "create"] and "--help" not in call["argv"])
    assert create["argv"][create["argv"].index("--attach") + 1] == "./proof.mp4"
    assert create["cwd"] == str((fail_run / "issue").resolve())
    assert "![LiteTraffic replay](./proof.mp4)" in create["files"]["-F"]
    assert "drag" not in capsys.readouterr().out


def test_partial_attach_failure_reports_url_and_drag_hint_exit_0(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["issue", "create", "-R"], stdout=CREATED + "\n", stderr="failed to upload proof.mp4: 422", exit=1)
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    out = capsys.readouterr().out
    assert CREATED in out and "failed to upload proof.mp4" in out and "drag " in out
    assert (fail_run / "issue" / "filed.json").exists()


def test_attach_url_flag_and_actions_autodetect_in_body(fail_run, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/app")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    assert report(str(fail_run)) == 0
    body = (fail_run / "issue" / "issue.md").read_text()
    assert "https://github.com/owner/app/actions/runs/12345" in body
    assert "requires GitHub sign-in" in body
    shutil.rmtree(fail_run / "issue")
    assert report(str(fail_run), "--attach-url", "https://ci.example.test/artifacts/9") == 0
    body = (fail_run / "issue" / "issue.md").read_text()
    assert "https://ci.example.test/artifacts/9" in body and "actions/runs/12345" not in body


def test_labels_are_passed_through(fail_run, capsys, gh):
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--label", "bug", "--label", "promo") == 0
    argv = _create_argv(gh)
    assert [argv[i + 1] for i, token in enumerate(argv) if token == "-l"] == ["bug", "promo"]


# --- submit: security route --------------------------------------------------------------------


def test_security_submit_posts_private_report_only(runs, tmp_path, capsys, gh):
    run_dir, scenario = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--scenario", str(scenario), "--submit", "--repo", "owner/app") == 0
    assert ADVISORY in capsys.readouterr().out
    argvs = gh.argvs()
    post = next(argv for argv in argvs if "repos/owner/app/security-advisories/reports" in argv)
    assert post[:3] == ["api", "-X", "POST"] and post[post.index("--input") + 1].endswith("report.json")
    assert not any(argv[:2] == ["issue", "create"] for argv in argvs)
    assert not any("--attach" in argv for argv in argvs)
    assert not any(argv[:2] == ["search", "issues"] for argv in argvs)


def test_security_public_flag_refuses_exit_3(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--submit", "--repo", "owner/app", "--public") == 3
    assert "never filed publicly" in capsys.readouterr().out
    assert gh.calls() == []


def test_security_pvr_disabled_refuses_with_instructions(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    gh.respond(["api", "repos/owner/app/private-vulnerability-reporting"], stdout="false\n")
    assert report(str(run_dir), "--submit", "--repo", "owner/app") == 3
    out = capsys.readouterr().out
    assert "security/advisories/new" in out and "SECURITY.md" in out and "Do not open a public issue" in out
    assert not any("POST" in argv for argv in gh.argvs())


def test_security_api_error_refuses(runs, tmp_path, capsys, gh, monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN_BOB", "bob-token-value-123456")
    run_dir, _ = write_kit_run(runs, tmp_path)
    gh.respond(["api", "repos/owner/app/security-advisories/reports"], exit=1,
               stderr="HTTP 422: Validation Failed (token bob-token-value-123456)")
    assert report(str(run_dir), "--submit", "--repo", "owner/app") == 3
    out = capsys.readouterr().out
    assert "422" in out and "bob-token-value-123456" not in out
    assert "security/advisories/new" in out


def test_security_comment_flag_refuses(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--submit", "--repo", "owner/app", "--comment") == 3
    assert "--comment" in capsys.readouterr().out
    assert gh.calls() == []


def test_security_ledger_blocks_second_report(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--submit", "--repo", "owner/app") == 0
    capsys.readouterr()
    assert report(str(run_dir), "--submit", "--repo", "owner/app") == 3
    assert ADVISORY in capsys.readouterr().out
    assert sum("POST" in argv for argv in gh.argvs()) == 1


# --- remove-local ------------------------------------------------------------------------------


def test_remove_local_requires_submit(fail_run, capsys, gh):
    assert report(str(fail_run), "--remove-local") == 3
    assert "--remove-local needs --submit" in capsys.readouterr().out
    assert fail_run.exists() and not (fail_run / "issue").exists()


def test_remove_local_deletes_run_after_attached_filing(fail_run, runs, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    result = draft_json(capsys, str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local")
    assert result["removed"] is True and result["url"] == CREATED
    assert not fail_run.exists()
    assert (runs / "issues.jsonl").exists()


def test_remove_local_deletes_run_with_no_video(fail_run, runs, capsys, gh):
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local") == 0
    assert not fail_run.exists()


def test_remove_local_keeps_run_when_video_not_uploaded(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["repo", "view"], stdout="READ\n")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local") == 0
    out = capsys.readouterr().out
    assert fail_run.exists() and f"kept {fail_run}" in out and "litetraffic prune" in out


def test_remove_local_with_attach_url_keeps_a_video_that_was_never_uploaded(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["repo", "view"], stdout="READ\n")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local", "--attach-url", "https://ci.example.test/a/1") == 0
    out = capsys.readouterr().out
    assert fail_run.exists() and f"kept {fail_run}" in out  # the URL is not checked to hold the video, so it is no proof it is safe
    assert not ("drag " in out and "removed " in out)


def test_remove_local_with_attach_url_and_no_video_deletes(fail_run, capsys, gh):
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local", "--attach-url", "https://ci.example.test/a/1") == 0
    assert not fail_run.exists()


def test_remove_local_never_deletes_for_security_reports(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--submit", "--repo", "owner/app", "--remove-local") == 0
    out = capsys.readouterr().out
    assert run_dir.exists() and f"kept {run_dir}" in out and "security" in out


def test_remove_local_refuses_symlinked_run(fail_run, tmp_path, capsys, gh):
    link = tmp_path / "link"
    link.symlink_to(fail_run, target_is_directory=True)
    assert report(str(link), "--submit", "--repo", "owner/app", "--remove-local") == 3
    assert "symlink" in capsys.readouterr().out
    assert fail_run.exists()
    assert not any(argv[:2] == ["issue", "create"] and "--help" not in argv for argv in gh.argvs())


def test_submit_refuses_while_blockers_exist_but_drafts(fail_run, capsys, gh):
    edit_json(fail_run, "result.json", lambda r: r.update(limitations=["observer HTTP 503"]))
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    assert "observer HTTP 503" in capsys.readouterr().out
    assert (fail_run / "issue" / "issue.md").exists()
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


# --- video -------------------------------------------------------------------------------------


def test_video_missing_extra_bundle_complete_with_enable_hint(fail_run, capsys):
    result = draft_json(capsys, str(fail_run))
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert evidence["video"]["status"] == "unavailable"
    assert "litetraffic[video]" in evidence["video"]["enable"]
    assert result["video"]["status"] == "unavailable"


def test_proof_available_reports_missing_pillow(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pil)
    assert REAL_AVAILABLE() is None


def test_no_video_flag_skips_rendering(fail_run, capsys, monkeypatch):
    fake_video(monkeypatch)
    result = draft_json(capsys, str(fail_run), "--no-video")
    assert result["video"]["status"] == "skipped"
    assert not (fail_run / "issue" / "proof.mp4").exists()


def test_video_render_failure_degrades(fail_run, capsys, monkeypatch):
    monkeypatch.setattr(proof, "available", lambda: "/usr/bin/ffmpeg")

    def boom(evidence, out_dir):
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(proof, "render", boom)
    assert report(str(fail_run)) == 0
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert evidence["video"]["status"] == "failed" and "encoder exploded" in evidence["video"]["reason"]
    assert (fail_run / "issue" / "issue.md").exists()


def test_video_refuses_to_draw_unscrubbed_lines(fail_run, capsys, monkeypatch):
    fake_video(monkeypatch)
    token = "ghp_" + "A1b2C3d4" * 5
    monkeypatch.setattr(proof, "lines", lambda evidence: [("title", (0, 0, 0)), (f"leaked {token}", (0, 0, 0))])
    assert report(str(fail_run)) == 0
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert evidence["video"]["status"] == "failed" and "redaction" in evidence["video"]["reason"]
    assert not (fail_run / "issue" / "proof.mp4").exists()


def drafted_evidence(fail_run, *args) -> dict:
    assert report(str(fail_run), "--no-video", *args) == 0
    return json.loads((fail_run / "issue" / "evidence.json").read_text())


def test_video_is_evidence_not_a_terminal(fail_run, capsys):
    drawn = [text for text, _ in proof.lines(drafted_evidence(fail_run))]
    assert all(text.isascii() for text in drawn), [t for t in drawn if not t.isascii()]
    assert not any(text.lstrip().startswith("$ ") for text in drawn)  # no shell prompt pretending to be a live session
    assert any("Replay of recorded LiteTraffic evidence, not a screen recording" in text for text in drawn)


def test_video_shows_version_samples_invariant_and_verdict(fail_run, capsys):
    evidence = drafted_evidence(fail_run, "--target-version", "v1.9.0-beta")
    drawn = [text for text, _ in proof.lines(evidence)]
    assert any("v1.9.0-beta" in text for text in drawn)
    header = next(text for text in drawn if "short_id" in text)
    assert "discount_applied" in header and "total_gross" in header
    rows = [text for text in drawn if "o_" in text and "true" in text]
    assert len(rows) == 10 and any("o_0xFu6JnvD28yM" in row for row in rows)
    assert any("expected <= 1" in text and "actual 10" in text and "VIOLATED" in text for text in drawn)
    assert any(text.startswith("FAIL") for text in drawn)
    assert any("201 x10" in text for text in drawn)  # every response said success


def test_video_is_at_most_ten_seconds(fail_run, capsys):
    frames = list(proof.frame_plan(drafted_evidence(fail_run)))
    assert 0 < len(frames) <= 10 * proof.FPS


def test_video_render_deterministic(fail_run, tmp_path, capsys, monkeypatch):
    if REAL_AVAILABLE() is None:
        pytest.skip("video extra (pillow + ffmpeg) not installed")
    assert report(str(fail_run), "--no-video") == 0
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    opened = []
    real_open = open

    def watch(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", watch)
    first = proof.render(evidence, tmp_path / "a")
    second = proof.render(evidence, tmp_path / "b")
    monkeypatch.undo()
    assert [p.name for p in first] == ["proof.mp4", "proof.gif"]
    for a, b in zip(first, second):
        assert hashlib.sha256(a.read_bytes()).hexdigest() == hashlib.sha256(b.read_bytes()).hexdigest()
    assert first[0].stat().st_size < 9.5 * 1024 * 1024
    assert not any(str(fail_run) in path for path in opened)


# --- CLI ---------------------------------------------------------------------------------------


def test_report_issue_json_output_shape(fail_run, capsys):
    result = draft_json(capsys, str(fail_run), "--repo", "owner/app")
    assert {"run_id", "verdict", "kind", "fingerprint", "bundle", "files", "would_file", "url", "duplicate", "video", "attach", "removed"} <= set(result)
    assert result["verdict"] == "fail" and result["url"] is None and result["removed"] is False
    assert result["would_file"]["command"][:4] == ["gh", "issue", "create", "-R"]
    assert "owner/app" in result["would_file"]["command"]
    assert "issue.md" in result["files"]


def test_cli_report_requires_subcommand(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["report"])
    assert raised.value.code == 2


def test_bad_run_reference_exits_3(runs, capsys):
    assert report("run_nope", "--runs-dir", str(runs)) == 3
    assert "no run directory or run ID" in capsys.readouterr().out


# --- hardening: classification fails closed ------------------------------------------------------


def rename_failing(run_dir: Path, name: str) -> None:
    edit_json(run_dir, "result.json", lambda r: r["assertions"][2].update(id=name))


@pytest.mark.parametrize("name", [
    "victim-unchanged", "victimUnchanged", "VictimUnchanged", "victim unchanged", "crossTenantReadBlocked", "auth_bypass",
    "bola_check", "access_control_enforced", "rbac_enforced", "ownership_enforced", "other_users_orders_hidden",
    "csrf_rejected", "ssrf_blocked", "sql_injection_rejected", "credential_not_echoed", "password_reset_scoped",
])
def test_security_variant_ids_classify_security(fail_run, runs, name):
    rename_failing(fail_run, name)
    classification = issue.classify(issue.load_run(str(fail_run), runs), force_security=False)
    assert classification.kind == "security", name


def test_security_variant_id_refuses_public_filing(fail_run, capsys, gh):
    rename_failing(fail_run, "auth_bypass")
    assert report(str(fail_run), "--no-video", "--submit", "--public", "--repo", "o/r") == 3
    assert "never filed publicly" in capsys.readouterr().out
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


def test_kit_shaped_sample_is_security_whatever_the_id(fail_run, runs):
    def put(result):
        sample = {"identity": "bob", "method": "GET", "path": "/x/{id}", "status": 200}
        result["assertions"][2] = {"id": "read_blocked", "samples": 4, "status": "fail", "failed": 1, "failures": [
            {"sequence": 1, "logical_key": "k", "expected": {"statuses": [403]}, "actual": {"failures": 1, "samples": [sample]}}]}

    edit_json(fail_run, "result.json", put)
    assert issue.classify(issue.load_run(str(fail_run), runs), force_security=False).kind == "security"


def test_renamed_kit_manifest_is_security(runs, tmp_path):
    run_dir, _ = write_kit_run(runs, tmp_path, {"cross_tenant_read_blocked": [dict(KIT_SAMPLE)]})

    def rename(lock):
        manifest = lock["manifest"]
        manifest["observer"] = "tenant-isolation-kit-v2"
        for journey in manifest.get("journeys") or []:
            journey["name"] = "tenant_isolation"
        for actor in manifest.get("actors") or []:
            actor["auth_recipe"] = "kit_headers"

    def rows(result):
        for row in result["assertions"]:
            if row["status"] == "fail":
                row["id"] = "check_one"
                row["failures"] = [{"sequence": 1, "logical_key": "k", "expected": 1, "actual": 2, "detail": None}]

    edit_json(run_dir, "scenario.lock.json", rename)
    edit_json(run_dir, "result.json", rows)
    classification = issue.classify(issue.load_run(str(run_dir), runs), force_security=False)
    assert classification.kind == "security", classification.reasons


# --- hardening: repo and ledger ------------------------------------------------------------------


@pytest.mark.parametrize("repo", ["o/.", "o/..", "o/.hidden"])
def test_submit_rejects_dot_repo_names(fail_run, capsys, gh, repo):
    assert report(str(fail_run), "--submit", "--public", "--repo", repo) == 3
    assert "--repo must be OWNER/NAME" in capsys.readouterr().out
    assert gh.calls() == []


def test_ledger_lives_in_runs_dir_for_a_run_outside_it(fail_run, runs, tmp_path, capsys, gh):
    elsewhere = tmp_path / "downloaded"
    elsewhere.mkdir()
    moved = Path(shutil.move(str(fail_run), elsewhere / FAIL_ID))
    assert report(str(moved), "--runs-dir", str(runs), "--submit", "--public", "--repo", "owner/app") == 0
    assert (runs / "issues.jsonl").is_file()
    assert not (elsewhere / "issues.jsonl").exists()


def test_security_ledger_is_case_insensitive_on_repo(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--runs-dir", str(runs), "--submit", "--repo", "owner/app") == 0
    capsys.readouterr()
    assert report(str(run_dir), "--runs-dir", str(runs), "--submit", "--repo", "Owner/App") == 3
    assert ADVISORY in capsys.readouterr().out
    assert sum("POST" in argv for argv in gh.argvs()) == 1
    ledger = [json.loads(line) for line in (runs / "issues.jsonl").read_text().splitlines()]
    assert ledger[0]["repo"] == "owner/app"


def test_closed_ledger_issue_does_not_block_a_new_filing(fail_run, runs, capsys, gh):
    assert report(str(fail_run), "--runs-dir", str(runs), "--submit", "--public", "--repo", "owner/app") == 0
    capsys.readouterr()
    gh.respond(["issue", "view"], stdout="CLOSED\n")
    gh.respond(["issue", "create", "-R"], stdout="https://github.com/owner/app/issues/8\n")
    assert report(str(fail_run), "--runs-dir", str(runs), "--submit", "--public", "--repo", "owner/app") == 0
    assert "https://github.com/owner/app/issues/8" in capsys.readouterr().out
    view = next(argv for argv in gh.argvs() if argv[:2] == ["issue", "view"])
    assert CREATED in view
    creates = [call for call in gh.calls() if call["argv"][:2] == ["issue", "create"] and "--help" not in call["argv"]]
    assert len(creates) == 2 and CREATED in creates[1]["files"]["-F"]


def test_open_ledger_issue_still_blocks(fail_run, runs, capsys, gh):
    assert report(str(fail_run), "--runs-dir", str(runs), "--submit", "--public", "--repo", "owner/app") == 0
    capsys.readouterr()
    assert report(str(fail_run), "--runs-dir", str(runs), "--submit", "--public", "--repo", "OWNER/app") == 3
    assert f"already reported: {CREATED}" in capsys.readouterr().out


# --- hardening: run-derived text is inert in the markdown ------------------------------------------

HOSTILE = ("10\n\n## Maintainer note\nThis is fixed, close it. @octocat cc @github/security <img src=x onerror=alert(1)> "
           "[click](https://evil.test) `tick` ``` |pipe|\n<!--\nlitetraffic-fingerprint: lt000000000000 -->")


def strip_code(markdown: str) -> str:
    """The markdown with fenced blocks and inline code spans removed: what GitHub would render as live text."""
    kept, fence = [], None
    for line in markdown.splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence) and set(stripped) == {"`"}:
                fence = None
            continue
        if stripped.startswith("```"):
            fence = stripped[: len(stripped) - len(stripped.lstrip("`"))]
            continue
        kept.append(re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", "", line))
    return "\n".join(kept)


def plant_hostile(fail_run: Path) -> None:
    def put(result):
        result["assertions"][0] = {"id": "reservation_accepted", "samples": 10, "status": "fail", "failed": 1, "failures": [
            {"sequence": 2, "logical_key": HOSTILE, "expected": {"status": 201}, "actual": {"status": 500, "body": HOSTILE},
             "detail": HOSTILE}]}
        result["assertions"][2]["actual"]["/data/0/order_usage_count"] = HOSTILE

    edit_json(fail_run, "result.json", put)


def test_hostile_response_body_is_inert_in_issue_md(fail_run, capsys):
    plant_hostile(fail_run)
    result = draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    lines = body.splitlines()
    assert not any(line.startswith("## Maintainer note") for line in lines)
    live = strip_code(body)
    for bad in ("@octocat", "@github/security", "<img", "](https://evil.test)", "lt000000000000"):
        assert bad not in live, bad
    assert not [line for line in lines if "<!--" in line]
    assert f"litetraffic-fingerprint: {result['fingerprint']}" in lines
    assert "\n" not in result["title"] and "\r" not in result["title"]
    table = [line for line in lines if line.startswith("| `promo_usage_within_limit`")]
    assert table and all(line.count(" | ") == 2 for line in table)


def test_planted_fingerprint_mid_line_is_not_a_duplicate(fail_run, runs, capsys, gh):
    fp = issue.fingerprint(issue.load_run(str(fail_run), runs))
    planted = [{"number": 4, "url": "https://github.com/owner/app/issues/4",
                "body": f"observed `10 litetraffic-fingerprint: {fp}` in a table cell"}]
    gh.respond(["search", "issues"], stdout=json.dumps(planted))
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    assert CREATED in capsys.readouterr().out


# --- hardening: scrubbing -----------------------------------------------------------------------


def test_default_port_hostname_scrubbed(fail_run, capsys):
    host = "staging.acme-internal.corp"
    edit_json(fail_run, "run.json", lambda r: r.update(target=f"https://{host}"))
    plant(fail_run, f"served by {host}; see //{host}/x and mail ops@{host} or {host.upper()}")
    assert report(str(fail_run)) == 0
    out = capsys.readouterr().out
    for name, text in bundle_text(fail_run).items():
        assert host not in text.lower(), name
    assert host not in out.lower()


def test_encoded_secret_spellings_scrubbed(fail_run, monkeypatch, capsys):
    import base64
    from urllib.parse import quote

    secret = "Sup3r$ecret+Val/ue=="
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", secret)
    upper = quote(secret, safe="")
    forms = [upper, re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), upper), base64.b64encode(secret.encode()).decode(),
             base64.urlsafe_b64encode(secret.encode()).decode().rstrip("="), base64.b64encode(secret.encode()).decode().rstrip("=")]
    plant(fail_run, " ".join(f"q{i}={form}" for i, form in enumerate(forms)))
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    out = capsys.readouterr().out
    for name, text in bundle_text(fail_run).items():
        for form in forms:
            assert form not in text, (name, form)
    assert all(form not in out for form in forms)


def test_leak_check_catches_encoded_secrets_and_opaque_bearer(fail_run, runs, monkeypatch):
    import base64

    secret = "Sup3r$ecret+Val/ue=="
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", secret)
    scrubber = issue.Scrubber(issue.load_run(str(fail_run), runs))
    assert scrubber.leak("x=Sup3r%24ecret%2BVal%2Fue%3D%3D")
    assert scrubber.leak("x=sup3r%24ecret%2bval%2fue%3d%3d".replace("sup3r", "Sup3r").replace("val", "Val"))
    assert scrubber.leak(base64.b64encode(secret.encode()).decode())
    assert scrubber.leak("Authorization: Bearer abcdefghij0123456789KLMNOP")
    assert not scrubber.leak('-H "Authorization: Bearer ${LT_HIEVENTS_ORG_TOKEN}"')
    assert not scrubber.leak("Authorization: Bearer [redacted]")


# --- hardening: repro.sh exit codes and shell safety ---------------------------------------------


def run_repro(script: Path, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    clean = {key: value for key, value in os.environ.items() if key not in {"TARGET", "LT_HIEVENTS_ORG_TOKEN"}}
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env={**clean, **env}, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=60)


class canned_server:
    def __init__(self, body: dict) -> None:
        _Canned.body, _Canned.seen = json.dumps(body).encode(), []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Canned)

    def __enter__(self) -> str:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()


def test_repro_without_target_exits_2(fail_run, capsys):
    assert report(str(fail_run)) == 0
    done = run_repro(script_of(fail_run), {"LT_HIEVENTS_ORG_TOKEN": "local-test-token"})
    assert done.returncode == 2, done.stdout + done.stderr
    assert "TARGET" in done.stderr and "REPRODUCED" not in done.stdout


def test_repro_without_credential_exits_2(fail_run, capsys):
    assert report(str(fail_run)) == 0
    done = run_repro(script_of(fail_run), {"TARGET": "http://127.0.0.1:9"})
    assert done.returncode == 2, done.stdout + done.stderr
    assert "LT_HIEVENTS_ORG_TOKEN" in done.stderr


def test_repro_author_step_failure_exits_2(fail_run, capsys):
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    with canned_server({"unexpected": "shape"}) as target:
        done = run_repro(script_of(fail_run), {"TARGET": target, "LT_HIEVENTS_ORG_TOKEN": "local-test-token",
                                                            "REPRO_DEADLINE_SECONDS": "0"})
    assert done.returncode == 2, done.stdout + done.stderr
    assert "REPRODUCED" not in done.stdout


HOSTILE_NAME = 'promo"; touch PWNED1; echo "$(touch PWNED2)`touch PWNED3`\ntouch PWNED4 #'


def test_repro_labels_are_shell_inert(fail_run, tmp_path, capsys):
    def lock(data):
        manifest = data["manifest"]
        manifest["assertions"] = [HOSTILE_NAME if a == "promo_usage_within_limit" else a for a in manifest["assertions"]]
        manifest["observation"]["assertion"] = HOSTILE_NAME

    edit_json(fail_run, "result.json", lambda r: r["assertions"][2].update(id=HOSTILE_NAME))
    edit_json(fail_run, "observation.json", lambda o: o.update(assertion=HOSTILE_NAME))
    edit_json(fail_run, "scenario.lock.json", lock)
    edit_json(fail_run, "run.json", lambda r: r.update(scenario="oversell\ntouch PWNED5 #"))
    assert report(str(fail_run)) == 0
    script = script_of(fail_run)
    assert "# === effect:" in script.read_text()
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    work = tmp_path / "work"
    work.mkdir()
    with canned_server({"data": [{"max_allowed_usages": 1, "order_usage_count": 10}]}) as target:
        done = run_repro(script, {"TARGET": target, "LT_HIEVENTS_ORG_TOKEN": "local-test-token", "REPRO_DEADLINE_SECONDS": "0"}, work)
    assert done.returncode == 1, done.stdout + done.stderr
    assert sorted(path.name for path in work.iterdir()) == []


def test_security_upgrade_keeps_expected_strings_in_repro(fail_run, capsys):
    def result(data):
        row = data["assertions"][2]
        row["expected"]["/data/0/status"] = {"eq": "COMPLETED"}
        row["actual"]["/data/0/status"] = "COMPLETED"

    def observation(data):
        data["checks"]["/data/0/status"] = {"actual": "COMPLETED", "matcher": {"eq": "COMPLETED"}, "pass": True}

    edit_json(fail_run, "result.json", result)
    edit_json(fail_run, "observation.json", observation)
    assert report(str(fail_run), "--security") == 0
    files = bundle_text(fail_run)
    assert "COMPLETED" in files["check-effect.sh"]
    assert "promo_usage_within_limit" in files["security.md"] and "[marker:expected" not in files["security.md"]
    body = {"data": [{"max_allowed_usages": 1, "order_usage_count": 1, "status": "COMPLETED"}]}
    with canned_server(body) as target:
        done = run_repro(script_of(fail_run), {"TARGET": target, "LT_HIEVENTS_ORG_TOKEN": "local-test-token",
                                                            "REPRO_DEADLINE_SECONDS": "0"})
    assert done.returncode == 0, done.stdout + done.stderr


# --- hardening: video rendering, partial attach and --remove-local -------------------------------


class _StrictPopen:
    """Popen as Python 3.12 behaves: communicate() after closing stdin raises 'flush of closed file'."""

    def __init__(self, argv, stdin=None, stdout=None, stderr=None):
        import io

        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stderr = io.BytesIO(b"") if stderr == subprocess.PIPE else None
        self.returncode = None

    def communicate(self, input=None, timeout=None):  # noqa: A002
        if self.stdin is not None and self.stdin.closed:
            raise ValueError("flush of closed file")
        self.returncode = 0
        return b"", b""

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        pass


def test_ffmpeg_pipe_does_not_flush_closed_stdin(monkeypatch):
    monkeypatch.setattr(proof.subprocess, "Popen", _StrictPopen)
    proof._ffmpeg(["ffmpeg", "-i", "-", "out.mp4"], iter([b"frame-1", b"frame-2"]))
    proof._ffmpeg(["ffmpeg", "-i", "in.mp4", "out.gif"])


def test_real_video_renders_through_report(fail_run, capsys, monkeypatch):
    if REAL_AVAILABLE() is None:
        pytest.skip("video extra (pillow + ffmpeg) not installed")
    monkeypatch.setattr(proof, "available", REAL_AVAILABLE)
    result = draft_json(capsys, str(fail_run))
    assert result["video"]["status"] == "rendered", result["video"]
    assert (fail_run / "issue" / "proof.mp4").stat().st_size > 0


def test_partial_attach_failure_edits_the_embed_out_of_the_filed_body(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["issue", "create", "-R"], stdout=CREATED + "\n", stderr="failed to upload proof.mp4: 422", exit=1)
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 0
    edit = next(call for call in gh.calls() if call["argv"][:2] == ["issue", "edit"])
    assert edit["argv"][2] == CREATED
    assert "![LiteTraffic replay]" not in edit["files"]["-F"] and "litetraffic-fingerprint" in edit["files"]["-F"]
    assert "![LiteTraffic replay]" not in (fail_run / "issue" / "issue.md").read_text()


def test_remove_local_keeps_run_when_video_render_failed(fail_run, capsys, gh, monkeypatch):
    monkeypatch.setattr(proof, "available", lambda: "/usr/bin/ffmpeg")

    def boom(evidence, out_dir):
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(proof, "render", boom)
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local") == 0
    out = capsys.readouterr().out
    assert fail_run.exists() and f"kept {fail_run}" in out


def test_remove_local_keeps_run_after_failed_upload_even_with_attach_url(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["issue", "create", "-R"], stdout=CREATED + "\n", stderr="failed to upload proof.mp4: 422", exit=1)
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local", "--attach-url", "https://ci.example.test/a/1") == 0
    assert fail_run.exists() and f"kept {fail_run}" in capsys.readouterr().out


def test_remove_local_ignores_autodetected_actions_url(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    for name, value in (("GITHUB_SERVER_URL", "https://github.com"), ("GITHUB_REPOSITORY", "owner/app"), ("GITHUB_RUN_ID", "12345")):
        monkeypatch.setenv(name, value)
    gh.respond(["repo", "view"], stdout="READ\n")
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--remove-local") == 0
    assert fail_run.exists() and f"kept {fail_run}" in capsys.readouterr().out


@pytest.mark.parametrize("url", ["x", "http://ci.example.test/a", "https://ci.example.test/a b", "javascript:alert(1)"])
def test_attach_url_must_be_plain_https(fail_run, capsys, gh, url):
    assert report(str(fail_run), "--attach-url", url) == 3
    assert "--attach-url must be a plain https:// URL" in capsys.readouterr().out


# --- issue quality: target version, known issues, samples, mechanism, metadata ---------------------


def top_of(body: str) -> str:
    """What a maintainer reads before the first heading."""
    return body.split("## Summary", 1)[0]


def add_server_json(run_dir: Path, image: str | None) -> None:
    server = {"schema_version": 1, "source": "docker", "problems": [],
              "containers": {"api": {"image": image, "log_lines": 3, "error_lines": 0, "signatures": [], "truncated": False}}}
    (run_dir / "server.json").write_text(json.dumps(server, indent=2, sort_keys=True) + "\n")
    reseal(run_dir)


def test_target_version_from_captured_image_tag(fail_run, capsys):
    add_server_json(fail_run, "daveearley/hi.events-all-in-one:v1.9.0-beta")
    draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    assert "Target version:" in top_of(body) and "v1.9.0-beta" in top_of(body)
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert evidence["target"]["images"] == {"api": "daveearley/hi.events-all-in-one:v1.9.0-beta"}
    assert "v1.9.0-beta" in evidence["target"]["version"]


def test_target_version_flag_when_nothing_was_captured(fail_run, capsys):
    draft_json(capsys, str(fail_run), "--target-version", "1.11.0-beta")
    top = top_of((fail_run / "issue" / "issue.md").read_text())
    assert "Target version:" in top and "1.11.0-beta" in top and "not recorded" not in top


def test_target_version_not_recorded_is_prominent(fail_run, capsys):
    add_server_json(fail_run, None)  # captured, but docker reported no image
    result = draft_json(capsys, str(fail_run))
    top = top_of((fail_run / "issue" / "issue.md").read_text())
    assert "**Target version: not recorded.**" in top and "--target-version" in top
    assert "unknown (not reported by the target)" not in (fail_run / "issue" / "issue.md").read_text()
    assert any("target version" in item for item in result["warnings"])


def test_known_issue_field_is_filled_by_the_reporter(fail_run, capsys):
    url = "https://github.com/HiEventsDev/hi.events/issues/1223"
    draft_json(capsys, str(fail_run), "--known-issue", url)
    top = top_of((fail_run / "issue" / "issue.md").read_text())
    assert f"**Already reported upstream:** <{url}>" in top
    assert json.loads((fail_run / "issue" / "evidence.json").read_text())["known_issue"]["reporter"] == url


def test_known_issue_field_says_unchecked_by_default(fail_run, capsys):
    draft_json(capsys, str(fail_run))
    top = top_of((fail_run / "issue" / "issue.md").read_text())
    assert "**Known/fixed?** not checked" in top and "--known-issue" in top


@pytest.mark.parametrize("url", ["CVE-2026-57959", "http://example.test/1", "https://x.test/a b"])
def test_known_issue_must_be_plain_https(fail_run, capsys, url):
    assert report(str(fail_run), "--known-issue", url) == 3
    assert "--known-issue must be a plain https:// URL" in capsys.readouterr().out


def test_scenario_readme_declaring_a_cve_warns(fail_run, tmp_path, capsys):
    scenario = Path(shutil.copytree(SCENARIO, tmp_path / "scenario"))
    (scenario / "README.md").write_text("# Hi.Events oversell\n\nReproduces CVE-2026-57959 (hi.events#1223), fixed in v1.11.0-beta.\n")
    assert report(str(fail_run), "--scenario", str(scenario)) == 0
    out = capsys.readouterr().out
    assert "warning:" in out and "CVE-2026-57959" in out
    top = top_of((fail_run / "issue" / "issue.md").read_text())
    assert "CVE-2026-57959" in top and "already" in top
    evidence = json.loads((fail_run / "issue" / "evidence.json").read_text())
    assert evidence["known_issue"]["declared"] == ["CVE-2026-57959"]


def test_manifest_report_known_issue_warns_without_scenario_dir(fail_run, capsys):
    edit_json(fail_run, "scenario.lock.json", lambda lock: lock["manifest"].update(report={"known_issue": "GHSA-abcd-efgh-ijkl"}))
    result = draft_json(capsys, str(fail_run))
    assert any("GHSA-abcd-efgh-ijkl" in item for item in result["warnings"])
    assert "GHSA-abcd-efgh-ijkl" in top_of((fail_run / "issue" / "issue.md").read_text())


def test_no_known_issue_warning_without_a_declaration(fail_run, capsys):
    result = draft_json(capsys, str(fail_run), "--target-version", "1.0")
    assert result["warnings"] == []
    assert "CVE-" not in (fail_run / "issue" / "issue.md").read_text()


def section(body: str, heading: str) -> str:
    return body.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_samples_table_carries_per_journey_evidence(fail_run, capsys):
    draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    assert "no per-request samples" not in body
    samples = section(body, "Samples")
    rows = [line for line in samples.splitlines() if line.startswith("| ") and "---" not in line]
    header, journeys = rows[0], rows[1:]
    for column in ("short_id", "discount_applied", "promo_code", "total_gross"):
        assert column in header, column
    assert len(journeys) == 10
    assert "o_0xFu6JnvD28yM" in samples and "o_bEysuf1XeKPuq" in samples
    assert all("`true`" in row and "`0`" in row for row in journeys)


def test_samples_table_is_in_the_duplicate_comment(fail_run, runs, capsys, gh):
    fp = issue.fingerprint(issue.load_run(str(fail_run), runs))
    gh.respond(["search", "issues"], stdout=json.dumps([{"number": 3, "url": "https://github.com/owner/app/issues/3",
                                                          "body": f"litetraffic-fingerprint: {fp}"}]))
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app", "--comment") == 0
    comment = (fail_run / "issue" / "comment.md").read_text()
    assert "o_0xFu6JnvD28yM" in comment and "no per-request samples" not in comment


def test_summary_describes_the_funnel_and_peak_in_flight(fail_run, capsys):
    draft_json(capsys, str(fail_run))
    summary = section((fail_run / "issue" / "issue.md").read_text(), "Summary")
    assert "concurrent" not in summary.lower()
    assert "`reserved` 10/10" in summary and "`completed` 10/10" in summary
    assert "at most 3" in summary  # complete_order peaked at 3 in flight
    assert "every journey reached `reserved` before any reached `completed`" in summary


def test_summary_never_claims_overlap_that_was_not_observed(fail_run, capsys):
    edit_json(fail_run, "result.json", lambda r: r["metrics"].update(overlap={"reserve_order": 1, "complete_order": 1}))
    draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    assert "concurrent" not in body.lower()
    assert "did not overlap" in section(body, "Summary")


def test_metadata_is_collapsed_and_latencies_rounded(fail_run, capsys):
    draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    visible = re.sub(r"<details>.*?</details>", "", body, flags=re.DOTALL)
    for hidden in ("5ee64a14f5063505e67beb4a1c811f1773a6a5debf005806a5b5e8f706a25af0", FAIL_ID, "commit/", "go1."):
        assert hidden in body and hidden not in visible, hidden
    assert not re.search(r"\d+\.\d{3}", visible)
    sent = section(body, "Requests sent")
    assert "154" in sent and "79" in sent and "153.967" not in body
    assert "What LiteTraffic sent" not in body


def test_one_searchable_fingerprint_and_no_marketing_footer(fail_run, capsys):
    from litetraffic.github import carries_fingerprint

    result = draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    fp = result["fingerprint"]
    assert body.count(fp) == 1
    assert carries_fingerprint(body, fp)
    assert "Reported with LiteTraffic" not in body and "<!--" not in body


def test_scenario_report_security_flag_routes_privately(fail_run, runs):
    edit_json(fail_run, "scenario.lock.json", lambda lock: lock["manifest"].update(report={"security": True}))
    evidence = issue.load_run(str(fail_run), runs)
    assert evidence.manifest is not None and evidence.manifest.report.security is True
    classification = issue.classify(evidence, force_security=False)
    assert classification.kind == "security"
    assert any("report.security" in reason for reason in classification.reasons)


def test_report_block_leaves_existing_scenario_digests_alone():
    from litetraffic.scenario import load_scenario

    recorded = json.loads((DATA / "fail_run" / FAIL_ID / "run.json").read_text())["scenario_sha256"]
    bundle = load_scenario(SCENARIO)
    assert bundle.digest == recorded  # digests hash the raw manifest, so a new optional key changes nothing for old ones
    assert bundle.manifest.report.security is False and bundle.manifest.report.known_issue is None


# --- hardening round 3: fail closed on secrets, more security names, known issues, input edges ----


def test_unset_declared_secret_blocks_submit_and_names_it(fail_run, monkeypatch, capsys, gh):
    monkeypatch.delenv("LT_HIEVENTS_ORG_TOKEN")
    result = draft_json(capsys, str(fail_run))
    assert any("LT_HIEVENTS_ORG_TOKEN" in item for item in result["caveats"])
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    out = capsys.readouterr().out
    assert "LT_HIEVENTS_ORG_TOKEN" in out and "not set" in out
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


def test_manifest_from_another_version_still_scrubs_and_blocks_submit(fail_run, runs, monkeypatch, capsys, gh):
    secret = "Sup3r$ecret+Val/ue=="
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", secret)
    edit_json(fail_run, "scenario.lock.json", lambda lock: lock["manifest"].update(future_option=True))
    plant(fail_run, f"echo {secret}")
    assert issue.load_run(str(fail_run), runs).manifest is None  # this version cannot validate it
    result = draft_json(capsys, str(fail_run))
    assert all(secret not in text for text in bundle_text(fail_run).values())
    assert any("does not validate" in item for item in result["caveats"])
    assert report(str(fail_run), "--submit", "--public", "--repo", "owner/app") == 3
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


@pytest.mark.parametrize("encode", ["quote_default", "path_escape", "php_slashes", "hex", "html_entities"])
def test_partially_encoded_secret_fails_closed(fail_run, monkeypatch, capsys, gh, encode):
    import html
    from urllib.parse import quote

    secret = "Sup3r$ecret+Val/ue=="
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", secret)
    form = {"quote_default": quote(secret), "path_escape": secret.replace("/", "%2F"), "php_slashes": secret.replace("/", "\\/"),
            "hex": secret.encode().hex(), "html_entities": html.escape(secret).replace("$", "&#36;").replace("+", "&#43;")}[encode]
    plant(fail_run, f"error: token={form}")
    code = report(str(fail_run))
    if code == 0:  # scrubbed: then no spelling of it may survive anywhere
        assert all(form not in text for text in bundle_text(fail_run).values())
    else:
        assert code == 3 and "redaction check failed" in capsys.readouterr().out and not (fail_run / "issue").exists()


@pytest.mark.parametrize("name", ["private_orders_not_visible", "admin_only_endpoint", "role_enforced", "guest_cannot_see_drafts",
                                  "pii_not_exposed", "sqli_rejected", "jwt_signature_checked", "login_required",
                                  "anonymous_cannot_read", "data_exposure_check", "open_redirect_rejected", "cors_restricted",
                                  "othersOrdersInvisible", "visibility_respected"])
def test_more_security_names_classify_as_security(fail_run, runs, name):
    rename_failing(fail_run, name)
    assert issue.classify(issue.load_run(str(fail_run), runs), force_security=False).kind == "security", name


def test_known_issue_blocks_filing_a_duplicate(fail_run, capsys, gh):
    url = "https://github.com/HiEventsDev/hi.events/issues/1223"
    result = draft_json(capsys, str(fail_run), "--known-issue", url, "--target-version", "v1.9.0-beta")
    assert any("already reported" in item for item in result["caveats"])
    top = top_of((fail_run / "issue" / "issue.md").read_text())
    assert f"**Already reported upstream:** <{url}>" in top
    assert report(str(fail_run), "--known-issue", url, "--submit", "--public", "--repo", "owner/app") == 3
    assert "already reported" in capsys.readouterr().out
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


@pytest.mark.parametrize("flag,value", [("--repo", "owner/app\n"), ("--known-issue", "https://example.test/issues/1\n"),
                                        ("--attach-url", "https://ci.example.test/a/1\n")])
def test_trailing_newline_is_rejected(fail_run, capsys, flag, value):
    args = ["--submit", "--repo", "owner/app"] if flag != "--repo" else ["--submit"]
    assert report(str(fail_run), flag, value, *args) == 3


def test_repo_dot_git_is_the_same_repo(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--submit", "--repo", "owner/app") == 0
    capsys.readouterr()
    assert report(str(run_dir), "--submit", "--repo", "owner/app.git") == 3
    assert sum("POST" in argv for argv in gh.argvs()) == 1


def test_moved_run_with_filed_json_is_not_reported_twice(runs, tmp_path, capsys, gh):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--submit", "--repo", "owner/app") == 0
    moved = tmp_path / "elsewhere" / run_dir.name
    shutil.copytree(run_dir, moved)
    (runs / "issues.jsonl").unlink()
    capsys.readouterr()
    assert report(str(moved), "--submit", "--repo", "owner/app") == 3
    assert sum("POST" in argv for argv in gh.argvs()) == 1


def test_issue_never_points_to_a_video_the_maintainer_cannot_see(fail_run, capsys, monkeypatch):
    fake_video(monkeypatch)
    draft_json(capsys, str(fail_run))
    body = (fail_run / "issue" / "issue.md").read_text()
    assert (fail_run / "issue" / "proof.mp4").exists()
    assert "proof.mp4" not in body  # not attached, not embedded: the body must not send a maintainer looking for it


# --- hardening round 4: public filing is an explicit choice; undeclared credentials; unreadable scenario ---


def test_public_issue_needs_explicit_public(fail_run, capsys, gh):
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
    out = capsys.readouterr().out
    assert "PUBLIC" in out and "--public" in out and "--security" in out
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--public") == 0


@pytest.mark.parametrize("name", ["user_a_cannot_read_user_b", "attacker_gets_404", "mass_assignment_rejected"])
def test_unrecognised_authz_names_still_cannot_go_public_by_accident(fail_run, capsys, gh, name):
    rename_failing(fail_run, name)
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


def test_credential_looking_env_var_is_scrubbed_even_if_undeclared(fail_run, monkeypatch, capsys):
    key = "sk_live_Ab3Q/Def+Ghi=Jkl9mnopZZ"
    monkeypatch.setenv("API_KEY", key)
    plant(fail_run, f"upstream rejected X-Api-Key {key}")
    assert report(str(fail_run)) == 0
    assert all(key not in text for text in bundle_text(fail_run).values())


def test_script_reading_an_undeclared_env_var_blocks_submit(fail_run, tmp_path, capsys, gh):
    scenario = Path(shutil.copytree(SCENARIO, tmp_path / "scenario"))
    reseal_scenario_script(fail_run, scenario, "const key = __ENV.PARTNER_SECRET_VALUE;\n")
    assert report(str(fail_run), "--scenario", str(scenario), "--submit", "--repo", "owner/app", "--public") == 3
    assert "PARTNER_SECRET_VALUE" in capsys.readouterr().out


@pytest.mark.parametrize("damage", ["missing", "not_a_dict"])
def test_unreadable_scenario_lock_blocks_submit(fail_run, capsys, gh, damage):
    if damage == "missing":
        (fail_run / "scenario.lock.json").unlink()
        reseal(fail_run)
    else:
        edit_json(fail_run, "scenario.lock.json", lambda lock: lock.update(manifest=json.dumps(lock["manifest"])))
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--public") == 3
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


@pytest.mark.parametrize("variant", ["base64_offset_1", "base64_offset_2", "unicode_escape", "percent_x4"])
def test_more_secret_spellings_fail_closed(fail_run, monkeypatch, capsys, variant):
    import base64

    secret = "Sup3r$ecret+Val/ue=="
    monkeypatch.setenv("LT_HIEVENTS_ORG_TOKEN", secret)
    form = {"base64_offset_1": base64.b64encode(b"u:" + secret.encode()).decode(),
            "base64_offset_2": base64.b64encode(b"ab:" + secret.encode()).decode(),
            "unicode_escape": secret.replace("/", "\\u002f").replace("+", "\\u002b"),
            "percent_x4": secret.replace("/", "%2525252F")}[variant]
    plant(fail_run, f"server said: {form}")
    code = report(str(fail_run))
    assert code == 3 or all(form not in text for text in bundle_text(fail_run).values())


def test_jwt_pattern_is_linear_on_long_runs():
    import time

    started = time.monotonic()
    issue.JWT_RE.search("eyJ" * 40000)
    assert time.monotonic() - started < 1


def test_comment_adds_evidence_to_the_known_issue(fail_run, capsys, gh):
    url = "https://github.com/owner/app/issues/3"
    assert report(str(fail_run), "--known-issue", url, "--submit", "--public", "--repo", "owner/app", "--comment") == 0
    comment = next(argv for argv in gh.argvs() if argv[:2] == ["issue", "comment"])
    assert url in comment or "3" in comment
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


# --- hardening round 5: scrub secret-looking values whatever the variable is called; __ENV fails closed ---


@pytest.mark.parametrize(("name", "value", "leaked"), [
    ("DATABASE_URL", "postgres://app:Xk29fQpL7mzr@db.internal:5432/app", "Xk29fQpL7mzr"),
    ("DB_URL", "mysql://root:Pw0rdQ93zz@10.0.0.5/app", "Pw0rdQ93zz"),
    ("STRIPE_SK", "sk_live_51HxYz9AbCdEfGh", "sk_live_51HxYz9AbCdEfGh"),
    ("SENTRY_DSN", "https://abc123def456@o1.ingest.sentry.io/42", "abc123def456"),
    ("WEBHOOK_URL", "https://hooks.example.test/in?token=T0kQ8zLm93Kd", "T0kQ8zLm93Kd"),
])
def test_secret_looking_env_values_are_scrubbed_whatever_the_name(fail_run, monkeypatch, capsys, name, value, leaked):
    monkeypatch.setenv(name, value)
    plant(fail_run, f"upstream said: {value}")
    assert report(str(fail_run)) == 0
    assert all(leaked not in text for text in bundle_text(fail_run).values())


@pytest.mark.parametrize(("name", "value"), [("NEXTAUTH_URL", "http://127.0.0.1:8081"), ("SESSION_STORE", "redis-cache"),
                                             ("LANG", "en_US.UTF-8"), ("TERM", "xterm-256color")])
def test_plain_settings_are_not_scrubbed_out_of_the_report(fail_run, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    plant(fail_run, "500 from http://127.0.0.1:8081/api via redis-cache with xterm-256color en_US.UTF-8")
    assert report(str(fail_run)) == 0
    body = (fail_run / "issue" / "evidence.json").read_text()
    assert "$TARGET/api" in body and "redis-cache" in body and "xterm-256color" in body and "en_US.UTF-8" in body


@pytest.mark.parametrize("code", ["const { STRIPE_SK } = __ENV;", "const k = __ENV ['STRIPE_SK'];", "const k = __ENV[name];",
                                  "const k = __ENV[`STRIPE_SK`];", "const k = __ENV?.STRIPE_SK;", "const env = __ENV; env.X;"])
def test_env_reads_that_cannot_be_checked_block_submit(fail_run, tmp_path, capsys, gh, code):
    scenario = Path(shutil.copytree(SCENARIO, tmp_path / "scenario"))
    reseal_scenario_script(fail_run, scenario, code + "\n")
    assert report(str(fail_run), "--scenario", str(scenario), "--submit", "--public", "--repo", "owner/app") == 3
    assert not any(argv[:2] == ["issue", "create"] for argv in gh.argvs())


def test_shipped_examples_read_env_in_checkable_ways():
    for script in sorted((Path(__file__).parents[1] / "examples").glob("*/journeys.js")):
        names, unresolved = issue.env_reads(script.read_text())
        assert not unresolved, (script, unresolved)
