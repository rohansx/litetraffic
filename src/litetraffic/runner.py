from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from litetraffic.artifacts import MANIFEST, artifact_files
from litetraffic.auth import AuthError, mint_tokens, redact, redact_value, secret_values
from litetraffic.engine import SUPPORTED_K6_VERSION, RunnerError, _engine, _target, k6_command  # noqa: F401 (re-exported)
from litetraffic.evidence import _read_events, _read_metrics, budget_overruns, evaluate_assertions, overlap_shortfalls, target_unreachable
from litetraffic.fixture import cleanup_fixture, create_fixture, fixture_json, fixture_pool, run_fixture_command
from litetraffic.observation import observe, sent_request
from litetraffic.process import _communicate, _stop_process
from litetraffic.report import render_report
from litetraffic.scenario import ScenarioError, load_scenario, staged

K6_THRESHOLDS_FAILED = 99  # k6 exit code: the run completed but a threshold was crossed


def _now() -> datetime:
    return datetime.now(UTC)


def _write_bytes(path: Path, data: bytes) -> None:
    # mkstemp creates the file 0600 before any content lands; os.replace swaps it in whole or not at all.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _write_text(path: Path, text: str) -> None:
    _write_bytes(path, text.encode("utf-8"))


def _write_json(path: Path, value: object) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _record_stage(run_dir: Path, run: dict, stage: str) -> None:
    # run.json tracks progress as it happens, so an interrupted host still shows where the run stopped.
    run["lifecycle"] = stage
    _write_json(run_dir / "run.json", run)


def _restrict(run_dir: Path) -> None:
    # k6 creates files with its own (umask) mode; skip symlinks, chmod would follow them out of the run dir.
    for path in [run_dir, *run_dir.rglob("*")]:
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)


def verify(
    target: str,
    scenario: Path,
    output_dir: Path,
    k6_path: str | None = None,
    seed: int = 0,
) -> dict:
    owed: dict = {}  # fixture teardown/cleanup not yet run; runs even when the run raises
    try:
        return _verify(target, scenario, output_dir, k6_path, seed, owed)
    finally:
        for release in list(owed.values()):
            release()


