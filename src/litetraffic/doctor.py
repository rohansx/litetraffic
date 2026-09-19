from __future__ import annotations

import shutil
import subprocess
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict


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
    return Check(name="k6", ok=result.returncode == 0, detail=detail or "k6 returned no version")


def run_doctor(
    target: str | None = None,
    k6_path: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> DoctorReport:
    checks = [_check_k6(k6_path)]
    if target is None:
        return DoctorReport(checks=checks)

    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("target must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("target URL must not contain credentials")
    try:
        with httpx.Client(transport=transport, timeout=3, follow_redirects=False) as client:
            response = client.get(target)
        checks.append(Check(name="target", ok=True, detail=f"reachable with HTTP {response.status_code}"))
    except httpx.HTTPError as exc:
        checks.append(Check(name="target", ok=False, detail=f"unreachable: {exc}"))
    return DoctorReport(checks=checks)

