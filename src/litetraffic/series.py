from __future__ import annotations

import statistics

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
