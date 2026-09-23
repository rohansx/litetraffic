from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

MAX_DETAIL_CHARS = 500
MAX_FAILURE_SAMPLES = 3
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

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
                or not isinstance(event.get("logical_key", ""), str)
                or not isinstance(event.get("detail", ""), str)
                or len(event.get("detail", "")) > MAX_DETAIL_CHARS
            ):
                raise ValueError
        except (AttributeError, json.JSONDecodeError, ValueError):
            malformed += 1
            continue
        events.append({"sequence": len(events) + 1, **event})
    return events, malformed


def evaluate_assertions(
    assertion_ids: list[str], events: list[dict], observations: list[dict], planned_journeys: int
) -> tuple[list[dict], list[str], list[str], list[str], bool]:
    """Summarize each declared assertion; return rows, missing ids, partial labels, identity limitations, definite failure."""
    rows, missing, partial, identity = [], [], [], []
    definite_failure = False
    observed = {observation["assertion"]: observation for observation in observations}
    for assertion_id in assertion_ids:
        if observation := observed.get(assertion_id):
            status = observation["status"]
            if status == "fail":
                definite_failure = True
            elif status == "unknown":
                missing.append(assertion_id)
            row = {"id": assertion_id, "status": status, "samples": int(status != "unknown")}
            rows.append(row | {key: observation[key] for key in ("expected", "actual", "reason") if key in observation})
            continue
        samples = [event for event in events if event["assertion"] == assertion_id]
        failures = [event for event in samples if not event["passed"]]
        row = {"id": assertion_id, "samples": len(samples)}
        if not samples:
            row["status"] = "unknown"
            missing.append(assertion_id)
        elif failures:
            row["status"] = "fail"
            row["failures"] = [
                {key: event.get(key) for key in ("sequence", "logical_key", "expected", "actual", "detail")}
                for event in failures[:MAX_FAILURE_SAMPLES]
            ]
            definite_failure = True
        elif not all(event.get("logical_key") for event in samples):
            # Without a journey key, one journey reporting twice is indistinguishable from two journeys.
            row["status"] = "unknown"
            identity.append(f"evidence without journey identity for {assertion_id}")
        elif repeated := [key for key, count in Counter(e["logical_key"] for e in samples).items() if count > 1]:
            # One journey reporting twice must not stand in for a journey that reported nothing.
            row["status"] = "unknown"
            identity.extend(f"duplicate evidence for {key}" for key in repeated)
        elif len(samples) != planned_journeys:
            row["status"] = "unknown"
            partial.append(f"{assertion_id} ({len(samples)}/{planned_journeys})")
        else:
            row["status"] = "pass"
        rows.append(row)
    return rows, missing, partial, identity, definite_failure


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _read_metrics(path: Path, journeys=()) -> tuple[dict[str, object], int]:
    count_metrics = {"dropped_iterations", "http_reqs", "iterations"}
    expected: dict[str, set[str]] = {}
    for journey in journeys:
        for operation, statuses in journey.expected_statuses.items():
            expected.setdefault(operation, set()).update(str(status) for status in statuses)
    totals: dict[str, object] = {}
    durations: list[float] = []
    failed: list[float] = []
    unexpected = 0
    operations: dict[str, dict[str, list[float]]] = {}
    transport_failures = 0
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
                if metric == "http_reqs" and (item["data"].get("tags") or {}).get("method") in WRITE_METHODS:
                    totals["write_attempts"] = int(totals.get("write_attempts", 0) + value)
            elif metric == "vus_max":
                totals["vus_max"] = max(int(value), int(totals.get("vus_max", 0)))
            elif metric == "http_req_duration":
                durations.append(float(value))
                bucket = _operation(operations, item)
                bucket["durations"].append(float(value))
                if isinstance(stamp := item["data"].get("time"), str):
                    # k6 stamps the point when the response completes, so the request spans [end - duration, end].
                    end = datetime.fromisoformat(stamp).timestamp() * 1000
                    bucket["spans"].append((end - value, end))
            elif metric == "http_req_failed" and 0 <= value <= 1:
                tags = item["data"].get("tags") or {}
                _operation(operations, item)["failed"].append(float(value))
                # k6 tags requests that never got an HTTP response with status "0"; 4xx/5xx also carry an error_code.
                transport_failures += value == 1 and tags.get("status") == "0"
                unexpected += value == 1 and tags.get("status") not in expected.get(tags.get("operation"), ())
                failed.append(float(value))
        except (AttributeError, KeyError, json.JSONDecodeError, TypeError, ValueError):
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
        if transport_failures:
            totals["http_req_failed_rate"]["transport"] = transport_failures
        if expected:
            totals["unexpected_http_failure_rate"] = {
                "samples": len(failed),
                "failed": unexpected,
                "rate": round(unexpected / len(failed), 6),
            }
    if operations:
        totals["by_operation"] = {
            name: {
                "samples": len(values["durations"]),
                "p95": round(_percentile(values["durations"], 0.95), 3) if values["durations"] else None,
                "failed_rate": round(sum(values["failed"]) / len(values["failed"]), 6) if values["failed"] else None,
            }
            for name, values in operations.items()
        }
        if overlap := {name: _peak_overlap(values["spans"]) for name, values in operations.items() if values["spans"]}:
            totals["overlap"] = overlap
    return totals, malformed


