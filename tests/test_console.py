"""The console API: a scenario library, background runs and AI drafting, all over the dashboard's loopback server."""

import json
import sys
import types
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from litetraffic import console, dashboard

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = {path.name: path.read_text() for path in (ROOT / "examples" / "checkout").iterdir() if path.name in {"manifest.json", "journeys.js"}}
KIT = json.loads((ROOT / "examples" / "tenant_api" / "kit.json").read_text())


def _command_bundle():
    manifest = json.loads(CHECKOUT["manifest.json"])
    manifest["budgets"]["max_seconds"] = 120
    manifest["fixtures"] = {"recipe": "x", "command": {"setup": ["sh", "setup.sh"], "teardown": ["sh", "teardown.sh"], "setup_timeout_seconds": 5, "teardown_timeout_seconds": 5}}
    return {"manifest.json": json.dumps(manifest), "journeys.js": CHECKOUT["journeys.js"], "setup.sh": "echo '{}'", "teardown.sh": "true"}


# A stand-in for `litetraffic verify`: makes a run dir like the real one, prints the JSON verdict, honours SIGINT.
FAKE_VERIFY = textwrap.dedent(
    """
    import json, os, signal, sys, time
    args = sys.argv[1:]
    out = args[args.index("--output-dir") + 1]
    seed = int(args[args.index("--seed") + 1])
    mode = os.environ.get("FAKE_MODE", "pass")
    run_id = "run_fake_%d" % seed
    run = os.path.join(out, run_id)
    os.makedirs(run, exist_ok=True)
    json.dump({"run_id": run_id, "scenario": "checkout", "lifecycle": "running", "seed": seed}, open(os.path.join(run, "run.json"), "w"))
    print("starting", flush=True)
    sys.stderr.write("secret is " + os.environ.get("MY_TOKEN", "") + "\\n"); sys.stderr.flush()
    if mode == "hang":
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            json.dump({"run_id": run_id, "scenario": "checkout", "lifecycle": "cancelled", "verdict": "inconclusive"}, open(os.path.join(run, "result.json"), "w"))
            print(json.dumps({"run_id": run_id, "verdict": "inconclusive", "lifecycle": "cancelled"}))
            sys.exit(130)
    verdict = "fail" if mode == "fail" else "pass"
    json.dump({"run_id": run_id, "scenario": "checkout", "lifecycle": "finished", "verdict": verdict}, open(os.path.join(run, "result.json"), "w"))
    print(json.dumps({"run_id": run_id, "verdict": verdict, "lifecycle": "finished"}))
    sys.exit(0 if verdict == "pass" else 1)
    """
)


@pytest.fixture
def served(tmp_path, monkeypatch):
    runs, library = tmp_path / "runs", tmp_path / "library"
    runs.mkdir()
    fake = tmp_path / "fake_verify.py"
    fake.write_text(FAKE_VERIFY)
    monkeypatch.setattr(console, "verify_argv", lambda scenario, args: [sys.executable, str(fake), *args])
    server = dashboard.make_server(runs, 0, library_dir=library)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield type("Served", (), {"base": f"http://127.0.0.1:{server.server_address[1]}", "runs": runs, "library": library, "server": server})
    server.shutdown()
    server.server_close()


