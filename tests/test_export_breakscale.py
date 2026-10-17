import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from litetraffic.breakscale import build_design
from litetraffic.cli import main

# What Breakscale's importer (src/clipboard.ts isTopology, src/designFile.ts parseDesignFile) accepts.
NODE_KINDS = {"client", "lb", "service", "cache", "db", "queue", "worker", "autoscaler", "replica"}
NUMERIC = ("capacity", "serviceMs", "serviceCv", "queueLimit", "hitRate", "errorRate", "timeoutMs", "retries", "rps")
# EXTRA_DEFAULTS (src/sim/presets.ts): the TypeScript type requires these even though isTopology does not check them.
EXTRA = ("instances", "replicaCount", "replicationLagMs", "readFraction", "shardCount", "shardCapacity", "hotKeyFraction")
NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
Z95 = 1.6448536269514722


def lognormal_mean(p50: float, p95: float) -> float:
    # Breakscale's serviceMs is the mean of its service-time draw; a lognormal's mean is its median x exp(sigma^2 / 2).
    sigma = math.log(p95 / p50) / Z95
    return p50 * math.exp(sigma * sigma / 2)


# The fixture's operations: reserve 30 x (p50 40, p95 120), complete 10 x (p50 80, p95 160).
FIXTURE_MEAN = (30 * lognormal_mean(40, 120) + 10 * lognormal_mean(80, 160)) / 40


def _finite(value) -> bool:
    # JavaScript's Number.isFinite: true and false are not numbers.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_topology(topology) -> bool:
    """A line-for-line Python port of isTopology."""
    if not isinstance(topology, dict) or not isinstance(topology.get("nodes"), list) or not isinstance(topology.get("edges"), list):
        return False
    ids = set()
    for node in topology["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            return False
        if not isinstance(node.get("label"), str) or node.get("kind") not in NODE_KINDS:
            return False
        if not _finite(node.get("x")) or not _finite(node.get("y")) or not isinstance(node.get("config"), dict):
            return False
        if not all(_finite(node["config"].get(key)) for key in NUMERIC) or node["id"] in ids:
            return False
        ids.add(node["id"])
    for edge in topology["edges"]:
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str) or not edge["id"]:
            return False
        if edge.get("from") not in ids or edge.get("to") not in ids or not _finite(edge.get("weight")):
            return False
    return True


def write_run(
    path: Path,
    *,
    by_operation: dict | None = None,
    overlap: dict | None = None,
    containers: dict | None = None,
    schedule: list | None = None,
    **extra_metrics: object,
) -> Path:
    path.mkdir(parents=True)
    run = {"run_id": path.name, "scenario": "hievents-promo-oversell", "target": "http://127.0.0.1:8123"}
    if schedule is not None:
        run["resolved_schedule"] = schedule
    metrics = {
        "http_reqs": 40.0,
        "http_reqs_per_second": 12.5,
        "write_attempts": 22,
        "fixture_requests": 2,
        "http_req_duration_ms": {"samples": 40, "average": 60.0, "p50": 50.0, "p95": 130.0, "max": 200.0},
        "http_req_failed_rate": {"samples": 40, "failed": 3, "rate": 0.075},
        "unexpected_http_failure_rate": {"samples": 40, "failed": 1, "rate": 0.025},
        "by_operation": by_operation
        if by_operation is not None
        else {
            "reserve": {"samples": 30, "p50": 40.0, "p95": 120.0, "failed_rate": 0.0},
            "complete": {"samples": 10, "p50": 80.0, "p95": 160.0, "failed_rate": 0.1},
        },
        "overlap": overlap if overlap is not None else {"reserve": 10, "complete": 4},
        **extra_metrics,
    }
    result = {"run_id": path.name, "verdict": "fail", "metrics": metrics}
    (path / "run.json").write_text(json.dumps(run))
    (path / "result.json").write_text(json.dumps(result))
    if containers is not None:
        (path / "server.json").write_text(json.dumps({"schema_version": 1, "source": "docker", "containers": containers, "problems": []}))
    return path


