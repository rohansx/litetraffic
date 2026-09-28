import json
import os
import sys
import time

import pytest

from litetraffic import capture as capture_module
from litetraffic import dashboard, runner
from litetraffic.capture import Capture, parse_spec, parse_stats, signature
from litetraffic.cli import main
from litetraffic.doctor import run_doctor
from litetraffic.runner import verify
from test_runner import assertion, fake_k6
from test_scenario import manifest, write_bundle

LOGS = [
    "2026-09-28T10:00:01.000000001Z INFO started worker 1234",
    "2026-09-28T10:00:02.000000001Z ERROR db timeout after 5000ms for 1b4e28ba-2fa1-11d2-883f-0016d3cca427",
    "2026-09-28T10:00:03.000000001Z ERROR db timeout after 7000ms for 6fa459ea-ee8a-3ca4-894e-db77e160355e",
    '2026-09-28T10:00:04.000000001Z 172.17.0.1 - "GET /orders HTTP/1.1" 502 12',
    "2026-09-28T10:00:05.000000001Z leaked token super-secret-value in a log line ERROR",
]
STATS = [
    {"Name": "api", "CPUPerc": "0.50%", "MemUsage": "100MiB / 1.9GiB"},
    {"Name": "api", "CPUPerc": "80.25%", "MemUsage": "1.5GiB / 1.9GiB"},
]


