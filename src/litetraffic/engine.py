"""Pre-flight checks shared by every run (the k6 executable and the target URL) and the k6 command line."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from litetraffic.target import validate_target

SUPPORTED_K6_VERSION = "v2.2.0"


class RunnerError(ValueError):
    """The experiment could not be started safely."""


def _engine(k6_path: str | None) -> tuple[str, str]:
    executable = k6_path or shutil.which("k6")
    if not executable:
        raise RunnerError("k6 executable not found")
    try:
        result = subprocess.run([executable, "version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"cannot execute k6: {exc}") from exc
    if result.returncode:
        raise RunnerError((result.stderr or result.stdout).strip() or "k6 version failed")
    version = (result.stdout or result.stderr).strip()
    if len(version.split()) < 2 or version.split()[1] != SUPPORTED_K6_VERSION:
        raise RunnerError(f"unsupported k6 version; expected {SUPPORTED_K6_VERSION}, got {version or 'no version'}")
    return executable, version


def _target(value: str) -> str:
    try:
        return validate_target(value)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc


def k6_command(executable: str, console_path: Path, metrics_path: Path) -> list[str]:
    """k6 run arguments, minus the script: raw console output for evidence, JSON metrics, no redirects."""
    return [
        executable, "run", "--quiet", "--max-redirects", "0", "--log-format", "raw",
        "--console-output", str(console_path), "--out", f"json={metrics_path}",
    ]