def _container(peak_cpu: float | None, baseline_cpu: float | None = 1.0) -> dict:
    return {"baseline_cpu_percent": baseline_cpu, "peak_cpu_percent": peak_cpu, "baseline_mem_mb": 100.0, "peak_mem_mb": 120.0}


def _nodes(design: dict) -> dict:
    return {node["id"]: node for node in design["topology"]["nodes"]}


def test_design_has_the_breakscale_file_envelope(tmp_path):
    run = write_run(tmp_path / "run_a")

    design = build_design(run, now=NOW)["design"]

    assert design["app"] == "breakscale"
    assert design["version"] == 1
    assert design["savedAt"] == "2026-10-17T12:00:00.000Z"
    assert design["name"] == "LiteTraffic hievents-promo-oversell"
    assert set(design["topology"]) == {"nodes", "edges", "annotations"}


def test_name_can_be_chosen_and_is_kept_within_what_breakscale_reads(tmp_path):
    run = write_run(tmp_path / "run_a")

    assert build_design(run, name="Hi.Events v1.9.0", now=NOW)["design"]["name"] == "Hi.Events v1.9.0"
    assert len(build_design(run, name="x" * 200, now=NOW)["design"]["name"]) == 80


@pytest.mark.parametrize("containers", [None, {"hievents-app": _container(40.0), "hievents-postgres": _container(25.0)}])
def test_topology_passes_breakscales_import_validation(tmp_path, containers):
    run = write_run(tmp_path / "run_a", containers=containers)

    design = build_design(run, now=NOW)["design"]
    text = json.dumps(design, allow_nan=False)  # NaN or Infinity would fail Number.isFinite

    assert is_topology(json.loads(text)["topology"])
    for node in design["topology"]["nodes"]:
        assert all(_finite(node["config"][key]) for key in EXTRA), node["id"]


def test_isTopology_port_rejects_what_breakscale_rejects(tmp_path):
    topology = build_design(write_run(tmp_path / "run_a"), now=NOW)["design"]["topology"]
    assert is_topology(topology)

    broken = json.loads(json.dumps(topology))
    broken["nodes"][0]["config"]["rps"] = True
    assert not is_topology(broken)
    broken = json.loads(json.dumps(topology))
    broken["edges"][0]["to"] = "nowhere"
    assert not is_topology(broken)


def test_client_rate_is_the_runs_delivered_request_rate(tmp_path):
    nodes = _nodes(build_design(write_run(tmp_path / "run_a"), now=NOW)["design"])

    client = nodes["users"]
    assert client["kind"] == "client"
    assert client["config"]["rps"] == 12.5
    assert client["config"]["traffic"] == "steady"


def test_client_rate_is_requests_over_the_scheduled_seconds_not_the_k6_process_time(tmp_path):
    # A 10 s schedule inside a 12.4 s k6 process: 255 requests are 25.5 rps, not the 20.598 http_reqs_per_second.
    schedule = [{"name": "warm", "rate": 10, "seconds": 4}, {"name": "steady", "rate": 30, "seconds": 6}]
    run = write_run(tmp_path / "run_a", schedule=schedule, http_reqs=255.0, http_reqs_per_second=20.598)

    export = build_design(run, now=NOW)

    assert _nodes(export["design"])["users"]["config"]["rps"] == 25.5
    assert any(line.startswith("users rps 25.5 = 255 requests / 10 s") and "resolved_schedule" in line for line in export["measured"])


def test_client_rate_uses_the_request_window_when_requests_outlast_the_schedule(tmp_path):
    # A 1 s burst of journeys whose requests run for 4 s: they were delivered over 4 s, not 1.
    run = write_run(tmp_path / "run_a", schedule=[{"name": "burst", "rate": 10, "seconds": 1}], http_reqs=20.0, request_window_seconds=4.0)

    export = build_design(run, now=NOW)

    assert _nodes(export["design"])["users"]["config"]["rps"] == 5.0
    assert any(line.startswith("users rps 5 = 20 requests / 4 s") and "first request" in line for line in export["measured"])


