import os
import subprocess
import sys

import pytest

import litetraffic.process as process_module
from litetraffic.fixture import run_fixture_command

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")

# The leader prints its pid (its process group id), leaves a grandchild sleeping on the shared pipes, and exits.
ORPHANING = [
    sys.executable, "-c",
    "import os, subprocess, sys; print(os.getpid(), flush=True); subprocess.Popen(['sleep', '30'])",
]


def group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@posix_only
def test_timed_out_fixture_kills_descendants_left_by_an_exited_leader(tmp_path):
    record, stdout = run_fixture_command("setup", ORPHANING, tmp_path, dict(os.environ), timeout=1)

    assert record["reason"] == "fixture setup failed: timed out after 1s"
    assert not group_exists(int(stdout.split()[0]))


@posix_only
def test_a_surviving_group_is_reported(monkeypatch):
    process = subprocess.Popen(["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    real_killpg = os.killpg
    monkeypatch.setattr(process_module, "GROUP_EXIT_SECONDS", 0.1)
    monkeypatch.setattr(process_module.os, "killpg", lambda pgid, value: None if value == 0 else real_killpg(pgid, value))

    stdout, stderr, survived = process_module._stop_process(process)

    assert survived
    assert process_module.GROUP_SURVIVED in stderr


@posix_only
def test_a_fixture_whose_group_survives_records_it_in_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(process_module, "_group_gone", lambda process: False)

    record, _ = run_fixture_command("setup", ["sleep", "30"], tmp_path, dict(os.environ), timeout=1)

    assert record["reason"] == f"fixture setup failed: timed out after 1s; {process_module.GROUP_SURVIVED}"


@posix_only
def test_a_timed_out_k6_whose_group_survives_is_a_limitation(tmp_path, monkeypatch):
    from test_runner import fake_k6
    from test_scenario import manifest, write_bundle
    from litetraffic.runner import verify

    data = manifest(
        schedule={"unit": "journeys_per_second", "phases": [{"name": "measure", "seconds": 1, "rate": 1}]},
        budgets=manifest()["budgets"] | {"max_seconds": 3, "max_requests": 3, "max_write_attempts": 1},
    )
    scenario = write_bundle(tmp_path / "scenario", data)
    monkeypatch.setenv("FAKE_K6_EVENTS", "[]")
    monkeypatch.setattr(process_module, "_group_gone", lambda process: False)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, [], iterations=1, sleep_seconds=60)))

    assert result["lifecycle"] == "timed_out"
    assert f"k6 {process_module.GROUP_SURVIVED}" in result["limitations"]


# The leader leaves a daemon-style grandchild that ignores SIGTERM and holds none of the pipes, then sleeps itself.
SIGTERM_PROOF_DAEMON = [
    sys.executable, "-c",
    "import os, signal, subprocess, time\n"
    "subprocess.Popen(['sleep', '30'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
    "                 preexec_fn=lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN))\n"
    "print(os.getpid(), flush=True)\n"
    "time.sleep(30)",
]


@posix_only
def test_a_sigterm_proof_daemon_gets_sigkill_after_its_leader_exits(tmp_path):
    record, stdout = run_fixture_command("setup", SIGTERM_PROOF_DAEMON, tmp_path, dict(os.environ), timeout=1)

    assert record["reason"] == "fixture setup failed: timed out after 1s"
    assert not group_exists(int(stdout.split()[0]))
