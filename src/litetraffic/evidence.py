from __future__ import annotations

import json
import math
from pathlib import Path


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
            elif metric == "http_req_duration":
                durations.append(float(value))
            elif metric == "http_req_failed" and 0 <= value <= 1:
                tags = item["data"].get("tags") or {}
                # k6 tags requests that never got an HTTP response with status "0" and an error_code.
                transport_failures += value == 1 and (tags.get("status") == "0" or "error_code" in tags)
                failed.append(float(value))
        except (AttributeError, KeyError, json.JSONDecodeError, TypeError):
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
    return totals, malformed


def target_unreachable(metrics: dict) -> bool:
    """Every request failed at the transport level, so no application outcome was observed."""
    failed = metrics.get("http_req_failed_rate") or {}
    return metrics.get("http_reqs", 0) > 0 and failed.get("transport", 0) == failed.get("samples")
