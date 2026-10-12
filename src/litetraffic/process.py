"""Engine subprocess control: bounded waits and process-group termination."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

STOP_GRACE_SECONDS = 4  # _stop_process waits 2 s after SIGTERM, 1 s after SIGKILL, then up to 1 s for the group
GROUP_EXIT_SECONDS = 1.0  # killed descendants are reaped by init (or by us when we are init); give that a moment
GROUP_SURVIVED = "process group did not exit"


def _communicate(process: subprocess.Popen[str], timeout: float) -> tuple[str, str]:
    return process.communicate(timeout=timeout)


def _signal_process(process: subprocess.Popen[str], value: signal.Signals) -> None:
    # On POSIX signal the whole group even when the leader has exited: its descendants may still hold the pipes.
    # ponytail: once the leader is reaped its pid can in principle be reused as another group's id; with a
    # large pid_max that is unlikely, and the upgrade is pidfd_send_signal on a pidfd taken at spawn.
    try:
        if os.name == "posix":
            os.killpg(process.pid, value)
        elif process.poll() is not None:
            return
        elif value == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS answers EPERM, not ESRCH, for a group whose only members are zombies awaiting reaping.
        # _group_gone decides afterwards whether anything is really left.
        pass


def _adopted_zombies(pgid: int, our_pid: int | None = None, proc: Path = Path("/proc")) -> list[int]:
    """Dead members of group `pgid` that were reparented to us: the orphans of a leader we already reaped.

    Only Linux procfs is read; elsewhere (or when there is no procfs) the list is empty. When litetraffic is
    pid 1 (a container without an init) nobody else waits for them, and they hold the group open for ever.
    """
    our_pid = os.getpid() if our_pid is None else our_pid
    zombies = []
    try:
        entries = [entry for entry in os.listdir(proc) if entry.isdigit()]
    except OSError:
        return []
    for entry in entries:
        try:
            stat = (proc / entry / "stat").read_text()
        except OSError:
            continue  # gone between listing and reading
        # "pid (comm) state ppid pgrp ...": comm may hold spaces and parentheses, so split after the last ')'.
        fields = stat.rpartition(")")[2].split()
        if len(fields) >= 3 and fields[0] == "Z" and fields[1] == str(our_pid) and fields[2] == str(pgid):
            zombies.append(int(entry))
    return sorted(zombies)


def _group_gone(process: subprocess.Popen[str]) -> bool:
    if os.name != "posix":
        return True  # ponytail: no process groups off POSIX; the leader was waited for above
    deadline = time.monotonic() + GROUP_EXIT_SECONDS
    while True:
        for pid in _adopted_zombies(process.pid):
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass  # reaped meanwhile
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # still there: unreaped zombies on macOS, or members we may not signal
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _stop_process(process: subprocess.Popen[str]) -> tuple[str, str, bool]:
    """Stop `process` and its process group; return (stdout, stderr, group_survived)."""
    _signal_process(process, signal.SIGTERM)
    try:
        stdout, stderr = _communicate(process, timeout=2)
    except subprocess.TimeoutExpired:
        _signal_process(process, signal.SIGKILL)
        try:
            stdout, stderr = _communicate(process, timeout=1)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "process did not exit within 1 second of SIGKILL"
    # The pipes can close while a descendant that ignored SIGTERM lives on (a daemon on /dev/null): kill the rest.
    _signal_process(process, signal.SIGKILL)
    if _group_gone(process):
        return stdout, stderr, False
    return stdout, f"{stderr}\n{GROUP_SURVIVED}".lstrip("\n"), True
