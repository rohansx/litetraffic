from __future__ import annotations

import os

import httpx

from litetraffic.models import FinalObservation, as_matcher, resolve_expected


def _pointer(document: object, path: str) -> object:
    current = document
    for part in path[1:].split("/") if path else []:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdecimal() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise KeyError(path)
    return current


def _matches(matcher: dict, found: bool, actual: object) -> bool:
    (op, operand), = matcher.items()
    if op == "exists":
        return found == operand
    if not found:
        return False
    if op == "eq":
        return actual == operand
    if op == "len":
        return isinstance(actual, (list, str, dict)) and len(actual) == operand
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return actual >= operand if op == "gte" else actual <= operand


def observe(
    target: str,
    config: FinalObservation,
    run_id: str,
    transport: httpx.BaseTransport | None = None,
    fixture_id: str | None = None,
    variables: dict[str, int] | None = None,
) -> dict:
    headers = {"X-LiteTraffic-Run": run_id}
    if fixture_id:
        headers["X-LiteTraffic-Fixture"] = fixture_id
    if config.bearer_token_env:
        token = os.environ.get(config.bearer_token_env)
        if not token:
            return {"assertion": config.assertion, "status": "unknown", "reason": "observer bearer token missing"}
        headers["Authorization"] = f"Bearer {token}"
    for header, env in config.headers_env.items():
        value = os.environ.get(env)
        if not value:
            return {"assertion": config.assertion, "status": "unknown", "reason": f"observer header env {env} missing"}
        headers[header] = value

    try:
        with httpx.Client(transport=transport, timeout=5, follow_redirects=False) as client:
            response = client.get((config.origin or target) + config.path, headers=headers)
        if response.status_code != 200:
            return {"assertion": config.assertion, "status": "unknown", "reason": f"observer HTTP {response.status_code}"}
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"assertion": config.assertion, "status": "unknown", "reason": f"observer unavailable: {type(exc).__name__}"}

    expected_values = resolve_expected(config.expected, variables or {})
    actual, missing, checks = {}, [], {}
    for pointer, expected in expected_values.items():
        try:
            actual[pointer] = _pointer(document, pointer)
        except KeyError:
            actual[pointer] = None
            missing.append(pointer)
        matcher = as_matcher(expected)
        checks[pointer] = {"matcher": matcher, "actual": actual[pointer], "pass": _matches(matcher, pointer not in missing, actual[pointer])}
    result = {
        "assertion": config.assertion,
        "status": "pass" if all(check["pass"] for check in checks.values()) else "fail",
        "expected": expected_values,
        "actual": actual,
        "checks": checks,
    }
    expressions = {pointer: value for pointer, value in config.expected.items() if expected_values[pointer] != value}
    if expressions:
        result["expressions"] = expressions
    if missing:
        result["missing"] = missing
    return result
