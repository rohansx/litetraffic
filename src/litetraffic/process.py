"""Engine subprocess control: bounded waits and process-group termination."""

from __future__ import annotations

import os
import signal
import subprocess


def _communicate(process: subprocess.Popen[str], timeout: float) -> tuple[str, str]:
    return process.communicate(timeout=timeout)


def _signal_process(process: subprocess.Popen[str], value: signal.Signals) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, value)
        elif value == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    _signal_process(process, signal.SIGTERM)
    try:
        return _communicate(process, timeout=2)
    except subprocess.TimeoutExpired:
        _signal_process(process, signal.SIGKILL)
        try:
            return _communicate(process, timeout=2)
        except subprocess.TimeoutExpired:
            return "", "process did not exit within 2 seconds of SIGKILL"
