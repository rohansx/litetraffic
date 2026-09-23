"""`up`: a background activity made of repeated bounded verify slices. It reports status, never a verdict."""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from litetraffic.runner import RunnerError, _engine, _now, _target, _write_json, verify
from litetraffic.scenario import load_scenario

ACTIVITY_FILE = "activity.json"


def _slice(result: dict) -> dict:
    # Slice verdicts stay in each slice's own result.json; the activity only records that load ran.
    metrics = result.get("metrics", {})
    return {
        "run_id": result["run_id"],
        "seed": result["seed"],
        "lifecycle": result["lifecycle"],
        "finished_at": result.get("finished_at"),
        "iterations": metrics.get("iterations"),
        "http_reqs": metrics.get("http_reqs"),
    }


def up(
    target: str,
    scenario: Path,
    output_dir: Path,
    k6_path: str | None = None,
    seed: int = 0,
    max_slices: int | None = None,
    log: Callable[[str], None] = lambda message: None,
) -> dict:
    if max_slices is not None and max_slices < 1:
        raise RunnerError("--max-slices must be at least 1")
    target = _target(target)
    bundle = load_scenario(Path(scenario))
    _engine(k6_path)  # fail before creating anything when k6 is unusable

    activity_id = f"activity_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    activity_dir = Path(output_dir).resolve() / activity_id
    activity_dir.mkdir(parents=True, mode=0o700)
    activity_dir.chmod(0o700)
    path = activity_dir / ACTIVITY_FILE
    activity = {
        "schema_version": 1,
        "activity_id": activity_id,
        "mode": "background",
        "scenario": bundle.manifest.name,
        "scenario_sha256": bundle.digest,
        "target": target,
        "starting_seed": seed,
        "max_slices": max_slices,
        "artifact_dir": str(activity_dir),
        "status": "running",
        "started_at": _now().isoformat(),
        "slices": [],
    }
    _write_json(path, activity)
    log(f"activity: {path}")

    status = "error"
    seeds = itertools.count(seed) if max_slices is None else range(seed, seed + max_slices)
    try:
        for current_seed in seeds:
            # ponytail: Ctrl-C outside verify's own handlers can leave that one slice's run.json mid-lifecycle.
            result = verify(target, scenario, activity_dir, k6_path, current_seed)
            activity["slices"].append(_slice(result))
            _write_json(path, activity)
            log(f"slice {len(activity['slices'])}: {result['run_id']} {result['lifecycle']}")
            if result["lifecycle"] == "cancelled":
                status = "stopped"
                break
        else:
            status = "completed"
    except KeyboardInterrupt:
        status = "stopped"
    except Exception as exc:  # any slice failure ends the activity as an error with its message
        activity["error"] = f"{type(exc).__name__}: {exc}"
        raise RunnerError(activity["error"]) from exc
    finally:
        activity.update({"status": status, "finished_at": _now().isoformat()})
        _write_json(path, activity)
        log(f"status: {status}")
    return activity