def _peak_overlap(spans: list[tuple[float, float]]) -> int:
    """Most requests open at one instant; a request ending exactly as another starts does not overlap it."""
    # Ends (-1) sort before starts (+1) at the same instant.
    edges = sorted([(start, 1) for start, _ in spans] + [(end, -1) for _, end in spans])
    open_now = peak = 0
    for _, delta in edges:
        open_now += delta
        peak = max(peak, open_now)
    return peak


def overlap_shortfalls(metrics: dict, journeys) -> list[str]:
    """Declared min_overlap that the run never reached: the concurrency the scenario relies on did not happen."""
    observed = metrics.get("overlap", {})
    required: dict[str, int] = {}
    for journey in journeys:
        for operation, minimum in journey.min_overlap.items():
            required[operation] = max(minimum, required.get(operation, 0))
    return [
        f"concurrency not achieved for {operation}: observed {observed.get(operation, 0)} < {minimum}"
        for operation, minimum in required.items()
        if observed.get(operation, 0) < minimum
    ]


def _operation(operations: dict[str, dict[str, list[float]]], item: dict) -> dict[str, list[float]]:
    """Bucket for the point's k6 `operation` tag; untagged requests share `_untagged`."""
    name = (item["data"].get("tags") or {}).get("operation")
    key = name if isinstance(name, str) and name else "_untagged"
    return operations.setdefault(key, {"durations": [], "failed": [], "spans": []})


def budget_overruns(metrics: dict, budgets) -> list[str]:
    """Run-time budget checks against what k6 and the controller actually did."""
    overruns = []
    requests = metrics.get("total_http_reqs", metrics.get("http_reqs", 0))
    if requests > budgets.max_requests:
        overruns.append(f"request budget exceeded: {requests} > {budgets.max_requests}")
    if metrics["write_attempts"] > budgets.max_write_attempts:
        overruns.append(f"write budget exceeded: {metrics['write_attempts']} > {budgets.max_write_attempts}")
    if metrics.get("vus_max", 0) > budgets.max_in_flight:
        overruns.append(f"in-flight budget exceeded: {metrics['vus_max']} > {budgets.max_in_flight}")
    return overruns


def target_unreachable(metrics: dict) -> bool:
    """Every request failed at the transport level, so no application outcome was observed."""
    failed = metrics.get("http_req_failed_rate") or {}
    return metrics.get("http_reqs", 0) > 0 and failed.get("transport", 0) == failed.get("samples")