def test_client_rate_falls_back_to_the_k6_process_rate_and_says_so(tmp_path):
    export = build_design(write_run(tmp_path / "run_a"), now=NOW)

    assert _nodes(export["design"])["users"]["config"]["rps"] == 12.5
    assert any(line.startswith("users rps 12.5") and "http_reqs_per_second" in line for line in export["measured"])
    assert any("whole k6 process" in line for line in export["assumed"])


def test_service_time_and_spread_come_from_per_operation_latencies(tmp_path):
    nodes = _nodes(build_design(write_run(tmp_path / "run_a"), now=NOW)["design"])

    app = nodes["app"]["config"]
    # Each operation's lognormal mean, weighted by samples; not the p50 of 50, which reads about 16% low.
    assert app["serviceMs"] == pytest.approx(FIXTURE_MEAN, abs=0.001)
    assert app["serviceMs"] > 59
    # Lognormal fit per operation from p95/p50 (3 and 2), weighted by samples.
    assert app["serviceCv"] == pytest.approx(0.673, abs=0.001)
    assert app["capacity"] == 10  # the largest peak overlap seen
    assert app["errorRate"] == 0.025  # unexpected failures only: expected statuses are intended outcomes
    assert app["instances"] == 1


@pytest.mark.parametrize(
    ("p95", "clamped", "raw"),
    [
        (40.0, 0.2, "0"),  # p95 == p50: a zero spread, which Breakscale would run as a fixed service time
        (1200.0, 1.0, "8.422"),  # p95/p50 = 30: a queueing tail, outside what Breakscale's Inspector can show
    ],
)
def test_service_spread_is_kept_within_0_2_to_1_and_the_raw_fit_is_said(tmp_path, p95, clamped, raw):
    operations = {"reserve": {"samples": 10, "p50": 40.0, "p95": p95, "failed_rate": 0.0}}
    export = build_design(write_run(tmp_path / "run_a", by_operation=operations), now=NOW)

    assert _nodes(export["design"])["app"]["config"]["serviceCv"] == clamped
    assert any(line.startswith(f"app serviceCv {clamped:g} ") and f"raw fit {raw}" in line for line in export["assumed"])


def test_without_per_operation_p50_the_run_wide_latency_is_used_and_said(tmp_path):
    old = {"reserve": {"samples": 40, "p95": 120.0, "failed_rate": 0.0}}
    export = build_design(write_run(tmp_path / "run_a", by_operation=old), now=NOW)

    assert _nodes(export["design"])["app"]["config"]["serviceMs"] == pytest.approx(lognormal_mean(50, 130), abs=0.001)
    assert any("run-wide" in line for line in export["assumed"])


def test_an_operation_without_a_usable_p95_counts_its_p50_as_the_mean_and_says_so(tmp_path):
    operations = {
        "reserve": {"samples": 30, "p50": 40.0, "p95": 120.0, "failed_rate": 0.0},
        "complete": {"samples": 10, "p50": 80.0, "p95": None, "failed_rate": 0.0},
    }
    export = build_design(write_run(tmp_path / "run_a", by_operation=operations), now=NOW)

    expected = (30 * lognormal_mean(40, 120) + 10 * 80) / 40
    assert _nodes(export["design"])["app"]["config"]["serviceMs"] == pytest.approx(expected, abs=0.001)
    assert any("complete" in line and "p50" in line and "mean" in line for line in export["assumed"])


