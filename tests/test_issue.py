"""`litetraffic report issue`: drafts, routing, redaction and filing, against a fake `gh` only."""

import hashlib
import http.server
import json
import os
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


@pytest.fixture(autouse=True)
def gh(tmp_path, monkeypatch):
    """Every test runs with a fake gh first on PATH; the real one must never run."""
    root = tmp_path / "ghroot"
    root.mkdir()
    fake = FakeGh(root)
    monkeypatch.setenv("PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "ghp_fakefakefakefakefakefakefakefake00")
    for name in ("GH_REPO", "GITHUB_TOKEN", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "LT_HIEVENTS_ORG_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(proof, "available", lambda: None)  # video tests opt back in
    assert shutil.which("gh") == str(root / "bin" / "gh")
    return fake


def reseal(run_dir: Path) -> None:
    files = artifact_files(run_dir)
    data = {"files": files, "run_id": run_dir.name, "schema_version": 1, "total_bytes": sum(f["bytes"] for f in files)}
    (run_dir / "artifacts.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


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
    for name in ("issue.md", "repro.sh", "evidence.json"):
        assert (bundle / name).is_file(), name
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    assert stat.S_IMODE((bundle / "issue.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((bundle / "evidence.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((bundle / "repro.sh").stat().st_mode) == 0o700
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
    assert f"<!-- litetraffic-fingerprint: {fingerprint} -->" in body
    assert f"litetraffic-fingerprint: {fingerprint}" in body.splitlines()[-1]
    summary = body.split("## Summary", 1)[1].split("\n## ", 1)[0]
    assert "order_usage_count" in summary and "10" in summary and "≤ 1" in summary
    assert "| Check | Expected | Actual |" in body
    environment = body.split("## Environment", 1)[1].split("\n## ", 1)[0]
    assert "0.1.0.dev0" in environment and "42" in environment
    assert "5ee64a14f5063505e67beb4a1c811f1773a6a5debf005806a5b5e8f706a25af0" in environment
    assert "## What this does and does not establish" in body
    assert "does not know the root cause" in body
    assert "found the root cause" not in body
    assert "repro.sh" in body
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
    script = (fail_run / "issue" / "repro.sh").read_text()
    assert "${LT_HIEVENTS_ORG_TOKEN}" in script
    assert ': "${LT_HIEVENTS_ORG_TOKEN:?' in script and ': "${TARGET:?' in script
    assert SECRET not in script


# --- repro -------------------------------------------------------------------------------------


def test_repro_is_valid_bash(fail_run, runs, tmp_path, capsys):
    kit_run, scenario = write_kit_run(runs, tmp_path)
    assert report(str(fail_run)) == 0
    assert report(str(kit_run), "--scenario", str(scenario)) == 0
    shutil.rmtree(fail_run / "issue")
    assert report(str(fail_run), "--scenario", str(SCENARIO)) == 0
    for run_dir in (fail_run, kit_run):
        script = run_dir / "issue" / "repro.sh"
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
        done = subprocess.run(["bash", str(fail_run / "issue" / "repro.sh")], capture_output=True, text=True, env=env, timeout=60)
    finally:
        server.shutdown()
    assert done.returncode == code, done.stdout + done.stderr
    assert ("VIOLATED" in done.stdout) == (code == 1)
    assert _Canned.seen[0] == ("/api/events/1/promo-codes", "Bearer local-test-token")


def test_repro_kit_emits_curl_per_failing_sample(runs, tmp_path, capsys):
    run_dir, scenario = write_kit_run(runs, tmp_path)
    assert report(str(run_dir), "--scenario", str(scenario)) == 0
    script = (run_dir / "issue" / "repro.sh").read_text()
    assert '"$TARGET/api/v1/repos/alice/r"' in script
    assert "Authorization: Bearer ${GITEA_TOKEN_BOB}" in script
    assert "401 403 404" in script
    assert "LT_MARKERS_ALICE" in script
    assert "alice-private-marker-1" not in script


def test_kit_run_without_scenario_still_drafts_and_says_why(runs, tmp_path, capsys):
    run_dir, _ = write_kit_run(runs, tmp_path)
    assert report(str(run_dir)) == 0
    script = (run_dir / "issue" / "repro.sh").read_text()
    assert "--scenario" in script


def test_repro_without_scenario_has_honest_timeline_note(fail_run, capsys):
    assert report(str(fail_run)) == 0
    script = (fail_run / "issue" / "repro.sh").read_text()
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
    script = (fail_run / "issue" / "repro.sh").read_text()
    digest = hashlib.sha256((SCENARIO / "repro.sh").read_bytes()).hexdigest()
    assert f"# --- provided by the scenario author (sha256 {digest}) ---" in script
    assert "max_allowed_usages\\\":1" in script
    assert "LiteTraffic does not record request bodies" not in script
    assert (fail_run / "issue" / "scenario" / "journeys.js").is_file()


# --- submit: public route ----------------------------------------------------------------------


def test_submit_requires_repo(fail_run, capsys, gh):
    assert report(str(fail_run), "--submit") == 3
    assert "--submit needs --repo" in capsys.readouterr().out
    assert gh.calls() == []


@pytest.mark.parametrize("repo", ["owner", "owner/app/extra", "-x/app", "owner/app;rm", "https://github.com/owner/app"])
def test_submit_rejects_malformed_repo(fail_run, capsys, gh, repo):
    assert report(str(fail_run), "--submit", "--repo", repo) == 3
    assert "--repo must be OWNER/NAME" in capsys.readouterr().out
    assert gh.calls() == []


def test_gh_missing_clear_error(fail_run, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
    assert "Install GitHub CLI" in capsys.readouterr().out


def test_gh_unauthenticated_clear_error(fail_run, capsys, gh):
    gh.respond(["auth", "status"], exit=1, stderr="You are not logged into any GitHub hosts.")
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
    assert "gh is not authenticated" in capsys.readouterr().out
    assert not any("create" in argv for argv in gh.argvs())


def test_submit_creates_issue_with_body_file_and_repo_flag(fail_run, runs, capsys, gh, monkeypatch):
    monkeypatch.setenv("GH_REPO", "evil/elsewhere")
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 0
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
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
    out = capsys.readouterr().out
    assert "already reported: https://github.com/owner/app/issues/3" in out and "--comment" in out
    assert not any(argv[:2] == ["issue", "create"] and "--help" not in argv for argv in gh.argvs())
    search = next(argv for argv in gh.argvs() if argv[:2] == ["search", "issues"])
    assert fingerprint in search and "owner/app" in search


def test_duplicate_found_via_rest_list_when_search_lags(fail_run, runs, capsys, gh):
    fingerprint = issue.fingerprint(issue.load_run(str(fail_run), runs))
    line = json.dumps({"number": 5, "url": "https://github.com/owner/app/issues/5", "body": f"litetraffic-fingerprint: {fingerprint}"})
    gh.respond(["api", "repos/owner/app/issues"], stdout=line + "\n")
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
    assert "already reported: https://github.com/owner/app/issues/5" in capsys.readouterr().out
    listing = next(argv for argv in gh.argvs() if "repos/owner/app/issues" in argv)
    assert "creator=tester" in listing


def test_duplicate_with_comment_adds_comment(fail_run, runs, capsys, gh):
    fingerprint = issue.fingerprint(issue.load_run(str(fail_run), runs))
    existing = [{"number": 3, "url": "https://github.com/owner/app/issues/3", "body": f"litetraffic-fingerprint: {fingerprint}"}]
    gh.respond(["search", "issues"], stdout=json.dumps(existing))
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--comment") == 0
    comment = next(call for call in gh.calls() if call["argv"][:2] == ["issue", "comment"])
    assert comment["argv"][2] == "https://github.com/owner/app/issues/3"
    assert comment["argv"][comment["argv"].index("-F") + 1].endswith("comment.md")
    assert FAIL_ID in comment["files"]["-F"]
    assert not any(argv[:2] == ["issue", "create"] and "--help" not in argv for argv in gh.argvs())


def test_ledger_blocks_refile(fail_run, runs, capsys, gh):
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 0
    capsys.readouterr()
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
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
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 0
    out = capsys.readouterr().out
    assert "--attach" not in _create_argv(gh)
    assert f"drag {fail_run / 'issue' / 'proof.mp4'} into {CREATED}" in out


def test_attach_used_with_write_permission_and_supported_gh(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 0
    create = next(call for call in gh.calls() if call["argv"][:2] == ["issue", "create"] and "--help" not in call["argv"])
    assert create["argv"][create["argv"].index("--attach") + 1] == "./proof.mp4"
    assert create["cwd"] == str((fail_run / "issue").resolve())
    assert "![LiteTraffic replay](./proof.mp4)" in create["files"]["-F"]
    assert "drag" not in capsys.readouterr().out


def test_partial_attach_failure_reports_url_and_drag_hint_exit_0(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["issue", "create", "-R"], stdout=CREATED + "\n", stderr="failed to upload proof.mp4: 422", exit=1)
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 0
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
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--label", "bug", "--label", "promo") == 0
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
    result = draft_json(capsys, str(fail_run), "--submit", "--repo", "owner/app", "--remove-local")
    assert result["removed"] is True and result["url"] == CREATED
    assert not fail_run.exists()
    assert (runs / "issues.jsonl").exists()


def test_remove_local_deletes_run_with_no_video(fail_run, runs, capsys, gh):
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--remove-local") == 0
    assert not fail_run.exists()


def test_remove_local_keeps_run_when_video_not_uploaded(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["repo", "view"], stdout="READ\n")
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--remove-local") == 0
    out = capsys.readouterr().out
    assert fail_run.exists() and f"kept {fail_run}" in out and "litetraffic prune" in out


def test_remove_local_with_attach_url_deletes(fail_run, capsys, gh, monkeypatch):
    fake_video(monkeypatch)
    gh.respond(["repo", "view"], stdout="READ\n")
    assert report(str(fail_run), "--submit", "--repo", "owner/app", "--remove-local", "--attach-url", "https://ci.example.test/a/1") == 0
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
    assert report(str(fail_run), "--submit", "--repo", "owner/app") == 3
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
