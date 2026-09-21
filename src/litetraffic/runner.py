from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from litetraffic.report import render_report
from litetraffic.scenario import load_scenario

SUPPORTED_K6_VERSION = "v2.2.0"


class RunnerError(ValueError):
    """The experiment could not be started safely."""


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


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
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RunnerError("target must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise RunnerError("target URL must not contain credentials")
    return value.rstrip("/")


def _read_events(path: Path, run_id: str) -> tuple[list[dict], int]:
    events: list[dict] = []
    malformed = 0
    decoder = json.JSONDecoder()
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        marker = line.find("LT_EVENT ")
        if marker < 0:
            continue
        try:
            event, _ = decoder.raw_decode(line[marker + len("LT_EVENT ") :])
            if (
                event.get("schema_version") != 1
                or event.get("type") != "assertion"
                or event.get("run_id") != run_id
                or not isinstance(event.get("assertion"), str)
                or not isinstance(event.get("passed"), bool)
            ):
                raise ValueError
        except (AttributeError, json.JSONDecodeError, ValueError):
            malformed += 1
            continue
        events.append({"sequence": len(events) + 1, **event})
    return events, malformed


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _read_metrics(path: Path) -> tuple[dict[str, object], int]:
    count_metrics = {"dropped_iterations", "http_reqs", "iterations"}
    totals: dict[str, object] = {}
    durations: list[float] = []
    failed: list[float] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            item = json.loads(line)
            metric = item.get("metric")
            value = item.get("data", {}).get("value")
            if item.get("type") != "Point" or not isinstance(value, (int, float)):
                continue
            if metric in count_metrics:
                totals[metric] = float(totals.get(metric, 0)) + value
            elif metric == "http_req_duration":
                durations.append(float(value))
            elif metric == "http_req_failed" and 0 <= value <= 1:
                failed.append(float(value))
        except (KeyError, json.JSONDecodeError, TypeError):
            malformed += 1
    if durations:
        totals["http_req_duration_ms"] = {
            "samples": len(durations),
            "average": round(sum(durations) / len(durations), 3),
            "p50": round(_percentile(durations, 0.5), 3),
            "p95": round(_percentile(durations, 0.95), 3),
            "max": round(max(durations), 3),
        }
    if failed:
        failures = sum(failed)
        totals["http_req_failed_rate"] = {
            "samples": len(failed),
            "failed": int(failures),
            "rate": round(failures / len(failed), 6),
        }
    return totals, malformed


def _communicate(process: subprocess.Popen[str], timeout: float) -> tuple[str, str]:
    return process.communicate(timeout=timeout)


def _signal_process(process: subprocess.Popen[str], value: signal.Signals) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, value)
        elif value == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    _signal_process(process, signal.SIGTERM)
    try:
        return _communicate(process, timeout=2)
    except subprocess.TimeoutExpired:
        _signal_process(process, signal.SIGKILL)
        try:
            return _communicate(process, timeout=2)
        except subprocess.TimeoutExpired:
            return "", "process did not exit within 2 seconds of SIGKILL"