def test_every_printed_app_service_time_is_the_one_in_the_file(tmp_path):
    for containers in (None, {"hievents-postgres": _container(25.0)}):
        run = write_run(tmp_path / f"run_{len(containers or {})}", containers=containers)

        export = build_design(run, now=NOW)
        service_ms = _nodes(export["design"])["app"]["config"]["serviceMs"]

        lines = [line for line in export["measured"] + export["assumed"] if line.startswith("app serviceMs")]
        assert lines and all(line.startswith(f"app serviceMs {service_ms:g} ") for line in lines), lines
        assert any(line.startswith("app end-to-end p50 50 ") for line in export["measured"])


def test_a_small_client_overlap_is_only_a_lower_bound_on_capacity(tmp_path):
    # 2 requests in flight is how many the run's VUs sent at once, not how many workers the app has.
    export = build_design(write_run(tmp_path / "run_a", overlap={"reserve": 2, "complete": 1}), now=NOW)

    assert _nodes(export["design"])["app"]["config"]["capacity"] == 8
    assert any(line.startswith("app overlap 2 ") and "lower bound" in line for line in export["measured"])
    assert any(line.startswith("app capacity 8: ") and "overlap 2" in line for line in export["assumed"])


def test_without_overlap_capacity_is_breakscales_default_and_said(tmp_path):
    export = build_design(write_run(tmp_path / "run_a", overlap={}), now=NOW)

    assert _nodes(export["design"])["app"]["config"]["capacity"] == 8
    assert any(line.startswith("app capacity 8") for line in export["assumed"])


def test_no_db_node_without_a_captured_database_container(tmp_path):
    for containers in (None, {"hievents-app": _container(40.0)}):
        run = write_run(tmp_path / f"run_{len(containers or {})}", containers=containers)

        design = build_design(run, now=NOW)["design"]

        assert set(_nodes(design)) == {"users", "app"}
        assert [(edge["from"], edge["to"]) for edge in design["topology"]["edges"]] == [("users", "app")]


def test_db_node_from_a_captured_database_container(tmp_path):
    run = write_run(tmp_path / "run_a", containers={"hievents-app": _container(40.0), "hievents-postgres": _container(25.0)})

    export = build_design(run, now=NOW)
    nodes = _nodes(export["design"])

    db = nodes["db"]
    assert db["kind"] == "db"
    assert db["label"] == "hievents-postgres"
    # DB CPU above its baseline per app request: (25 - 1)% of a core * 1000 ms / 12.5 rps.
    assert db["config"]["serviceMs"] == 19.2
    # The app's measured time already includes the DB call, so the DB share comes out of it.
    assert nodes["app"]["config"]["serviceMs"] == pytest.approx(FIXTURE_MEAN - 19.2, abs=0.001)
    # 20 writes from k6 (22 counted, 2 of them fixture requests) out of 40 requests.
    assert db["config"]["readFraction"] == 0.5
    assert [(edge["from"], edge["to"]) for edge in export["design"]["topology"]["edges"]] == [("users", "app"), ("app", "db")]
    assert any(line.startswith("db capacity 6") for line in export["assumed"])


def test_db_time_is_cpu_above_baseline_over_the_delivered_rate(tmp_path):
    # 40 requests over a 4 s schedule is 10 rps (http_reqs_per_second says 12.5); the DB idles at 20% of a core.
    run = write_run(
        tmp_path / "run_a",
        containers={"hievents-postgres": _container(25.0, baseline_cpu=20.0)},
        schedule=[{"name": "steady", "rate": 5, "seconds": 4}],
    )

    export = build_design(run, now=NOW)

    # (25 - 20)% of a core * 1000 ms / 10 rps.
    assert _nodes(export["design"])["db"]["config"]["serviceMs"] == 5.0
    assert any(line.startswith("db serviceMs 5: ") and "baseline" in line and "average" in line for line in export["assumed"])


def test_db_time_without_a_baseline_uses_the_peak(tmp_path):
    run = write_run(tmp_path / "run_a", containers={"hievents-postgres": _container(25.0, baseline_cpu=None)})

    assert _nodes(build_design(run, now=NOW)["design"])["db"]["config"]["serviceMs"] == 20.0


