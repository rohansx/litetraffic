"""`export breakscale`: a finished run as a Breakscale design file (`.breakscale`), a queueing model to sweep load on.

The model is built from the outside, so it is a sketch: every number is either measured in the run, with its source, or
assumed, with the reason. Both lists are written into the design as canvas notes and returned for the CLI to print.
The file format is Breakscale's src/designFile.ts; the checks it must pass are isTopology in src/clipboard.ts.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from litetraffic.runs import read_json

DESIGN_APP = "breakscale"
DESIGN_VERSION = 1
NAME_MAX_CHARS = 80  # Breakscale trims a longer name on import
NOTE_MAX_CHARS = 2000  # NOTE_MAX_CHARS in src/sim/annotations.ts; longer text is cut on import
NOTE_WIDTH = 440
Z95 = 1.6448536269514722  # standard normal quantile at 0.95
K6_DEFAULT_TIMEOUT_MS = 60_000
DATABASE = re.compile(r"(?i)postgres|mysql|mariadb|mongo|cockroach|database|(?:^|[-_.])(?:db|pg)(?:$|[-_.\d])")

# Breakscale's defaults (src/sim/presets.ts, repo commit d2c876e0). The TypeScript type requires every field.
EXTRA_DEFAULTS = {
    "instances": 1,
    "replicaCount": 3,
    "replicationLagMs": 50,
    "readFraction": 0.9,
    "shardCount": 4,
    "shardCapacity": 4,
    "hotKeyFraction": 0,
}
_BASE = {"hitRate": 0, "errorRate": 0, "timeoutMs": 0, "retries": 0, "rps": 0}
CLIENT = {**_BASE, "capacity": 1, "serviceMs": 0, "serviceCv": 0, "queueLimit": 0, "timeoutMs": 1000, "rps": 50}
SERVICE = {**_BASE, "capacity": 8, "serviceMs": 25, "serviceCv": 0.6, "queueLimit": 64}
DB = {**_BASE, "capacity": 6, "serviceMs": 30, "serviceCv": 0.7, "queueLimit": 32, "lockMs": 15}


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _n(value: float) -> str:
    return f"{value:g}"


def _inputs(run_dir: Path) -> tuple[dict, dict, dict]:
    """run.json and server.json are optional context; result.json holds the metrics and is required."""
    result = read_json(run_dir / "result.json")
    if result is None or not isinstance(result.get("metrics"), dict):
        raise ValueError(f"{run_dir / 'result.json'} is missing or unreadable; export needs a finished run")
    return read_json(run_dir / "run.json") or {}, result, read_json(run_dir / "server.json") or {}


def _lognormal_cv(p50: float, p95: float) -> float | None:
    """Coefficient of variation of the lognormal whose median and 95th percentile are p50 and p95."""
    if not (_finite(p50) and _finite(p95)) or p50 <= 0 or p95 < p50:
        return None
    sigma = math.log(p95 / p50) / Z95
    return math.sqrt(math.exp(sigma * sigma) - 1)


def _operations(metrics: dict) -> list[dict]:
    operations = metrics.get("by_operation") if isinstance(metrics.get("by_operation"), dict) else {}
    return [
        item
        for item in operations.values()
        if isinstance(item, dict) and _finite(item.get("samples")) and item["samples"] > 0 and _finite(item.get("p50"))
    ]


def _latency(metrics: dict, rps: float, measured: list[str], assumed: list[str]) -> tuple[float, float]:
    """(serviceMs, serviceCv) from per-operation p50/p95, else the run-wide figures; ValueError without either."""
    operations = _operations(metrics)
    if operations:
        weights = [item["samples"] for item in operations]
        pairs = [(item["p50"], item.get("p95")) for item in operations]
        source, fit = f"sample-weighted p50 of {len(operations)} operation(s)", "each operation's p95/p50, weighted by samples"
    else:
        overall = metrics.get("http_req_duration_ms") if isinstance(metrics.get("http_req_duration_ms"), dict) else {}
        if not _finite(overall.get("p50")):
            raise ValueError("the run has no request latencies to model")
        weights, pairs, source, fit = [1], [(overall["p50"], overall.get("p95"))], "run-wide p50", "the run-wide p95/p50"
        assumed.append("app latency is the run-wide p50/p95: this run did not record a per-operation p50")
    service_ms = sum(weight * p50 for weight, (p50, _) in zip(weights, pairs)) / sum(weights)
    measured.append(
        f"app serviceMs {_n(round(service_ms, 3))} = {source} at {_n(rps)} rps; an upper bound, because it includes"
        " queueing, the network and any database call"
    )
    fits = [(weight, cv) for weight, (p50, p95) in zip(weights, pairs) if (cv := _lognormal_cv(p50, p95)) is not None]
    if not fits:
        assumed.append(f"app serviceCv {_n(SERVICE['serviceCv'])}: Breakscale default; no usable p95/p50 pair")
        return service_ms, SERVICE["serviceCv"]
    cv = round(sum(weight * cv for weight, cv in fits) / sum(weight for weight, _ in fits), 3)
    assumed.append(
        f"app serviceCv {_n(cv)}: a lognormal fit to {fit}; the spread between operations is not included"
    )
    return service_ms, cv


def _capacity(metrics: dict, measured: list[str], assumed: list[str]) -> int:
    overlap = metrics.get("overlap") if isinstance(metrics.get("overlap"), dict) else {}
    peaks = [value for value in overlap.values() if _finite(value) and value >= 1]
    if not peaks:
        assumed.append(f"app capacity {SERVICE['capacity']}: Breakscale default; the run recorded no request overlap")
        return SERVICE["capacity"]
    capacity = int(max(peaks))
    measured.append(
        f"app capacity {capacity} = most requests of one operation in flight at once (metrics.overlap); a lower bound"
        " on the app's real concurrency, which is not visible from outside"
    )
    return capacity


def _error_rate(metrics: dict, measured: list[str], assumed: list[str]) -> float:
    for key, label in (("unexpected_http_failure_rate", "unexpected HTTP failure rate"), ("http_req_failed_rate", "HTTP failure rate")):
        rate = (metrics.get(key) or {}).get("rate") if isinstance(metrics.get(key), dict) else None
        if _finite(rate):
            measured.append(f"app errorRate {_n(rate)} = the run's {label}")
            assumed.append("app errorRate was measured at the run's own load, so it may include failures caused by that load")
            return rate
    assumed.append("app errorRate 0: the run recorded no failure rate")
    return 0


def _database(server: dict, assumed: list[str]) -> tuple[str, dict] | None:
    """The first captured container whose name looks like a database, or None."""
    containers = server.get("containers") if isinstance(server.get("containers"), dict) else {}
    names = [name for name in containers if DATABASE.search(name) and isinstance(containers[name], dict)]
    if not names:
        assumed.append("no db node: server capture shows no database container, so database time stays inside app serviceMs")
        return None
    if len(names) > 1:
        assumed.append(f"only {names[0]} is modelled; other database containers ignored: {', '.join(names[1:])}")
    return names[0], containers[names[0]]


def _db_ms(container: dict, rps: float, service_ms: float, assumed: list[str]) -> float:
    """The database's share of the app's measured time: its peak CPU per app request, at most half of that time."""
    cpu = container.get("peak_cpu_percent")
    if _finite(cpu) and cpu > 0:
        db_ms = cpu * 1000 / 100 / rps
        why = f"peak CPU {_n(cpu)}% of one core x 1000 / {_n(rps)} rps: CPU only, no I/O wait, one query per request"
    else:
        db_ms, why = DB["serviceMs"], "Breakscale default; server capture has no CPU sample for it"
    if db_ms > service_ms / 2:
        db_ms, why = service_ms / 2, f"{why}; capped at half the app's measured time"
    db_ms = round(db_ms, 3)
    assumed.append(f"db serviceMs {_n(db_ms)}: {why}; app serviceMs is reduced by the same amount")
    return db_ms


def _read_fraction(metrics: dict, measured: list[str], assumed: list[str]) -> float:
    requests, writes = metrics.get("http_reqs"), metrics.get("write_attempts")
    if not (_finite(requests) and requests > 0 and _finite(writes)):
        assumed.append(f"db readFraction {_n(EXTRA_DEFAULTS['readFraction'])}: Breakscale default; the run counted no writes")
        return EXTRA_DEFAULTS["readFraction"]
    k6_writes = max(writes - (metrics.get("fixture_requests") or 0), 0)
    fraction = round(min(max(1 - k6_writes / requests, 0), 1), 3)
    measured.append(f"db readFraction {_n(fraction)} = 1 - {_n(k6_writes)} write requests / {_n(requests)} requests")
    assumed.append("each write request is taken to be one database write")
    return fraction


def _node(node_id: str, kind: str, label: str, x: int, preset: dict, **config: object) -> dict:
    return {"id": node_id, "kind": kind, "label": label, "x": x, "y": 40, "config": {**EXTRA_DEFAULTS, **preset, **config}}


def _notes(title: str, lines: list[str], note_id: str, x: int) -> list[dict]:
    """Canvas notes holding `lines` under `title`, split so that no note is cut on import."""
    chunks: list[list[str]] = [[]]
    for line in (f"- {line}" for line in lines):
        if len("\n".join([title, *chunks[-1], line])) > NOTE_MAX_CHARS and chunks[-1]:
            chunks.append([])
        chunks[-1].append(line[: NOTE_MAX_CHARS - len(title) - 1])
    return [
        {"id": f"{note_id}-{index}", "kind": "note", "text": "\n".join([title, *chunk]), "x": x, "y": 200 + 520 * index, "width": NOTE_WIDTH, "size": "sm"}
        for index, chunk in enumerate(chunks)
    ]


def _saved_at(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_design(run_dir: Path, name: str | None = None, now: datetime | None = None) -> dict:
    """{design, measured, assumed} for the run in `run_dir`; ValueError when the run cannot be modelled."""
    run, result, server = _inputs(Path(run_dir))
    metrics = result["metrics"]
    rps = metrics.get("http_reqs_per_second")
    if not (_finite(rps) and rps > 0):
        raise ValueError("the run delivered no requests, so there is no load to model")
    measured = [f"users rps {_n(rps)} = requests delivered per second over the k6 window (http_reqs_per_second)"]
    phases = [phase.get("name") for phase in run.get("resolved_schedule") or [] if isinstance(phase, dict)]
    assumed = [
        "users traffic steady: the run's phases" + (f" ({', '.join(map(str, phases))})" if phases else "") + " are flattened to their average rate",
        f"users timeoutMs {K6_DEFAULT_TIMEOUT_MS}: k6's default request timeout; a journey can set its own",
        "users retries 0: LiteTraffic adds none; a journey script could",
    ]
    service_ms, service_cv = _latency(metrics, rps, measured, assumed)
    capacity = _capacity(metrics, measured, assumed)
    error_rate = _error_rate(metrics, measured, assumed)
    assumed += [
        "app instances 1: the replica count is not visible from outside",
        f"app queueLimit {SERVICE['queueLimit']}: Breakscale default; the real backlog is unknown",
        "app timeoutMs 0 and retries 0: Breakscale defaults (no timeout or retry toward the db)",
    ]
    nodes = [_node("users", "client", "LiteTraffic load", 40, CLIENT, rps=rps, timeoutMs=K6_DEFAULT_TIMEOUT_MS, traffic="steady")]
    edges = [{"id": "users->app", "from": "users", "to": "app", "weight": 1}]
    database = _database(server, assumed)
    if database is not None:
        db_name, container = database
        db_ms = _db_ms(container, rps, service_ms, assumed)
        service_ms -= db_ms
        read_fraction = _read_fraction(metrics, measured, assumed)
        assumed += [
            f"db capacity {DB['capacity']}: Breakscale default; max_connections and the app's pool size are unknown",
            f"db serviceCv {_n(DB['serviceCv'])}, queueLimit {DB['queueLimit']}, lockMs {DB['lockMs']} and instances 1: Breakscale defaults",
        ]
        nodes.append(_node("db", "db", db_name, 568, DB, serviceMs=db_ms, readFraction=read_fraction))
        edges.append({"id": "app->db", "from": "app", "to": "db", "weight": 1})
    service = {"capacity": capacity, "serviceMs": round(service_ms, 3), "serviceCv": service_cv, "errorRate": error_rate}
    nodes.insert(1, _node("app", "service", run.get("scenario") or "App", 304, SERVICE, **service))
    assumed.append(
        "replicaCount, replicationLagMs, shardCount, shardCapacity and hotKeyFraction (and the client's unused service"
        " fields) keep Breakscale's defaults; these node kinds do not use them"
    )
    default_name = f"LiteTraffic {run.get('scenario') or result.get('run_id') or Path(run_dir).name}"
    annotations = _notes("Measured in the run", measured, "measured", 40) + _notes("Assumed", assumed, "assumed", 520)
    design = {
        "app": DESIGN_APP,
        "version": DESIGN_VERSION,
        "savedAt": _saved_at(now),
        "name": ((name or "").strip() or default_name)[:NAME_MAX_CHARS],
        "topology": {"nodes": nodes, "edges": edges, "annotations": annotations},
    }
    return {"design": design, "measured": measured, "assumed": assumed}


def write_design(design: dict, path: Path) -> None:
    # allow_nan=False: NaN or Infinity is not JSON and would fail Breakscale's finite-number checks anyway.
    Path(path).write_text(json.dumps(design, indent=2, allow_nan=False) + "\n", encoding="utf-8")
