from __future__ import annotations

import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path

from litetraffic import runner

# Series key -> path into each run's metrics.
DISPERSION_METRICS = {
    "p95_ms": ("http_req_duration_ms", "p95"),
    "http_req_failed_rate": ("http_req_failed_rate", "rate"),
    "http_reqs_per_second": ("http_reqs_per_second",),
}


def aggregate_verdict(runs: list[dict]) -> str:
    verdicts = {run["verdict"] for run in runs}
    return next((verdict for verdict in ("error", "fail", "inconclusive") if verdict in verdicts), "pass")


def _value(metrics: object, path: tuple[str, ...]) -> float | None:
    for key in path:
        metrics = metrics.get(key) if isinstance(metrics, dict) else None
    return float(metrics) if isinstance(metrics, (int, float)) and not isinstance(metrics, bool) else None


def dispersion(runs: list[dict]) -> dict:
    """min/max/mean/stdev per metric across the runs that reported it; stdev needs two runs."""
    summary: dict[str, dict | None] = {}
    for name, path in DISPERSION_METRICS.items():
        values = [value for run in runs if (value := _value(run.get("metrics"), path)) is not None]
        summary[name] = (
            {
                "min": min(values),
                "max": max(values),
                "mean": statistics.mean(values),
                "stdev": statistics.stdev(values) if len(values) > 1 else None,
            }
            if values
            else None
        )
    return summary


def repeat_verify(
    target: str,
    scenario: Path,
    output_dir: Path,
    k6_path: str | None = None,
    seed: int = 0,
    repeats: int = 3,
    same_seed: bool = False,
) -> dict:
    if repeats < 2:
        raise runner.RunnerError("repeats must be at least 2")

    runs = []
    for current_seed in [seed] * repeats if same_seed else range(seed, seed + repeats):
        result = runner.verify(target, scenario, output_dir, k6_path, current_seed)  # module lookup: stubbable
        runs.append(result)
        if result["lifecycle"] == "cancelled":
            break

    series_id = f"series_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    result_path = f"{series_id}.json"
    summary = {
        "schema_version": 1,
        "series_id": series_id,
        "mode": "repeat",
        "starting_seed": seed,
        "same_seed": same_seed,
        "requested_runs": repeats,
        "completed_runs": len(runs),
        "lifecycle": "cancelled" if runs[-1]["lifecycle"] == "cancelled" else "finished",
        "verdict": aggregate_verdict(runs),
        "consistent": len({(run["verdict"], run["lifecycle"]) for run in runs}) == 1,
        "dispersion": dispersion(runs),
        "runs": runs,
        "result": result_path,
    }
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_path.chmod(0o700)
    runner._write_json(output_path / result_path, summary)
    return summary
