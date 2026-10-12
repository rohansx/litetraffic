import os
import signal
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
def test_a_group_of_unreaped_zombies_does_not_crash_the_stop(monkeypatch):
    # macOS reports EPERM for a group that holds only zombies; it becomes ESRCH once they are reaped.
    process = subprocess.Popen(["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    real_killpg = os.killpg
    probes = []

    def killpg(pgid, value):
        if value == signal.SIGTERM:
            return real_killpg(pgid, value)
        if value == 0:
            probes.append(value)
        raise PermissionError(1, "Operation not permitted") if len(probes) < 3 else ProcessLookupError()

    monkeypatch.setattr(process_module.os, "killpg", killpg)

    _, stderr, survived = process_module._stop_process(process)

    assert not survived
    assert process_module.GROUP_SURVIVED not in stderr
    assert len(probes) == 3  # polled through the EPERM answers until the group was gone


def write_proc_stat(proc, pid, comm, state, ppid, pgrp):
    (proc / str(pid)).mkdir()
    (proc / str(pid) / "stat").write_text(f"{pid} ({comm}) {state} {ppid} {pgrp} {pgrp} 0 -1 4194560 0 0 0 0 0 0 0 1 0 0\n")


def test_adopted_zombies_lists_only_our_dead_children_in_the_group(tmp_path):
    # A procfs as seen by litetraffic running as pid 1: the group's orphans were reparented to it when
    # their leader exited, and nothing else reaps them.
    write_proc_stat(tmp_path, 7, "sleep", "Z", 1, 5)  # ours, in the group, dead: reap it
    write_proc_stat(tmp_path, 8, "sleep (1)", "Z", 1, 5)  # comm with a space and parentheses
    write_proc_stat(tmp_path, 9, "sleep", "S", 1, 5)  # still running: not ours to wait for yet
    write_proc_stat(tmp_path, 10, "k6", "Z", 1, 6)  # another group's zombie
    write_proc_stat(tmp_path, 11, "sleep", "Z", 3, 5)  # the group's zombie, but someone else's child
    (tmp_path / "self").mkdir()  # non-numeric entries are skipped
    (tmp_path / "12").mkdir()  # a process that vanished between listing and reading

    assert process_module._adopted_zombies(5, our_pid=1, proc=tmp_path) == [7, 8]


def test_adopted_zombies_is_empty_without_procfs(tmp_path):
    assert process_module._adopted_zombies(5, our_pid=1, proc=tmp_path / "missing") == []


@posix_only
def test_group_gone_reaps_zombies_that_were_reparented_to_us(monkeypatch):
    # Linux: killpg(pgid, 0) keeps succeeding while unreaped zombies hold the group, so a litetraffic
    # that is pid 1 (docker run without --init) must reap the orphans itself or report a false survivor.
    process = subprocess.Popen(["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    zombies = [4242, 4243]
    reaped = []
    monkeypatch.setattr(process_module, "_adopted_zombies", lambda pgid, **_: list(zombies) if pgid == process.pid else [])
    monkeypatch.setattr(process_module.os, "waitpid", lambda pid, flags: (reaped.append((pid, flags)), zombies.remove(pid), (pid, 0))[2])
    monkeypatch.setattr(process_module.os, "killpg", lambda pgid, sig: None if zombies else (_ for _ in ()).throw(ProcessLookupError()))

    assert process_module._group_gone(process)
    assert reaped == [(4242, os.WNOHANG), (4243, os.WNOHANG)]
    monkeypatch.undo()  # the real waitpid for the real child
    process.kill()
    process.wait()


@posix_only
def test_group_gone_tolerates_a_zombie_reaped_by_someone_else(monkeypatch):
    process = subprocess.Popen(["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    calls = []
    monkeypatch.setattr(process_module, "_adopted_zombies", lambda pgid, **_: [4242] if not calls else [])
    monkeypatch.setattr(process_module.os, "waitpid", lambda pid, flags: (calls.append(pid), (_ for _ in ()).throw(ChildProcessError()))[1])
    monkeypatch.setattr(process_module.os, "killpg", lambda pgid, sig: None if not calls else (_ for _ in ()).throw(ProcessLookupError()))

    assert process_module._group_gone(process)
    assert calls == [4242]
    monkeypatch.undo()
    process.kill()
    process.wait()


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
