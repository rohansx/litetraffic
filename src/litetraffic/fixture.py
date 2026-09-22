from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from litetraffic.models import OwnedHttpFixture
from litetraffic.observation import _pointer
from litetraffic.process import _communicate, _stop_process


def _headers(config: OwnedHttpFixture, run_id: str) -> dict[str, str] | None:
    headers = {"X-LiteTraffic-Run": run_id}
    if config.bearer_token_env:
        token = os.environ.get(config.bearer_token_env)
        if not token:
            return None
        headers["Authorization"] = f"Bearer {token}"
    return headers


def create_fixture(
    target: str, config: OwnedHttpFixture, run_id: str, transport: httpx.BaseTransport | None = None
) -> dict:
    headers = _headers(config, run_id)
    if headers is None:
        return {"status": "error", "reason": "fixture bearer token missing", "requests": 0}
    try:
        with httpx.Client(transport=transport, timeout=5, follow_redirects=False) as client:
            response = client.post(target + config.create_path, json=config.create_body, headers=headers)
        if response.status_code not in {200, 201}:
            return {"status": "error", "reason": f"fixture create HTTP {response.status_code}", "requests": 1}
        fixture_id = _pointer(response.json(), config.id_pointer)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"status": "error", "reason": f"fixture create unavailable: {type(exc).__name__}", "requests": 1}
    if not isinstance(fixture_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", fixture_id):
        return {"status": "error", "reason": "invalid fixture id", "requests": 1}
    return {"status": "created", "fixture_id": fixture_id, "requests": 1}


STDERR_LIMIT = 4096  # bytes of command stderr kept in fixture.json (the tail, where errors usually are)


def run_fixture_command(stage: str, argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> tuple[dict, str]:
    """Run one fixture hook without a shell; return its fixture.json record and its stdout."""
    record: dict = {"argv": argv, "exit_code": None, "status": "ok", "stderr": ""}
    stdout = ""
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", start_new_session=os.name == "posix",
        )
    except OSError as exc:
        record.update(status="error", reason=f"fixture {stage} failed: {exc.strerror or type(exc).__name__}")
    else:
        try:
            stdout, stderr = _communicate(process, timeout=timeout)
            record["exit_code"] = process.returncode
            if process.returncode:
                record.update(status="error", reason=f"fixture {stage} failed: exit {process.returncode}")
        except subprocess.TimeoutExpired:
            stdout, stderr = _stop_process(process)
            record.update(status="error", reason=f"fixture {stage} failed: timed out after {timeout}s")
        except KeyboardInterrupt:
            stdout, stderr = _stop_process(process)
            record.update(status="cancelled", reason=f"fixture {stage} cancelled")
        record["stderr"] = stderr.encode()[-STDERR_LIMIT:].decode(errors="ignore")
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    return record, stdout


def fixture_json(stdout: str) -> str | None:
    """The last stdout line when it is a JSON object, else None."""
    lines = stdout.strip().splitlines()
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    return json.dumps(value) if isinstance(value, dict) else None


def cleanup_fixture(
    target: str, config: OwnedHttpFixture, run_id: str, fixture_id: str, transport: httpx.BaseTransport | None = None
) -> dict:
    headers = _headers(config, run_id)
    if headers is None:
        return {"status": "error", "reason": "fixture bearer token missing", "requests": 0}
    try:
        with httpx.Client(transport=transport, timeout=5, follow_redirects=False) as client:
            response = client.delete(target + config.delete_path.replace("{fixture_id}", fixture_id), headers=headers)
    except httpx.HTTPError as exc:
        return {"status": "error", "reason": f"fixture cleanup unavailable: {type(exc).__name__}", "requests": 1}
    if response.status_code not in {200, 204}:
        return {"status": "error", "reason": f"fixture cleanup HTTP {response.status_code}", "requests": 1}
    return {"status": "deleted", "requests": 1}
