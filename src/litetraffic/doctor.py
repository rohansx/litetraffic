from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from litetraffic.runner import SUPPORTED_K6_VERSION
from litetraffic.target import validate_target

MIN_PYTHON = (3, 11)
# ponytail: fixed floor, not calibrated to scenario size; make it a flag if runs outgrow it
MIN_FREE_BYTES = 100 * 1024 * 1024
DEFAULT_OUTPUT_DIR = Path(".litetraffic/runs")


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    ok: bool
    detail: str


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _check_k6(k6_path: str | None) -> Check:
    executable = k6_path or shutil.which("k6")
    if not executable:
        return Check(name="k6", ok=False, detail="k6 executable not found")
    try:
        result = subprocess.run(
            [executable, "version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(name="k6", ok=False, detail=f"cannot execute k6: {exc}")
    detail = (result.stdout or result.stderr).strip()
    if result.returncode:
        return Check(name="k6", ok=False, detail=detail or "k6 version failed")
    words = detail.split()
    if len(words) < 2 or words[1] != SUPPORTED_K6_VERSION:
        return Check(
            name="k6",
            ok=False,
            detail=f"unsupported k6 version; expected {SUPPORTED_K6_VERSION}, got {detail or 'no version'}",
        )
    return Check(name="k6", ok=True, detail=detail)


def _check_python() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = tuple(sys.version_info[:2]) >= MIN_PYTHON
    required = ".".join(map(str, MIN_PYTHON))
    return Check(name="python", ok=ok, detail=f"Python {version}" + ("" if ok else f"; requires {required}+"))


def _existing_ancestor(path: Path) -> Path:
    path = path.absolute()
    while not path.exists():
        path = path.parent
    return path


def _check_output_dir(output_dir: Path) -> Check:
    base = _existing_ancestor(output_dir)
    if base.is_dir() and os.access(base, os.W_OK | os.X_OK):
        return Check(name="output_dir", ok=True, detail=f"{output_dir} is writable")
    return Check(name="output_dir", ok=False, detail=f"{output_dir} is not writable ({base})")


def _check_disk(output_dir: Path) -> Check:
    try:
        free = shutil.disk_usage(_existing_ancestor(output_dir)).free
    except OSError as exc:
        return Check(name="disk", ok=False, detail=f"cannot read disk usage: {exc}")
    ok = free >= MIN_FREE_BYTES
    detail = f"{free} bytes free" + ("" if ok else f"; requires at least {MIN_FREE_BYTES}")
    return Check(name="disk", ok=ok, detail=detail)


def run_doctor(
    target: str | None = None,
    k6_path: str | None = None,
    transport: httpx.BaseTransport | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> DoctorReport:
    checks = [_check_k6(k6_path), _check_python(), _check_output_dir(output_dir), _check_disk(output_dir)]
    if target is None:
        return DoctorReport(checks=checks)

    validate_target(target)
    try:
        with httpx.Client(transport=transport, timeout=3, follow_redirects=False) as client:
            response = client.get(target)
        status = response.status_code
        if 200 <= status < 400:
            checks.append(Check(name="target", ok=True, detail=f"ready with HTTP {status}"))
        else:
            checks.append(Check(name="target", ok=False, detail=f"reachable but not ready (HTTP {status})"))
    except httpx.HTTPError as exc:
        checks.append(Check(name="target", ok=False, detail=f"unreachable: {exc}"))
    return DoctorReport(checks=checks)

