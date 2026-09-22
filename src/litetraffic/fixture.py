from __future__ import annotations

import os
import re

import httpx

from litetraffic.models import OwnedHttpFixture
from litetraffic.observation import _pointer


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