def _wait(condition) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """A `docker` on PATH: `inspect` knows only `api`, `logs -f` prints LOGS then blocks, `stats` walks STATS."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "stats-calls"
    script = bin_dir / "docker"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        "if os.environ.get('FAKE_DOCKER_DENIED'):\n"
        "    print('permission denied while trying to connect to the Docker daemon socket', file=sys.stderr); raise SystemExit(1)\n"
        "if args[0] == '--version':\n"
        "    print('Docker version 27.0.1, build fake')\n"
        "elif args[0] == 'inspect':\n"
        "    if args[-1] != 'api':\n"
        "        print(f'Error: No such container: {args[-1]}', file=sys.stderr); raise SystemExit(1)\n"
        "    print('/api')\n"
        "elif args[0] == 'logs':\n"
        f"    lines = {LOGS!r} * int(os.environ.get('FAKE_DOCKER_REPEAT', '1'))\n"
        "    sys.stdout.write(''.join(line + '\\n' for line in lines)); sys.stdout.flush()\n"
        "    time.sleep(60)\n"
        "elif args[0] == 'stats':\n"
        f"    state = pathlib.Path({str(state)!r})\n"
        "    calls = int(state.read_text()) if state.exists() else 0\n"
        "    state.write_text(str(calls + 1))\n"
        f"    print(json.dumps({STATS!r}[min(calls, 1)]))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(capture_module, "STATS_INTERVAL_SECONDS", 0.05)
    return bin_dir


@pytest.fixture
def no_docker(tmp_path, monkeypatch):
    bin_dir = tmp_path / "nodocker"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)  # fake k6 runs through `env python3`
    monkeypatch.setenv("PATH", str(bin_dir))


def test_parse_spec_accepts_docker_names_and_rejects_everything_else():
    assert parse_spec("docker:api,worker.1,api") == ["api", "worker.1"]
    for bad in ("api", "podman:api", "docker:", "docker:-api", "docker:a/b", "docker:../x", "docker:" + "a" * 129):
        with pytest.raises(ValueError):
            parse_spec(bad)


def test_signature_collapses_repeats_that_differ_only_in_variable_parts():
    first = signature('2026-09-28 10:00:02,123 ERROR user "alice" timeout 5000ms id 1b4e28ba-2fa1-11d2-883f-0016d3cca427 at 0x7f3a')
    second = signature("2026-09-29T11:00:02Z ERROR user 'bob' timeout 7000ms id 6fa459ea-ee8a-3ca4-894e-db77e160355e at 0x1")
    assert first == second == "<ts> ERROR user <str> timeout <n>ms id <uuid> at <hex>"
    assert signature("GET /orders 502") == "GET /orders 502"  # status codes stay readable


def test_parse_stats_reads_cpu_and_memory_in_mib():
    assert parse_stats('{"Name": "api", "CPUPerc": "12.5%", "MemUsage": "1.5GiB / 2GiB"}') == {"name": "api", "cpu_percent": 12.5, "mem_mb": 1536.0}
    assert parse_stats('{"Name": "api", "CPUPerc": "--", "MemUsage": ""}') is None
    assert parse_stats("not json") is None


def test_capture_summarises_logs_stats_and_problems(tmp_path, fake_docker):
    run = Capture(["api", "ghost"], tmp_path / "server")
    run.start()
    _wait(lambda: (tmp_path / "stats-calls").exists() and int((tmp_path / "stats-calls").read_text() or 0) >= 2)
    summary = run.stop()
    api = summary["containers"]["api"]
    assert list(summary["containers"]) == ["api"]
    assert summary["problems"] == ["ghost: Error: No such container: ghost"]
    assert (api["baseline_cpu_percent"], api["peak_cpu_percent"], api["baseline_mem_mb"], api["peak_mem_mb"]) == (0.5, 80.25, 100.0, 1536.0)
    assert (api["log_lines"], api["error_lines"], api["truncated"]) == (5, 4, False)
    top = api["signatures"][0]
    assert top["count"] == 2 and top["signature"] == "ERROR db timeout after <n>ms for <uuid>"
    assert top["first_seen"] == "2026-09-28T10:00:02.000000001Z" and top["example"].startswith("ERROR db timeout after 5000ms")
    assert (tmp_path / "server" / "api.log").read_text().count("\n") == 5
    assert all("Name" in json.loads(line) and "at" in json.loads(line) for line in (tmp_path / "server" / "stats.jsonl").read_text().splitlines())
    assert all(log.process.poll() is not None for log in run.logs)  # the blocking `docker logs -f` was stopped


def test_capture_caps_each_log_file_but_keeps_counting(tmp_path, fake_docker, monkeypatch):
    monkeypatch.setattr(capture_module, "LOG_CAP_BYTES", 200)
    monkeypatch.setenv("FAKE_DOCKER_REPEAT", "50")
    run = Capture(["api"], tmp_path / "server")
    run.start()
    _wait(lambda: run.logs[0].lines >= 250)
    summary = run.stop()
    assert summary["containers"]["api"]["truncated"] is True
    assert summary["containers"]["api"]["log_lines"] == 250
    assert (tmp_path / "server" / "api.log").stat().st_size <= 200


def test_capture_without_docker_only_records_a_problem(tmp_path, no_docker):
    run = Capture(["api"], tmp_path / "server")
    run.start()
    assert run.stop() == {"schema_version": 1, "source": "docker", "containers": {}, "problems": ["docker not found on PATH"]}
    assert not (tmp_path / "server").exists()


def test_capture_reports_a_daemon_permission_error(tmp_path, fake_docker, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_DENIED", "1")
    run = Capture(["api"], tmp_path / "server")
    run.start()
    assert run.stop()["problems"] == ["api: permission denied while trying to connect to the Docker daemon socket"]


def _passing_run(tmp_path, monkeypatch, sleep_seconds=0, **kwargs):
    monkeypatch.setenv("LT_SERVICE_SECRET", "super-secret-value")
    scenario = write_bundle(tmp_path / "scenario", manifest(secret_env=["LT_SERVICE_SECRET"]))
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    return verify("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, sleep_seconds=sleep_seconds)), **kwargs)


def test_verify_capture_writes_server_json_without_touching_the_verdict(tmp_path, fake_docker, monkeypatch):
    # One second of "load" so the fake `docker logs` has printed before capture stops.
    result = _passing_run(tmp_path, monkeypatch, sleep_seconds=1, capture=["api", "ghost"])
    run_dir = tmp_path / "runs" / result["run_id"]
    server = json.loads((run_dir / "server.json").read_text())
    assert result["verdict"] == "pass" and result["completeness"] == "complete"
    assert server["containers"]["api"]["error_lines"] == 4
    assert result["limitations"] == ["Server capture incomplete: ghost: Error: No such container: ghost"]
    assert (run_dir / "server" / "api.log").stat().st_mode & 0o077 == 0
    assert dashboard._detail(run_dir)["server"] == server
    assert "super-secret-value" not in (run_dir / "server.json").read_text() + (run_dir / "server" / "api.log").read_text()
    assert any("[redacted]" in item["example"] for item in server["containers"]["api"]["signatures"])
    assert any(item["path"] == "server/api.log" for item in json.loads((run_dir / "artifacts.json").read_text())["files"])


def test_verify_capture_without_docker_still_passes(tmp_path, no_docker, monkeypatch):
    result = _passing_run(tmp_path, monkeypatch, capture=["api"])
    assert result["verdict"] == "pass"
    assert result["limitations"] == ["Server capture incomplete: docker not found on PATH"]


def test_verify_without_capture_writes_no_server_files(tmp_path, fake_docker, monkeypatch):
    result = _passing_run(tmp_path, monkeypatch)
    assert not (tmp_path / "runs" / result["run_id"] / "server.json").exists()
    assert result["limitations"] == []


def test_verify_rejects_an_invalid_capture_name(capsys):
    assert main(["verify", "scenario", "--target", "http://example.test", "--capture", "docker:bad/name", "--json"]) == 3
    assert "invalid container name" in json.loads(capsys.readouterr().out)["error"]


def test_doctor_reports_docker_only_when_present(fake_docker, tmp_path):
    check = next(check for check in run_doctor(output_dir=tmp_path).checks if check.name == "docker")
    assert check.ok and "Docker version 27.0.1" in check.detail


def test_doctor_does_not_fail_without_docker(no_docker, tmp_path):
    assert all(check.name != "docker" for check in run_doctor(output_dir=tmp_path).checks)


def test_a_run_interrupted_mid_way_still_stops_every_docker_child(tmp_path, fake_docker, monkeypatch):
    started = []

    class Recording(Capture):
        def start(self):
            started.append(self)
            super().start()

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "Capture", Recording)
    monkeypatch.setattr(runner, "_read_events", interrupt)
    result = _passing_run(tmp_path, monkeypatch, capture=["api"])
    assert result["lifecycle"] == "cancelled"
    assert started and all(log.process.poll() is not None for log in started[0].logs)
    assert started[0].stats_thread is not None and not started[0].stats_thread.is_alive()
