from __future__ import annotations

import json
import math
from pathlib import Path


class ComparisonError(ValueError):
    """Run artifacts cannot be compared safely."""


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _load_run(path: Path) -> tuple[dict, dict]:
    root = Path(path).resolve()
    try:
        run = json.loads((root / "run.json").read_text(encoding="utf-8"))
        result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read run artifacts from {root}: {exc}") from exc
    if not isinstance(run, dict) or not isinstance(result, dict):
        raise ComparisonError(f"run artifacts in {root} must be JSON objects")
    if run.get("schema_version") != 1 or result.get("schema_version") != 1:
        raise ComparisonError(f"unsupported run artifact schema in {root}")
    if run.get("run_id") != result.get("run_id"):
        raise ComparisonError(f"run_id mismatch in {root}")
    if (
        not isinstance(run.get("run_id"), str)
        or not isinstance(run.get("scenario_sha256"), str)
        or not isinstance(run.get("seed"), int)
        or not isinstance(run.get("engine"), str)
        or not isinstance(run.get("resolved_schedule"), list)
    ):
        raise ComparisonError(f"invalid run artifact in {root}")
    metrics = result.get("metrics")
    assertions = result.get("assertions")
    if (
        result.get("verdict") not in {"pass", "fail", "inconclusive", "error"}
        or not isinstance(metrics, dict)
        or not isinstance(assertions, list)
        or not all(isinstance(item, dict) for item in assertions)
        or any(
            key in metrics and not isinstance(metrics[key], dict)
            for key in ("http_req_duration_ms", "http_req_failed_rate")
        )
    ):
        raise ComparisonError(f"invalid result artifact in {root}")
    return run, result


def compare_runs(
    baseline_path: Path,
    candidate_path: Path,
    max_p95_regression_percent: float | None = None,
) -> dict:
    if max_p95_regression_percent is not None and (
        not math.isfinite(max_p95_regression_percent) or max_p95_regression_percent < 0
    ):
        raise ComparisonError("max p95 regression percent must be finite non-negative")

    baseline_run, baseline = _load_run(baseline_path)
    candidate_run, candidate = _load_run(candidate_path)
    compatibility_fields = ("scenario_sha256", "seed", "engine", "resolved_schedule")
    incompatibilities = [
        field for field in compatibility_fields if baseline_run.get(field) != candidate_run.get(field)
    ]

    baseline_verdict = baseline.get("verdict")
    candidate_verdict = candidate.get("verdict")
    correctness_regression = baseline_verdict == "pass" and candidate_verdict == "fail"
    baseline_assertions = {item.get("id"): item.get("status") for item in baseline.get("assertions", [])}
    candidate_assertions = {item.get("id"): item.get("status") for item in candidate.get("assertions", [])}
    assertion_regressions = sorted(
        assertion_id
        for assertion_id, status in baseline_assertions.items()
        if status == "pass" and candidate_assertions.get(assertion_id) != "pass"
    )
    correctness = {
        "baseline_verdict": baseline_verdict,
        "candidate_verdict": candidate_verdict,
        "regression": correctness_regression,
        "assertion_regressions": assertion_regressions,
    }

    baseline_metrics = baseline.get("metrics", {})
    candidate_metrics = candidate.get("metrics", {})
    baseline_latency = baseline_metrics.get("http_req_duration_ms", {})
    candidate_latency = candidate_metrics.get("http_req_duration_ms", {})
    baseline_p95 = baseline_latency.get("p95")
    candidate_p95 = candidate_latency.get("p95")
    baseline_samples = baseline_latency.get("samples", 0)
    candidate_samples = candidate_latency.get("samples", 0)
    if not _finite_number(baseline_samples) or baseline_samples < 0:
        baseline_samples = 0
    if not _finite_number(candidate_samples) or candidate_samples < 0:
        candidate_samples = 0
    change_percent = None
    status = "reported"
    if not _finite_number(baseline_p95) or not _finite_number(candidate_p95):
        status = "unavailable"
    elif baseline_p95 <= 0:
        status = "inconclusive"
    else:
        change_percent = round((candidate_p95 - baseline_p95) / baseline_p95 * 100, 3)
        if max_p95_regression_percent is not None:
            if min(baseline_samples, candidate_samples) < 200:
                status = "inconclusive"
            elif change_percent > max_p95_regression_percent:
                status = "regression"
            else:
                status = "within_limit"
    if incompatibilities:
        status = "incomparable"

    baseline_error_rate = baseline_metrics.get("http_req_failed_rate", {}).get("rate")
    candidate_error_rate = candidate_metrics.get("http_req_failed_rate", {}).get("rate")
    baseline_throughput = baseline_metrics.get("http_reqs_per_second")
    candidate_throughput = candidate_metrics.get("http_reqs_per_second")
    throughput_change = None
    if _finite_number(baseline_throughput) and _finite_number(candidate_throughput):
        if baseline_throughput == candidate_throughput == 0:
            throughput_change = 0.0
        elif baseline_throughput:
            throughput_change = round((candidate_throughput - baseline_throughput) / baseline_throughput * 100, 3)

    performance = {
        "p95": {
            "baseline_ms": float(baseline_p95) if _finite_number(baseline_p95) else None,
            "candidate_ms": float(candidate_p95) if _finite_number(candidate_p95) else None,
            "change_percent": change_percent,
            "threshold_percent": max_p95_regression_percent,
            "samples": {"baseline": baseline_samples, "candidate": candidate_samples},
            "status": status,
        },
        "http_error_rate": {
            "baseline": float(baseline_error_rate) if _finite_number(baseline_error_rate) else None,
            "candidate": float(candidate_error_rate) if _finite_number(candidate_error_rate) else None,
            "change_percentage_points": round((candidate_error_rate - baseline_error_rate) * 100, 3)
            if _finite_number(baseline_error_rate) and _finite_number(candidate_error_rate)
            else None,
        },
        "http_reqs_per_second": {
            "baseline": float(baseline_throughput) if _finite_number(baseline_throughput) else None,
            "candidate": float(candidate_throughput) if _finite_number(candidate_throughput) else None,
            "change_percent": throughput_change,
        },
    }
    progress = {
        "iterations": {
            "baseline": baseline_metrics.get("iterations"),
            "candidate": candidate_metrics.get("iterations"),
        },
        "http_reqs": {
            "baseline": baseline_metrics.get("http_reqs"),
            "candidate": candidate_metrics.get("http_reqs"),
        },
    }

    if incompatibilities:
        verdict = "inconclusive"
    elif correctness_regression or status == "regression":
        verdict = "fail"
    elif candidate_verdict in {"error", "inconclusive"} or (
        max_p95_regression_percent is not None and status in {"inconclusive", "unavailable"}
    ):
        verdict = "inconclusive"
    else:
        verdict = "pass"

    return {
        "schema_version": 1,
        "baseline_run_id": baseline_run["run_id"],
        "candidate_run_id": candidate_run["run_id"],
        "comparable": not incompatibilities,
        "incompatibilities": incompatibilities,
        "verdict": verdict,
        "correctness": correctness,
        "progress": progress,
        "performance": performance,
    }
