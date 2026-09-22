"""Plain-text renderings of command results for terminals (the --json output is the stable contract)."""

from __future__ import annotations

from pathlib import Path


def _number(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "?" if value is None else str(value)


def _journeys(result: dict) -> str:
    delivered = _number(result.get("metrics", {}).get("iterations"))
    return f"journeys: {delivered}/{_number(result.get('planned_journeys'))}"


def format_verify(result: dict, output_dir: Path) -> list[str]:
    if result.get("mode") == "repeat":
        lines = [
            f"verdict: {result['verdict'].upper()}  lifecycle: {result['lifecycle']}"
            f"  runs: {result['completed_runs']}/{result['requested_runs']}"
            f"  consistent: {'yes' if result['consistent'] else 'no'}"
        ]
        for run in result["runs"]:
            lines.append(f"seed {run['seed']}  {run['verdict'].upper()}  {run['lifecycle']}  {_journeys(run)}")
        lines.append(f"summary: {Path(output_dir).resolve() / result['result']}")
        return lines
    lines = [f"verdict: {result['verdict'].upper()}  lifecycle: {result['lifecycle']}  {_journeys(result)}"]
    lines += [f"{item['id']}  {item['status']}  {item['samples']}" for item in result.get("assertions", [])]
    lines += [f"- {limitation}" for limitation in result.get("limitations", [])]
    if "run_id" in result and "report" in result:
        lines.append(f"report: {Path(output_dir).resolve() / result['run_id'] / result['report']}")
    return lines


def format_diff(result: dict) -> list[str]:
    lines = [f"verdict: {result['verdict'].upper()}"]
    lines += [f"- {reason}" for reason in result.get("reasons", [])]
    incompatible = result.get("incompatibilities", [])
    lines.append(f"compatibility: incompatible ({', '.join(incompatible)})" if incompatible else "compatibility: comparable")
    lines += [f"regression: {item}" for item in result["correctness"]["assertion_regressions"]]
    p95 = result["performance"]["p95"]
    change = "" if p95["change_percent"] is None else f" ({p95['change_percent']:+}%)"
    baseline = "n/a" if p95["baseline_ms"] is None else f"{p95['baseline_ms']}ms"
    candidate = "n/a" if p95["candidate_ms"] is None else f"{p95['candidate_ms']}ms"
    lines.append(f"p95: {baseline} -> {candidate}{change}  status: {p95['status']}")
    for name, item in result["performance"].get("by_operation", {}).items():
        change = "" if item["change_percent"] is None else f" ({item['change_percent']:+}%)"
        lines.append(f"p95 {name}: {item['baseline_p95_ms']}ms -> {item['candidate_p95_ms']}ms{change}")
    return lines


def format_inspect(result: dict) -> list[str]:
    lines = [
        f"name: {result['name']}",
        f"script: {result['script']}",
        f"planned journeys: {result['planned_journeys']}",
        f"max journey requests: {result['maximum_journey_requests']}",
        f"max observation requests: {result['maximum_observation_requests']}",
        f"max journey writes: {result['maximum_journey_writes']}",
    ]
    lines += [f"phase: {phase['name']}  {phase['seconds']}s at {phase['rate']}/s" for phase in result["resolved_schedule"]]
    lines += [f"assertion: {assertion}" for assertion in result["assertions"]]
    return lines