def test_db_share_never_exceeds_half_the_measured_time(tmp_path):
    run = write_run(tmp_path / "run_a", containers={"pg": _container(400.0)})

    export = build_design(run, now=NOW)
    nodes = _nodes(export["design"])

    assert nodes["db"]["config"]["serviceMs"] == pytest.approx(FIXTURE_MEAN / 2, abs=0.001)
    assert nodes["app"]["config"]["serviceMs"] == pytest.approx(FIXTURE_MEAN / 2, abs=0.001)
    assert any("capped" in line for line in export["assumed"])


def test_every_assumption_is_returned_and_written_into_the_design(tmp_path):
    run = write_run(tmp_path / "run_a", containers={"hievents-postgres": _container(None)})

    export = build_design(run, now=NOW)
    notes = [a for a in export["design"]["topology"]["annotations"] if a["kind"] == "note"]
    text = "\n".join(note["text"] for note in notes)

    for word in ("instances", "queueLimit", "timeoutMs", "serviceCv", "lognormal", "lockMs", "steady"):
        assert any(word in line for line in export["assumed"]), word
    for line in export["assumed"] + export["measured"]:
        assert line in text
    assert any("end-to-end" in line for line in export["measured"])
    assert all(len(note["text"]) <= 2000 and 80 <= note["width"] <= 900 for note in notes)
    assert len({a["id"] for a in export["design"]["topology"]["annotations"]}) == len(notes)


def test_a_run_without_latencies_cannot_be_modelled(tmp_path):
    run = write_run(tmp_path / "run_a")
    result = json.loads((run / "result.json").read_text())
    del result["metrics"]["http_req_duration_ms"]
    result["metrics"]["by_operation"] = {}
    (run / "result.json").write_text(json.dumps(result))

    with pytest.raises(ValueError, match="no request latencies"):
        build_design(run, now=NOW)


def test_cli_writes_the_design_and_prints_every_assumption(tmp_path, capsys):
    runs = tmp_path / "runs"
    write_run(runs / "run_a")
    out = tmp_path / "hievents.breakscale"

    code = main(["export", "breakscale", "run_a", "--runs-dir", str(runs), "--out", str(out), "--name", "Hi.Events"])

    printed = capsys.readouterr().out
    assert code == 0
    design = json.loads(out.read_text())
    assert design["name"] == "Hi.Events"
    assert f"wrote: {out}" in printed
    assumed = [line for line in printed.splitlines() if line.startswith("assumed: ")]
    assert assumed and len(assumed) == len(build_design(runs / "run_a")["assumed"])


def test_cli_defaults_the_output_to_the_run_id_in_the_working_directory(tmp_path, capsys, monkeypatch):
    run = write_run(tmp_path / "runs" / "run_a")
    monkeypatch.chdir(tmp_path)

    code = main(["export", "breakscale", str(run), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["out"] == "run_a.breakscale"
    assert (tmp_path / "run_a.breakscale").is_file()
    assert payload["assumed"] and payload["measured"]


def test_cli_exits_3_for_a_missing_run(tmp_path, capsys):
    code = main(["export", "breakscale", "run_missing", "--runs-dir", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["ok"] is False
    assert "run_missing" in payload["error"]
    assert not (Path.cwd() / "run_missing.breakscale").exists()


def test_cli_exits_3_for_a_run_directory_without_a_result(tmp_path, capsys):
    (tmp_path / "run_a").mkdir()

    code = main(["export", "breakscale", "run_a", "--runs-dir", str(tmp_path), "--out", str(tmp_path / "x.breakscale")])

    assert code == 3
    assert "result.json" in capsys.readouterr().out
    assert not (tmp_path / "x.breakscale").exists()


def test_cli_rejects_an_unknown_export_format(tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        main(["export", "excalidraw", "run_a"])
    assert exit_info.value.code == 2
