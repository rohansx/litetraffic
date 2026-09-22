from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from litetraffic.artifacts import MANIFEST, artifact_files
from litetraffic.evidence import _read_events, _read_metrics, evaluate_assertions, target_unreachable
from litetraffic.fixture import cleanup_fixture, create_fixture
from litetraffic.observation import observe
from litetraffic.process import _communicate, _stop_process
from litetraffic.report import render_report
from litetraffic.scenario import load_scenario
from litetraffic.target import validate_target

SUPPORTED_K6_VERSION = "v2.2.0"
K6_THRESHOLDS_FAILED = 99  # k6 exit code: the run completed but a threshold was crossed


def _now() -> datetime:
    return datetime.now(UTC)


class RunnerError(ValueError):
    """The experiment could not be started safely."""


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
    # k6 creates console.log and metrics.jsonl with its own (umask) mode.
    for path in [run_dir, *run_dir.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)


def _engine(k6_path: str | None) -> tuple[str, str]:
    executable = k6_path or shutil.which("k6")
    if not executable:
        raise RunnerError("k6 executable not found")
    try:
        result = subprocess.run([executable, "version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"cannot execute k6: {exc}") from exc
    if result.returncode:
        raise RunnerError((result.stderr or result.stdout).strip() or "k6 version failed")
    version = (result.stdout or result.stderr).strip()
    if len(version.split()) < 2 or version.split()[1] != SUPPORTED_K6_VERSION:
        raise RunnerError(f"unsupported k6 version; expected {SUPPORTED_K6_VERSION}, got {version or 'no version'}")
    return executable, version


def _target(value: str) -> str:
    try:
        return validate_target(value)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc


def verify(
    target: str,
    scenario: Path,
    output_dir: Path,
    k6_path: str | None = None,
    seed: int = 0,
) -> dict:
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
    command = [
        executable,
        "run",
        "--quiet",
        "--max-redirects",
        "0",
        "--log-format",
        "raw",
        "--console-output",
        str(console_path),
        "--out",
        f"json={metrics_path}",
        str(bundle.script_path),
    ]
    environment = os.environ.copy()
    environment.pop("LT_FIXTURE_ID", None)
    environment.update(
        {
            "LT_RUN_ID": run_id,
            "LT_TARGET": target,
            "LT_SEED": str(seed),
            "LT_MAX_IN_FLIGHT": str(bundle.manifest.budgets.max_in_flight),
            "LT_SCHEDULE_JSON": json.dumps(
                [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule]
            ),
        }
    )
    lifecycle = "running"
    engine_error = ""
    process: subprocess.Popen[str] | None = None
    engine_started = engine_finished = None
    fixture = None
    if bundle.manifest.fixtures.owned_http:
        try:
            fixture = {"create": create_fixture(target, bundle.manifest.fixtures.owned_http, run_id)}
        except KeyboardInterrupt:
            # ponytail: an id-less create cannot be cleaned up; at most one request was sent.
            fixture = {"create": {"status": "cancelled", "reason": "fixture create cancelled", "requests": 1}}
            lifecycle = "cancelled"
        if fixture["create"]["status"] == "created":
            environment["LT_FIXTURE_ID"] = fixture["create"]["fixture_id"]
        elif lifecycle != "cancelled":
            lifecycle = "crashed"
            engine_error = fixture["create"]["reason"]
    if lifecycle != "running":
        stdout, stderr = "", engine_error
    else:
        _record_stage(run_dir, run, "running")
        try:
            engine_seconds = bundle.manifest.budgets.max_seconds - (5 if bundle.manifest.observation else 0) - (10 if fixture else 0)
            engine_started = _now()
            process = subprocess.Popen(
                command,
                cwd=bundle.root,
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
    _write_text(run_dir / "engine.stdout.log", stdout)
    _write_text(run_dir / "engine.stderr.log", stderr)

    events, malformed_events = _read_events(console_path, run_id)
    metrics, malformed_metrics = _read_metrics(metrics_path)
    events_path = events_dir / "000001.jsonl"
    _write_text(events_path, "".join(json.dumps(event, sort_keys=True) + "\n" for event in events))

    _record_stage(run_dir, run, "observing")
    observation = None
    if bundle.manifest.observation:
        observed = lifecycle == "finished"
        observation = {"assertion": bundle.manifest.observation.assertion, "status": "unknown", "reason": "engine did not finish"}
        if observed:
            try:
                observation = observe(target, bundle.manifest.observation, run_id, fixture_id=environment.get("LT_FIXTURE_ID"))
            except KeyboardInterrupt:
                lifecycle = "cancelled"
                observation["reason"] = "observation cancelled"
        _write_json(run_dir / "observation.json", observation)
        metrics["observer_requests"] = int(observed and observation.get("reason") != "observer bearer token missing")
    if fixture:
        if fixture["create"]["status"] == "created":
            fixture_id = fixture["create"]["fixture_id"]
            try:
                fixture["cleanup"] = cleanup_fixture(target, bundle.manifest.fixtures.owned_http, run_id, fixture_id)
            except KeyboardInterrupt:
                lifecycle = "cancelled"
                fixture["cleanup"] = {"status": "error", "reason": f"fixture cleanup cancelled; fixture {fixture_id} may remain", "requests": 1}
        _write_json(run_dir / "fixture.json", fixture)
        metrics["fixture_requests"] = fixture["create"]["requests"] + fixture.get("cleanup", {}).get("requests", 0)
    if fixture or observation:
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
        bundle.manifest.assertions, events, observation, bundle.manifest.planned_journeys
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
    observed_requests = metrics.get("total_http_reqs", metrics.get("http_reqs", 0))
    request_budget_exceeded = observed_requests > bundle.manifest.budgets.max_requests
    if request_budget_exceeded:
        limitations.append(
            f"request budget exceeded: {observed_requests} > {bundle.manifest.budgets.max_requests}"
        )
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
    completeness = "complete" if not limitations else "incomplete"
    if request_budget_exceeded or fixture_error or unreachable:
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


def repeat_verify(
    target: str,
    scenario: Path,
    output_dir: Path,
    k6_path: str | None = None,
    seed: int = 0,
    repeats: int = 3,
) -> dict:
    if repeats < 2:
        raise RunnerError("repeats must be at least 2")

    runs = []
    for current_seed in range(seed, seed + repeats):
        result = verify(target, scenario, output_dir, k6_path, current_seed)
        runs.append(result)
        if result["lifecycle"] == "cancelled":
            break

    verdicts = {run["verdict"] for run in runs}
    if "error" in verdicts:
        verdict = "error"
    elif "fail" in verdicts:
        verdict = "fail"
    elif "inconclusive" in verdicts:
        verdict = "inconclusive"
    else:
        verdict = "pass"

    series_id = f"series_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    result_path = f"{series_id}.json"
    summary = {
        "schema_version": 1,
        "series_id": series_id,
        "mode": "repeat",
        "starting_seed": seed,
        "requested_runs": repeats,
        "completed_runs": len(runs),
        "lifecycle": "cancelled" if runs[-1]["lifecycle"] == "cancelled" else "finished",
        "verdict": verdict,
        "consistent": len({(run["verdict"], run["lifecycle"]) for run in runs}) == 1,
        "runs": runs,
        "result": result_path,
    }
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_path.chmod(0o700)
    _write_json(output_path / result_path, summary)
    return summary
