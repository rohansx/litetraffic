from __future__ import annotations

import os

import httpx

from litetraffic.models import FinalObservation


def _pointer(document: object, path: str) -> object:
    current = document
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdecimal() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise KeyError(path)
    return current


def observe(
    target: str,
    config: FinalObservation,
    run_id: str,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    headers = {"X-LiteTraffic-Run": run_id}
    if config.bearer_token_env:
        token = os.environ.get(config.bearer_token_env)
        if not token:
            return {"assertion": config.assertion, "status": "unknown", "reason": "observer bearer token missing"}
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(transport=transport, timeout=5, follow_redirects=False) as client:
            response = client.get(target + config.path, headers=headers)
        if response.status_code != 200:
            return {"assertion": config.assertion, "status": "unknown", "reason": f"observer HTTP {response.status_code}"}
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"assertion": config.assertion, "status": "unknown", "reason": f"observer unavailable: {type(exc).__name__}"}

    actual = {}
    missing = []
    for pointer in config.expected:
        try:
            actual[pointer] = _pointer(document, pointer)
        except KeyError:
            actual[pointer] = None
            missing.append(pointer)
    result = {
        "assertion": config.assertion,
        "status": "pass" if not missing and actual == config.expected else "fail",
        "expected": config.expected,
        "actual": actual,
    }
    if missing:
        result["missing"] = missing
    return result
