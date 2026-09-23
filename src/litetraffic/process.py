"""Engine subprocess control: bounded waits and process-group termination."""

from __future__ import annotations

import os
import signal
import subprocess
import time

STOP_GRACE_SECONDS = 4  # _stop_process waits 2 s after SIGTERM, 1 s after SIGKILL, then up to 1 s for the group
GROUP_EXIT_SECONDS = 1.0  # killed descendants are reaped by init, not by us; give that a moment
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


def _group_gone(process: subprocess.Popen[str]) -> bool:
    if os.name != "posix":
        return True  # ponytail: no process groups off POSIX; the leader was waited for above
    deadline = time.monotonic() + GROUP_EXIT_SECONDS
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
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