def _verify(target: str, scenario: Path, output_dir: Path, k6_path: str | None, seed: int, owed: dict) -> dict:
    target = _target(target)
    bundle = load_scenario(Path(scenario))
    executable, engine_version = _engine(k6_path)
    resolved_schedule = bundle.manifest.schedule.resolve(seed)
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = Path(output_dir).resolve() / run_id
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True, mode=0o700)
    run_dir.chmod(0o700)

    started_at = _now()
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario": bundle.manifest.name,
        "mode": "verify",
        "target": target,
        "seed": seed,
        "scenario_sha256": bundle.digest,
        "engine": engine_version,
        "lifecycle": "preparing",
        "resolved_schedule": [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule],
        "started_at": started_at.isoformat(),
    }
    _write_json(run_dir / "run.json", run)
    _write_json(run_dir / "scenario.lock.json", {"manifest": bundle.manifest_data, "engine": engine_version, "files": bundle.files})

    console_path = run_dir / "console.log"
    metrics_path = run_dir / "metrics.jsonl"
    command = k6_command(executable, console_path, metrics_path)
    environment = os.environ.copy()
    for name in [name for name in environment if name in ("LT_FIXTURE_ID", "LT_FIXTURE_JSON", "LT_FIXTURE_POOL_JSON") or name.startswith("LT_TOKEN_")]:
        environment.pop(name)
    environment.update({"LT_RUN_ID": run_id, "LT_TARGET": target, "LT_SEED": str(seed)})
    hook_environment = dict(environment)  # command fixtures see only the run identity, not the schedule
    environment.update(
        {
            "LT_MAX_IN_FLIGHT": str(bundle.manifest.budgets.max_in_flight),
            "LT_SCHEDULE_JSON": json.dumps(
                [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule]
            ),
        }
    )
    lifecycle = "running"
    engine_error = ""
    tokens = {}
    try:  # a missing signing secret stops the run before any fixture work or traffic
        tokens = mint_tokens(bundle.manifest.actors, run_id, environment)
    except AuthError as exc:
        lifecycle, engine_error = "crashed", str(exc)
    environment.update(tokens)
    secrets = secret_values(bundle.manifest, environment, tokens)
    process: subprocess.Popen[str] | None = None
    engine_started = engine_finished = None
    fixture = hooks = None
    commands = bundle.manifest.fixtures.command
    if commands and lifecycle == "running":
        owed["teardown"] = lambda: run_fixture_command(
            "teardown", commands.teardown, bundle.root, hook_environment, commands.timeout_seconds, secrets
        )
        setup, setup_stdout = run_fixture_command("setup", commands.setup, bundle.root, hook_environment, commands.timeout_seconds, secrets)
        hooks = {"setup": setup}
        if fixture_json(setup_stdout):
            environment["LT_FIXTURE_JSON"] = hook_environment["LT_FIXTURE_JSON"] = fixture_json(setup_stdout)
        lifecycle = {"ok": "running", "cancelled": "cancelled"}.get(setup["status"], "crashed")
        engine_error = setup.get("reason", "")
    if lifecycle == "running":
        try:
            pool_json = fixture_pool(bundle.pool, environment.get("LT_FIXTURE_JSON"), bundle.manifest.planned_journeys)
        except ScenarioError as exc:
            lifecycle, engine_error = "crashed", str(exc)
        else:
            if pool_json:
                environment["LT_FIXTURE_POOL_JSON"] = pool_json
    if bundle.manifest.fixtures.owned_http and lifecycle == "running":
        try:
            fixture = {"create": create_fixture(target, bundle.manifest.fixtures.owned_http, run_id)}
        except KeyboardInterrupt:
            # ponytail: an id-less create cannot be cleaned up; at most one request was sent.
            fixture = {"create": {"status": "cancelled", "reason": "fixture create cancelled", "requests": 1}}
            lifecycle = "cancelled"
        if fixture["create"]["status"] == "created":
            environment["LT_FIXTURE_ID"] = fixture["create"]["fixture_id"]
            owed["cleanup"] = lambda: cleanup_fixture(target, bundle.manifest.fixtures.owned_http, run_id, fixture["create"]["fixture_id"])
        elif lifecycle != "cancelled":
            lifecycle = "crashed"
            engine_error = fixture["create"]["reason"]
    if lifecycle != "running":
        stdout, stderr = "", engine_error
    else:
        _record_stage(run_dir, run, "running")
        try:
            engine_seconds = bundle.manifest.budgets.max_seconds - 5 * len(bundle.manifest.observations) - bundle.manifest.fixtures.reserved_seconds
            engine_started = _now()
            # k6 runs a staged copy so the bundled runtime helper sits next to the script.
            with staged(bundle) as root:
                process = subprocess.Popen(
                    [*command, str(root / bundle.script_path.relative_to(bundle.root))],
                    cwd=root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=os.name == "posix",
                )
                try:
                    stdout, stderr = _communicate(process, timeout=engine_seconds)
                    lifecycle = "finished" if process.returncode in (0, K6_THRESHOLDS_FAILED) else "crashed"
                except subprocess.TimeoutExpired:
                    lifecycle = "timed_out"
                    stdout, stderr = _stop_process(process)
                except KeyboardInterrupt:
                    lifecycle = "cancelled"
                    stdout, stderr = _stop_process(process)
        except OSError as exc:
            lifecycle = "crashed"
            engine_error = str(exc)
            stdout, stderr = "", str(exc)
        engine_finished = _now()

    engine_exit_code = process.returncode if process is not None else None
    _write_text(run_dir / "engine.stdout.log", redact(stdout, secrets))
    _write_text(run_dir / "engine.stderr.log", redact(stderr, secrets))
    for path in (console_path, metrics_path) if secrets else ():
        if path.is_file():  # k6 writes these itself; scrub any token or secret a script printed
            path.write_text(redact(path.read_text(errors="replace"), secrets))

    events, malformed_events = _read_events(console_path, run_id)
    metrics, malformed_metrics = _read_metrics(metrics_path, bundle.manifest.journeys)
    events_path = events_dir / "000001.jsonl"
    _write_text(events_path, "".join(json.dumps(event, sort_keys=True) + "\n" for event in redact_value(events, secrets)))

    _record_stage(run_dir, run, "observing")
    observations, engine_finished_ok = [], lifecycle == "finished"
    for config in bundle.manifest.observations:  # sequentially, each after k6 has finished
        record = {"assertion": config.assertion, "status": "unknown", "reason": "engine did not finish", "requested": False}
        if engine_finished_ok and lifecycle == "cancelled":
            record["reason"] = "observation cancelled"
        elif engine_finished_ok:
            try:
                record = observe(
                    target,
                    config,
                    run_id,
                    fixture_id=environment.get("LT_FIXTURE_ID"),
                    variables={"planned_journeys": bundle.manifest.planned_journeys, "seed": seed},
                    environ=environment,  # includes the LT_TOKEN_<CLASS> tokens minted for this run
                    allowed_origins=bundle.manifest.allowed_origins,
                    allowed_origins_env=bundle.manifest.allowed_origins_env,
                )
                record["requested"] = sent_request(record)
            except KeyboardInterrupt:
                lifecycle = "cancelled"
                record.update(reason="observation cancelled", requested=True)
        observations.append(record)
    if observations:
        metrics["observer_requests"] = sum(record.pop("requested") for record in observations)
        # The legacy single `observation` keeps its single-object observation.json.
        _write_json(run_dir / "observation.json", redact_value(observations[0] if bundle.manifest.observation else observations, secrets))
    if fixture:
        if fixture["create"]["status"] == "created":
            fixture_id = fixture["create"]["fixture_id"]
            try:
                fixture["cleanup"] = owed.pop("cleanup")()
            except KeyboardInterrupt:
                lifecycle = "cancelled"
                fixture["cleanup"] = {"status": "error", "reason": f"fixture cleanup cancelled; fixture {fixture_id} may remain", "requests": 1}
        _write_json(run_dir / "fixture.json", redact_value(fixture, secrets))
        metrics["fixture_requests"] = fixture["create"]["requests"] + fixture.get("cleanup", {}).get("requests", 0)
    if hooks:
        # Teardown runs whatever happened before it, including a failed or cancelled setup.
        hooks["teardown"], _ = owed.pop("teardown")()
        if hooks["teardown"]["status"] == "cancelled":
            lifecycle = "cancelled"
            hooks["teardown"].update(status="error", reason="fixture teardown cancelled; fixture state may remain")
        _write_json(run_dir / "fixture.json", redact_value(hooks, secrets))
    # Fixture create (POST) and cleanup (DELETE) are writes; the observer only reads.
    metrics["write_attempts"] = metrics.get("write_attempts", 0) + metrics.get("fixture_requests", 0)
    if fixture or observations:
        metrics["total_http_reqs"] = float(metrics.get("http_reqs", 0)) + metrics.get("observer_requests", 0) + metrics.get("fixture_requests", 0)
    _record_stage(run_dir, run, "finalizing")
    finished = _now()
    finished_at = finished.isoformat()
    run.update({"finished_at": finished_at, "engine_exit_code": engine_exit_code})
    if engine_started:
        run.update({"engine_started_at": engine_started.isoformat(), "engine_finished_at": engine_finished.isoformat()})
    metrics["elapsed_seconds"] = round(max((finished - started_at).total_seconds(), 0.000001), 6)
    # Rates cover only the engine window: fixture setup, observation and cleanup are not load.
    engine_window = max((engine_finished - engine_started).total_seconds(), 0.000001) if engine_started else 0.000001
    metrics["iterations_per_second"] = round(float(metrics.get("iterations", 0)) / engine_window, 3)
    metrics["http_reqs_per_second"] = round(float(metrics.get("http_reqs", 0)) / engine_window, 3)

    assertions, missing, partial, duplicates, definite_failure = evaluate_assertions(
        bundle.manifest.assertions, events, observations, bundle.manifest.planned_journeys
    )

    limitations = []
    unreachable = target_unreachable(metrics)
    if unreachable:
        # Failed assertions against a target that never answered say nothing about the application.
        assertions = [
            {"id": row["id"], "status": "unknown", "samples": row["samples"]} if row["status"] == "fail" else row
            for row in assertions
        ]
        definite_failure = False
        limitations.append(
            f"target unreachable: all {int(metrics['http_reqs'])} requests failed before an HTTP response"
        )
    if missing:
        limitations.append(f"missing assertion evidence: {', '.join(missing)}")
    if partial:
        limitations.append(f"partial assertion evidence: {', '.join(partial)}")
    limitations.extend(f"duplicate evidence for {key}" for key in dict.fromkeys(duplicates))
    if metrics.get("dropped_iterations"):
        limitations.append(f"k6 dropped {int(metrics['dropped_iterations'])} iterations (under-delivered load)")
    thresholds_breached = lifecycle == "finished" and engine_exit_code == K6_THRESHOLDS_FAILED
    if thresholds_breached:
        limitations.append("k6 thresholds breached")
    limitations.extend(overlap_shortfalls(metrics, bundle.manifest.journeys))
    if malformed_events:
        limitations.append(f"ignored {malformed_events} malformed event record(s)")
    if malformed_metrics:
        limitations.append(f"ignored {malformed_metrics} malformed metric record(s)")
    # A create cancelled before returning an id is a cancellation, not a fixture failure.
    fixture_error = fixture and fixture["create"]["status"] != "cancelled" and (
        fixture["create"]["status"] != "created" or fixture.get("cleanup", {}).get("status") != "deleted"
    )
    if fixture_error:
        limitations.append(fixture.get("cleanup", fixture["create"])["reason"])
    hook_errors = [record["reason"] for record in (hooks or {}).values() if record["status"] == "error"]
    limitations.extend(hook_errors)
    overruns = budget_overruns(metrics, bundle.manifest.budgets)
    limitations.extend(overruns)
    delivered = metrics.get("iterations")
    if delivered != bundle.manifest.planned_journeys:
        limitations.append(
            f"delivered journeys {delivered!r} do not match planned journeys {bundle.manifest.planned_journeys}"
        )
    if lifecycle == "timed_out":
        limitations.append(
            f"engine stopped after its {engine_seconds}-second share of the {bundle.manifest.budgets.max_seconds}-second budget"
        )
    elif lifecycle == "cancelled":
        limitations.append("run cancelled by user")
    elif lifecycle == "crashed":
        limitations.append(engine_error or f"k6 exited with status {engine_exit_code}")
    limitations = list(dict.fromkeys(limitations))  # a fixture failure can also be the crash reason
    completeness = "complete" if not limitations else "incomplete"
    if overruns or fixture_error or hook_errors or unreachable:
        verdict = "error"
    elif definite_failure or thresholds_breached:
        verdict = "fail"
    elif lifecycle == "crashed":
        verdict = "error"
    elif completeness == "incomplete":
        verdict = "inconclusive"
    else:
        verdict = "pass"

    result = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "verify",
        "seed": seed,
        "planned_journeys": bundle.manifest.planned_journeys,
        "planned_journeys_per_second": round(
            sum(phase.admitted_journeys for phase in resolved_schedule)
            / max(sum(phase.seconds for phase in resolved_schedule), 1),
            3,
        ),
        "report": "report.html",
        "lifecycle": lifecycle,
        "engine_exit_code": engine_exit_code,
        "finished_at": finished_at,
        "verdict": verdict,
        "completeness": completeness,
        "assertions": assertions,
        "metrics": metrics,
        "limitations": limitations,
        # Honesty notes describe what this preview never measures; they do not affect completeness.
        "notes": ["per-arrival lateness not measured", "workload is synthetic (no traces supplied)"],
    }
    # Assertions have already seen the real values; everything kept or returned from here is a scrubbed copy.
    result = redact_value(result, secrets)
    _write_json(run_dir / "result.json", result)
    _record_stage(run_dir, run, lifecycle)
    report_path = run_dir / result["report"]
    _write_text(report_path, render_report(result, run))
    artifact_bytes = sum(entry["bytes"] for entry in artifact_files(run_dir))
    if artifact_bytes > bundle.manifest.budgets.max_artifact_bytes:
        result["verdict"] = "error"
        result["completeness"] = "incomplete"
        result["limitations"].append(
            f"artifact budget exceeded: {artifact_bytes} > {bundle.manifest.budgets.max_artifact_bytes} bytes"
        )
        _write_json(run_dir / "result.json", result)
        _write_text(report_path, render_report(result, run))
    files = artifact_files(run_dir)  # written last, so it covers every final file
    _write_json(run_dir / MANIFEST, {"schema_version": 1, "run_id": run_id, "files": files, "total_bytes": sum(entry["bytes"] for entry in files)})
    _restrict(run_dir)
    return result