def call(served, method, path, body=None, action="x", headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(served.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if action:
        request.add_header(dashboard.ACTION_HEADER, action)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def save(served, name="checkout", files=None, **extra):
    return call(served, "POST", "/api/library", {"name": name, "files": CHECKOUT if files is None else files, **extra}, "library-save")


def wait_job(served, job_id, states=("finished", "failed", "cancelled"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, job = call(served, "GET", f"/api/jobs/{job_id}")
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck: {job}")


# ---- library --------------------------------------------------------------------------------------------------------


def test_library_starts_empty_and_meta_describes_the_console(served):
    assert call(served, "GET", "/api/library") == (200, [])
    status, meta = call(served, "GET", "/api/meta")
    assert meta["console"]["library_dir"] == str(served.library.resolve())
    assert meta["console"]["busy_job"] is None
    assert set(meta["console"]["ai"]) == {"available", "model"}


def test_save_list_get_delete_round_trip(served):
    status, saved = save(served)
    assert status == 201 and saved["name"] == "checkout" and saved["has_command"] is False and len(saved["digest"]) == 64
    status, listing = call(served, "GET", "/api/library")
    [entry] = listing
    assert entry["name"] == "checkout" and entry["digest"] == saved["digest"] and entry["planned_journeys"] > 0
    assert "one_effect_per_payment" in entry["assertions"] and entry["has_command"] is False and entry["error"] is None
    status, detail = call(served, "GET", "/api/library/checkout")
    assert status == 200 and detail["files"]["journeys.js"] == CHECKOUT["journeys.js"]
    assert detail["inspect"]["ok"] is True and detail["inspect"]["scenario_sha256"] == saved["digest"]
    assert call(served, "POST", "/api/library", {"name": "checkout", "files": CHECKOUT}, "library-save")[0] == 409
    assert save(served, overwrite=True)[0] == 201
    assert call(served, "DELETE", "/api/library/checkout", action="library-delete") == (200, {"deleted": "checkout"})
    assert call(served, "GET", "/api/library/checkout")[0] == 404
    assert call(served, "GET", "/api/library") == (200, [])


@pytest.mark.parametrize("name", ["", "Up", "-x", "a/b", "..", "a b", "x" * 64, ".hidden"])
def test_bad_names_are_rejected(served, name):
    status, body = save(served, name=name)
    assert status == 422 and "name" in body["error"]
    assert not served.library.exists() or not list(served.library.iterdir())


@pytest.mark.parametrize(
    "files",
    [
        {"manifest.json": "{}"},  # invalid manifest
        {**CHECKOUT, "../evil.js": "x"},
        {**CHECKOUT, "/abs.js": "x"},
        {**CHECKOUT, "a/../../b.js": "x"},
        {**CHECKOUT, ".hidden": "x"},
        {**CHECKOUT, "n\u0000ul": "x"},
        {},
    ],
)
def test_invalid_or_hostile_bundles_are_rejected_and_leave_nothing(served, files):
    status, body = save(served, files=files)
    assert status == 422 and body["error"]
    assert call(served, "GET", "/api/library") == (200, [])
    assert not (served.library.parent / "evil.js").exists()


def test_oversized_bundle_is_rejected(served):
    status, body = save(served, files={**CHECKOUT, "big.js": "x" * (console.MAX_BUNDLE_BYTES + 1)})
    assert status == 422 and "too large" in body["error"]


def test_a_bundle_with_a_setup_command_needs_explicit_trust(served):
    files = _command_bundle()
    status, body = save(served, files=files)
    assert status == 400 and body["needs_trust"] is True and body["command"] == ["sh", "setup.sh"]
    assert call(served, "GET", "/api/library") == (200, [])
    status, saved = save(served, files=files, trust_command=True)
    assert status == 201 and saved["has_command"] is True
    assert call(served, "GET", "/api/library")[1][0]["has_command"] is True


def test_kit_generates_a_validated_bundle(served):
    status, saved = call(served, "POST", "/api/library/kit", {"name": "tenants", "config": KIT}, "library-kit")
    assert status == 201 and saved["name"] == "tenants"
    detail = call(served, "GET", "/api/library/tenants")[1]
    assert {"manifest.json", "journeys.js"} <= set(detail["files"]) and detail["inspect"]["ok"]
    status, body = call(served, "POST", "/api/library/kit", {"name": "bad", "config": {"name": "x"}}, "library-kit")
    assert status == 422 and "invalid kit config" in body["error"]


def test_a_saved_bundle_that_later_breaks_is_reported_not_fatal(served):
    save(served)
    (served.library / "checkout" / "manifest.json").write_text("{not json")
    [entry] = call(served, "GET", "/api/library")[1]
    assert entry["name"] == "checkout" and entry["error"] and entry["digest"] is None
    assert call(served, "GET", "/api/library/checkout")[0] == 200


def test_library_ignores_symlinks_and_strays(served):
    save(served)
    outside = served.library.parent / "outside"
    outside.mkdir()
    (served.library / "link").symlink_to(outside)
    (served.library / "stray.txt").write_text("x")
    assert [entry["name"] for entry in call(served, "GET", "/api/library")[1]] == ["checkout"]
    assert call(served, "GET", "/api/library/link")[0] == 404
    assert call(served, "DELETE", "/api/library/link", action="library-delete")[0] == 404
    assert outside.exists()


# ---- write guard ----------------------------------------------------------------------------------------------------


def test_writes_need_the_action_header_and_a_same_origin_request(served):
    assert call(served, "POST", "/api/library", {"name": "a", "files": CHECKOUT}, action=None)[0] == 403
    assert call(served, "POST", "/api/library", {"name": "a", "files": CHECKOUT}, "wrong")[0] == 403
    assert call(served, "POST", "/api/library", {"name": "a", "files": CHECKOUT}, "library-save", {"Origin": "http://evil.example"})[0] == 403
    assert call(served, "DELETE", "/api/library/a", action=None)[0] == 403
    assert call(served, "POST", "/api/jobs", {}, action=None)[0] == 403
    assert call(served, "GET", "/api/library")[1] == []


def test_bad_json_bodies_are_a_clean_422(served):
    request = urllib.request.Request(served.base + "/api/library", data=b"{nope", method="POST")
    request.add_header(dashboard.ACTION_HEADER, "library-save")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request)
    assert excinfo.value.code == 422
    assert call(served, "POST", "/api/library", ["not", "an", "object"], "library-save")[0] == 422


def test_dns_rebinding_host_is_refused_for_writes(served):
    request = urllib.request.Request(served.base + "/api/library", data=b"{}", method="POST", headers={"Host": "evil.example", dashboard.ACTION_HEADER: "library-save"})
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request)
    assert excinfo.value.code == 421


# ---- jobs -----------------------------------------------------------------------------------------------------------


def run_body(**changes):
    return {"scenario": "checkout", "target": "http://127.0.0.1:9", "seed": 3, "confirm_target": True} | changes


def test_a_run_goes_from_running_to_finished_and_links_the_run(served):
    save(served)
    status, started = call(served, "POST", "/api/jobs", run_body(), "job-run")
    assert status == 202
    job = wait_job(served, started["id"])
    assert job["state"] == "finished" and job["verdict"] == "pass" and job["exit_code"] == 0
    assert job["run_id"] == "run_fake_3" and job["seed"] == 3 and job["scenario"] == "checkout" and job["kind"] == "run"
    assert "starting" in job["log"]
    assert any(line.startswith("run run_fake_3:") for line in job["log"])  # lifecycle stages are narrated
    assert call(served, "GET", f"/api/runs/{job['run_id']}")[0] == 200
    assert [item["id"] for item in call(served, "GET", "/api/jobs")[1]] == [started["id"]]
    assert "log" not in call(served, "GET", "/api/jobs")[1][0]
    assert call(served, "GET", "/api/meta")[1]["console"]["busy_job"] is None


@pytest.mark.parametrize(("mode", "verdict", "code"), [("fail", "fail", 1)])
def test_a_failing_verdict_is_a_finished_job(served, mode, verdict, code):
    save(served)
    job = wait_job(served, call(served, "POST", "/api/jobs", run_body(env={"FAKE_MODE": mode}), "job-run")[1]["id"])
    assert (job["state"], job["verdict"], job["exit_code"]) == ("finished", verdict, code)


def test_secret_values_reach_the_process_but_never_the_api(served):
    save(served)
    status, started = call(served, "POST", "/api/jobs", run_body(env={"MY_TOKEN": "hunter2hunter2"}), "job-run")
    job = wait_job(served, started["id"])
    assert job["env_names"] == ["MY_TOKEN"]
    assert "hunter2hunter2" not in json.dumps(call(served, "GET", "/api/jobs")) + json.dumps(job)
    assert "secret is [redacted]" in job["log"]


@pytest.mark.parametrize("name", ["PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "PYTHONPATH", "HOME", "NODE_OPTIONS", "K6_CLOUD_TOKEN", "lower", "1BAD", "A-B"])
def test_dangerous_or_malformed_env_names_are_refused(served, name):
    save(served)
    status, body = call(served, "POST", "/api/jobs", run_body(env={name: "valuevalue"}), "job-run")
    assert status == 422 and "environment variable" in body["error"]


@pytest.mark.parametrize(
    "changes",
    [
        {"confirm_target": False},
        {"confirm_target": None},
        {"target": "ftp://x"},
        {"target": "http://169.254.169.254/"},
        {"target": "http://user:pw@127.0.0.1/"},
        {"seed": -1},
        {"seed": "x"},
        {"repeat": 0},
        {"repeat": 21},
        {"scenario": "missing"},
        {"scenario": "../etc"},
        {"capture": "rm -rf /"},
        {"env": ["x"]},
    ],
)
def test_bad_run_requests_are_refused_before_anything_starts(served, changes):
    save(served)
    status, body = call(served, "POST", "/api/jobs", run_body(**changes), "job-run")
    assert status in {404, 422} and body["error"]
    assert call(served, "GET", "/api/jobs") == (200, [])


def test_only_one_run_at_a_time_and_cancel_ends_it(served):
    save(served)
    status, started = call(served, "POST", "/api/jobs", run_body(env={"FAKE_MODE": "hang"}), "job-run")
    assert status == 202
    deadline = time.time() + 10
    while time.time() < deadline and not call(served, "GET", f"/api/jobs/{started['id']}")[1]["run_id"]:
        time.sleep(0.05)
    assert call(served, "GET", f"/api/jobs/{started['id']}")[1]["run_id"] == "run_fake_3"  # known while still running
    assert call(served, "GET", "/api/meta")[1]["console"]["busy_job"] == started["id"]
    status, busy = call(served, "POST", "/api/jobs", run_body(seed=4), "job-run")
    assert status == 409 and busy["job_id"] == started["id"]
    assert call(served, "POST", f"/api/jobs/{started['id']}/cancel", {}, "job-cancel")[0] == 200
    job = wait_job(served, started["id"])
    assert job["state"] == "cancelled" and job["exit_code"] == 130
    assert call(served, "POST", f"/api/jobs/{started['id']}/cancel", {}, "job-cancel")[0] == 200  # idempotent
    assert call(served, "POST", "/api/jobs", run_body(seed=5), "job-run")[0] == 202


def test_a_crashing_runner_is_a_failed_job_with_its_output(served, monkeypatch, tmp_path):
    crash = tmp_path / "crash.py"
    crash.write_text("import sys; print('boom on stderr', file=sys.stderr); sys.exit(3)")
    monkeypatch.setattr(console, "verify_argv", lambda scenario, args: [sys.executable, str(crash)])
    save(served)
    job = wait_job(served, call(served, "POST", "/api/jobs", run_body(), "job-run")[1]["id"])
    assert job["state"] == "failed" and job["exit_code"] == 3 and "boom on stderr" in job["log"]


def test_unknown_job_is_404(served):
    assert call(served, "GET", "/api/jobs/nope")[0] == 404
    assert call(served, "POST", "/api/jobs/nope/cancel", {}, "job-cancel")[0] == 404


def test_a_scenario_with_a_command_fixture_runs_only_if_it_was_trusted(served):
    save(served, name="cmd", files=_command_bundle(), trust_command=True)
    assert call(served, "POST", "/api/jobs", run_body(scenario="cmd"), "job-run")[0] == 202
    (served.library / "cmd" / "setup.sh").write_text("echo changed")  # edited on disk after trust was given
    wait_job(served, call(served, "GET", "/api/jobs")[1][0]["id"])
    status, body = call(served, "POST", "/api/jobs", run_body(scenario="cmd", seed=9), "job-run")
    assert status == 422 and "changed since you approved" in body["error"]


# ---- real k6 end to end ---------------------------------------------------------------------------------------------


@pytest.mark.real_k6
def test_a_real_run_through_the_console_matches_the_cli(tmp_path):
    import subprocess

    runs, library = tmp_path / "runs", tmp_path / "library"
    runs.mkdir()
    port = 18765
    app = subprocess.Popen([sys.executable, str(ROOT / "examples" / "checkout" / "server.py"), "--port", str(port)])
    server = dashboard.make_server(runs, 0, library_dir=library)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    served = type("Served", (), {"base": f"http://127.0.0.1:{server.server_address[1]}"})
    try:
        time.sleep(1)
        assert save(served)[0] == 201
        status, started = call(served, "POST", "/api/jobs", run_body(target=f"http://127.0.0.1:{port}", seed=42), "job-run")
        assert status == 202
        job = wait_job(served, started["id"], timeout=120)
        assert job["state"] == "finished" and job["verdict"] == "pass" and job["exit_code"] == 0
        detail = call(served, "GET", f"/api/runs/{job['run_id']}")[1]
        assert detail["result"]["verdict"] == "pass" and detail["run"]["seed"] == 42
    finally:
        app.terminate()
        server.shutdown()
        server.server_close()


# ---- AI drafting ----------------------------------------------------------------------------------------------------


class FakeClaude:
    """An anthropic client stand-in that replays canned replies and records every prompt."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        block = type("Block", (), {"type": "text", "text": text})
        usage = type("Usage", (), {"input_tokens": 1000, "output_tokens": 200})
        return type("Reply", (), {"content": [block], "usage": usage, "stop_reason": "end_turn"})


def reply(files):
    return f"```json\n{files['manifest.json']}\n```\n```javascript\n{files['journeys.js']}\n```"


def draft_body(**changes):
    return {"name": "shop", "app_description": "an online shop", "rules": "never oversell stock", "spec_text": "openapi: 3.0.0", "api_key": "fake-draft-key-for-tests"} | changes


@pytest.fixture
def claude(monkeypatch):
    holder = {}

    def factory(api_key):
        holder["api_key"] = api_key
        return holder["client"]

    monkeypatch.setattr(console, "anthropic_client", factory)
    return holder


def test_a_draft_job_repairs_its_own_invalid_bundle_and_returns_files(served, claude):
    claude["client"] = FakeClaude(["no code blocks here", reply(CHECKOUT)])
    status, started = call(served, "POST", "/api/draft", draft_body(), "draft-start")
    assert status == 202
    job = wait_job(served, started["id"])
    assert job["kind"] == "draft" and job["state"] == "finished"
    result = job["result"]
    assert result["valid"] is True and result["attempts"] == 2 and result["tokens"] == {"input": 2000, "output": 400}
    assert set(result["files"]) == {"manifest.json", "journeys.js"} and result["name"] == "shop" and result["error"] is None
    assert any("attempt 1" in line for line in job["log"]) and any("valid" in line for line in job["log"])
    assert claude["api_key"] == "fake-draft-key-for-tests"
    first = claude["client"].calls[0]
    prompt = json.dumps(first["messages"]) + json.dumps(first.get("system"))
    assert "never oversell stock" in prompt and "openapi: 3.0.0" in prompt and "inspect" not in first["messages"][-1]["content"][:0]
    assert "fake-draft-key" not in json.dumps(job)
    assert call(served, "GET", "/api/library") == (200, [])  # drafts are reviewed and saved by the human, never auto-saved


def test_a_draft_that_never_validates_reports_the_last_error_and_files(served, claude):
    claude["client"] = FakeClaude(["```json\n{}\n```\n```javascript\nexport default function(){}\n```"] * console.MAX_DRAFT_ATTEMPTS)
    job = wait_job(served, call(served, "POST", "/api/draft", draft_body(), "draft-start")[1]["id"])
    assert job["state"] == "finished" and job["result"]["valid"] is False and job["result"]["error"]
    assert job["result"]["attempts"] == console.MAX_DRAFT_ATTEMPTS and "manifest.json" in job["result"]["files"]


def test_a_draft_may_not_smuggle_in_a_command_fixture(served, claude):
    files = _command_bundle()
    claude["client"] = FakeClaude([reply(files)] * console.MAX_DRAFT_ATTEMPTS)
    job = wait_job(served, call(served, "POST", "/api/draft", draft_body(), "draft-start")[1]["id"])
    assert job["result"]["valid"] is False and "command" in job["result"]["error"]


def test_draft_inputs_are_checked(served, claude, monkeypatch):
    claude["client"] = FakeClaude([])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert call(served, "POST", "/api/draft", draft_body(api_key=None), "draft-start")[0] == 422
    assert "API key" in call(served, "POST", "/api/draft", draft_body(api_key=None), "draft-start")[1]["error"]
    assert call(served, "POST", "/api/draft", draft_body(rules=" "), "draft-start")[0] == 422
    assert call(served, "POST", "/api/draft", draft_body(name="Bad Name"), "draft-start")[0] == 422
    assert call(served, "POST", "/api/draft", draft_body(spec_text="x" * (console.MAX_SPEC_BYTES + 1)), "draft-start")[0] == 422
    assert call(served, "POST", "/api/draft", draft_body(spec_url="http://169.254.169.254/x", spec_text=None), "draft-start")[0] == 422
    assert call(served, "GET", "/api/jobs") == (200, [])


def test_the_server_key_is_used_when_the_request_has_none(served, claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-env-key-for-tests")
    monkeypatch.setitem(sys.modules, "anthropic", types.ModuleType("anthropic"))  # CI has no SDK
    claude["client"] = FakeClaude([reply(CHECKOUT)])
    assert call(served, "GET", "/api/meta")[1]["console"]["ai"]["available"] is True
    wait_job(served, call(served, "POST", "/api/draft", draft_body(api_key=None), "draft-start")[1]["id"])
    assert claude["api_key"] == "fake-env-key-for-tests"


def test_the_authoring_guide_shipped_with_the_package_matches_the_docs():
    shipped = (ROOT / "src" / "litetraffic" / "authoring" / "scenarios.md").read_text()
    assert shipped == (ROOT / "docs" / "scenarios.md").read_text()


def test_cancel_works_even_when_the_console_was_started_with_sigint_ignored():
    import signal
    import subprocess

    child = subprocess.Popen(
        [sys.executable, "-c", console.SIGINT_BOOT + "import time; print('up', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
    )  # fmt: skip
    assert child.stdout.readline().strip() == "up"
    for _ in range(3):  # a slow CI runner can swallow the first signal; the handler must still act on a later one
        child.send_signal(signal.SIGINT)
        try:
            assert child.wait(timeout=4) != 0
            return
        except subprocess.TimeoutExpired:
            continue
    child.kill()
    raise AssertionError("SIGINT never stopped the child")


def test_a_cancel_that_arrives_after_the_run_ended_reports_the_real_verdict(served):
    save(served)
    started = call(served, "POST", "/api/jobs", run_body(), "job-run")[1]
    job = wait_job(served, started["id"])
    assert call(served, "POST", f"/api/jobs/{started['id']}/cancel", {}, "job-cancel") == (200, {"state": "finished"})
    assert job["state"] == "finished" and job["verdict"] == "pass"