def verify(
    target: str,
    scenario: Path,
    output_dir: Path,
    k6_path: str | None = None,
    seed: int = 0,
) -> dict:
    bundle = load_scenario(Path(scenario))
    executable, engine_version = _engine(k6_path)
    target = _target(target)
    resolved_schedule = bundle.manifest.schedule.resolve(seed)
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = Path(output_dir).resolve() / run_id
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True, mode=0o700)
    run_dir.chmod(0o700)

    manifest_bytes = bundle.manifest_path.read_bytes()
    started_at = datetime.now(UTC)
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario": bundle.manifest.name,
        "mode": "verify",
        "target": target,
        "seed": seed,
        "scenario_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "engine": engine_version,
        "lifecycle": "running",
        "resolved_schedule": [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule],
        "started_at": started_at.isoformat(),
    }
    _write_json(run_dir / "run.json", run)
    (run_dir / "scenario.lock.json").write_bytes(manifest_bytes)
    (run_dir / "scenario.lock.json").chmod(0o600)

    console_path = run_dir / "console.log"
    metrics_path = run_dir / "metrics.jsonl"
    command = [
        executable,
        "run",
        "--quiet",
        "--log-format",
        "raw",
        "--console-output",
        str(console_path),
        "--out",
        f"json={metrics_path}",
        str(bundle.script_path),
    ]
    environment = os.environ.copy()
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
    try:
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
            stdout, stderr = _communicate(process, timeout=bundle.manifest.budgets.max_seconds)
            lifecycle = "finished" if process.returncode == 0 else "crashed"
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

    engine_exit_code = process.returncode if process is not None else None
    finished = datetime.now(UTC)
    finished_at = finished.isoformat()
    run.update({"lifecycle": lifecycle, "finished_at": finished_at, "engine_exit_code": engine_exit_code})
    _write_json(run_dir / "run.json", run)
    (run_dir / "engine.stdout.log").write_text(stdout, encoding="utf-8")
    (run_dir / "engine.stderr.log").write_text(stderr, encoding="utf-8")
    (run_dir / "engine.stdout.log").chmod(0o600)
    (run_dir / "engine.stderr.log").chmod(0o600)

    events, malformed_events = _read_events(console_path, run_id)
    metrics, malformed_metrics = _read_metrics(metrics_path)
    elapsed_seconds = max((finished - started_at).total_seconds(), 0.000001)
    metrics["elapsed_seconds"] = round(elapsed_seconds, 6)
    metrics["iterations_per_second"] = round(float(metrics.get("iterations", 0)) / elapsed_seconds, 3)
    metrics["http_reqs_per_second"] = round(float(metrics.get("http_reqs", 0)) / elapsed_seconds, 3)
    events_path = events_dir / "000001.jsonl"
    events_path.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events), encoding="utf-8")
    events_path.chmod(0o600)

    assertions = []
    missing = []
    partial = []
    definite_failure = False
    for assertion_id in bundle.manifest.assertions:
        samples = [event["passed"] for event in events if event["assertion"] == assertion_id]
        if not samples:
            status = "unknown"
            missing.append(assertion_id)
        elif not all(samples):
            status = "fail"
            definite_failure = True
        elif len(samples) != bundle.manifest.planned_journeys:
            status = "unknown"
            partial.append(f"{assertion_id} ({len(samples)}/{bundle.manifest.planned_journeys})")
        else:
            status = "pass"
        assertions.append({"id": assertion_id, "status": status, "samples": len(samples)})

    limitations = []
    if missing:
        limitations.append(f"missing assertion evidence: {', '.join(missing)}")
    if partial:
        limitations.append(f"partial assertion evidence: {', '.join(partial)}")
    if malformed_events:
        limitations.append(f"ignored {malformed_events} malformed event record(s)")
    if malformed_metrics:
        limitations.append(f"ignored {malformed_metrics} malformed metric record(s)")
    delivered = metrics.get("iterations")
    if delivered != bundle.manifest.planned_journeys:
        limitations.append(
            f"delivered journeys {delivered!r} do not match planned journeys {bundle.manifest.planned_journeys}"
        )
    if lifecycle == "timed_out":
        limitations.append(f"run exceeded the {bundle.manifest.budgets.max_seconds}-second budget")
    elif lifecycle == "cancelled":
        limitations.append("run cancelled by user")
    elif lifecycle == "crashed":
        limitations.append(engine_error or f"k6 exited with status {engine_exit_code}")
    completeness = "complete" if not limitations else "incomplete"
    if definite_failure:
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
        "seed": seed,
        "planned_journeys": bundle.manifest.planned_journeys,
        "report": "report.html",
        "lifecycle": lifecycle,
        "engine_exit_code": engine_exit_code,
        "finished_at": finished_at,
        "verdict": verdict,
        "completeness": completeness,
        "assertions": assertions,
        "metrics": metrics,
        "limitations": limitations,
    }
    _write_json(run_dir / "result.json", result)
    report_path = run_dir / result["report"]
    report_path.write_text(render_report(result, run), encoding="utf-8")
    report_path.chmod(0o600)
    artifact_bytes = sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file())
    if artifact_bytes > bundle.manifest.budgets.max_artifact_bytes:
        result["verdict"] = "error"
        result["completeness"] = "incomplete"
        result["limitations"].append(
            f"artifact budget exceeded: {artifact_bytes} > {bundle.manifest.budgets.max_artifact_bytes} bytes"
        )
        _write_json(run_dir / "result.json", result)
        report_path.write_text(render_report(result, run), encoding="utf-8")
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
    _write_json(output_path / result_path, summary)
    return summary
