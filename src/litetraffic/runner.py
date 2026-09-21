from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

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


def _read_metrics(path: Path) -> tuple[dict[str, float], int]:
    count_metrics = {"dropped_iterations", "http_reqs", "iterations"}
    totals: dict[str, float] = {}
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            item = json.loads(line)
            if (
                item.get("type") == "Point"
                and item.get("metric") in count_metrics
                and isinstance(item.get("data", {}).get("value"), (int, float))
            ):
                metric = item["metric"]
                totals[metric] = totals.get(metric, 0) + item["data"]["value"]
        except (KeyError, json.JSONDecodeError, TypeError):
            malformed += 1
    return totals, malformed


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
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = Path(output_dir).resolve() / run_id
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True, mode=0o700)
    run_dir.chmod(0o700)

    manifest_bytes = bundle.manifest_path.read_bytes()
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "verify",
        "target": target,
        "seed": seed,
        "scenario_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "engine": engine_version,
        "started_at": datetime.now(UTC).isoformat(),
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
                [phase.model_dump(exclude={"admitted_journeys"}) for phase in bundle.manifest.schedule.phases]
            ),
        }
    )
    try:
        process = subprocess.run(
            command,
            cwd=bundle.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=bundle.manifest.budgets.max_seconds + 10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"k6 execution failed: {exc}") from exc

    (run_dir / "engine.stdout.log").write_text(process.stdout, encoding="utf-8")
    (run_dir / "engine.stderr.log").write_text(process.stderr, encoding="utf-8")
    (run_dir / "engine.stdout.log").chmod(0o600)
    (run_dir / "engine.stderr.log").chmod(0o600)

    events, malformed_events = _read_events(console_path, run_id)
    metrics, malformed_metrics = _read_metrics(metrics_path)
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
    completeness = "complete" if not limitations else "incomplete"
    if process.returncode:
        verdict = "error"
        limitations.append(f"k6 exited with status {process.returncode}")
        completeness = "incomplete"
    elif definite_failure:
        verdict = "fail"
    elif completeness == "incomplete":
        verdict = "inconclusive"
    else:
        verdict = "pass"

    artifact_bytes = sum(path.stat().st_size for path in run_dir.rglob("*") if path.is_file())
    if artifact_bytes > bundle.manifest.budgets.max_artifact_bytes:
        verdict = "error"
        completeness = "incomplete"
        limitations.append(
            f"artifact budget exceeded: {artifact_bytes} > {bundle.manifest.budgets.max_artifact_bytes} bytes"
        )
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "verdict": verdict,
        "completeness": completeness,
        "assertions": assertions,
        "metrics": metrics,
        "limitations": limitations,
    }
    _write_json(run_dir / "result.json", result)
    return result
