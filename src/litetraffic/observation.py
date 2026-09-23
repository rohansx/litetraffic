from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping, Sequence

import httpx

from litetraffic.models import FinalObservation, as_matcher, resolve_expected
from litetraffic.target import normalize_origin

MAX_ACTUAL_BYTES = 2048
REQUEST_DEADLINE_SECONDS = 5.0  # total wall clock per fixture/observer request: connect + headers + full body
MAX_BODY_BYTES = 1 << 20


class DeadlineExceeded(Exception):
    pass


class BodyTooLarge(ValueError):
    pass


def failure(exc: BaseException) -> str:
    """Short reason for a failed bounded_request: 'deadline exceeded', the body-cap message, or the exception name."""
    if isinstance(exc, DeadlineExceeded):
        return "deadline exceeded"
    return str(exc) if isinstance(exc, BodyTooLarge) else type(exc).__name__


def bounded_request(
    method: str, url: str, headers: dict[str, str], transport: httpx.BaseTransport | None = None, body: object = None
) -> tuple[int, bytes]:
    """One request under REQUEST_DEADLINE_SECONDS of wall clock with a MAX_BODY_BYTES cap; returns (status, body)."""
    deadline = time.monotonic() + REQUEST_DEADLINE_SECONDS
    outcome: list = []

    def work() -> None:
        try:
            with httpx.Client(transport=transport, timeout=5, follow_redirects=False) as client:
                with client.stream(method, url, headers=headers, json=body) as response:
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content += chunk
                        if len(content) > MAX_BODY_BYTES:
                            raise BodyTooLarge("response body over 1 MiB")
                        if time.monotonic() > deadline:
                            raise DeadlineExceeded
                    outcome.append((response.status_code, bytes(content)))
        except Exception as exc:
            outcome.append(exc)

    # ponytail: httpx timeouts are per read, so a trickling peer is cut off by join(); a worker stuck
    # mid-headers is abandoned (daemon) until its own 5 s read timeout or the peer closes.
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    if not outcome:
        raise DeadlineExceeded
    if isinstance(outcome[0], Exception):
        raise outcome[0]
    return outcome[0]
TRUNCATED = "...[truncated]"
# Reasons given before any request is sent; every other outcome sent exactly one GET.
PRE_REQUEST = ("observer bearer token", "observer header env", "observer origin env", "observer allowed_origins_env")


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


def _json_equal(a: object, b: object) -> bool:
    """JSON equality: booleans never equal numbers (Python's True == 1), at any depth; 1 == 1.0 still holds."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(_json_equal, a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[key], b[key]) for key in a)
    return a == b


def _matches(matcher: dict, found: bool, actual: object) -> tuple[bool, str | None]:
    """(pass, reason); a reason is given only when an ordering matcher meets a non-number."""
    (op, operand), = matcher.items()
    if op == "exists":
        return found == operand, None
    if not found:
        return False, None
    if op == "eq":
        return _json_equal(actual, operand), None
    if op == "len":
        return isinstance(actual, (list, str, dict)) and len(actual) == operand, None
    for side, value in (("operand", operand), ("", actual)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"{op} needs a number{' operand' if side else ''}, got {_json_type(value)}"
    return (actual >= operand if op == "gte" else actual <= operand), None


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return {dict: "object", list: "array", str: "string"}.get(type(value), "number")


def _recorded(op: str, found: bool, value: object) -> object:
    """What an artifact keeps of the value: a summary for len/exists, else the value capped at 2 KB of JSON."""
    if op == "exists":
        return found
    if not found:
        return None
    if op == "len":
        summary = {"type": _json_type(value)}
        if isinstance(value, (list, str, dict)):
            summary["length"] = len(value)
        return summary
    text = json.dumps(value)  # ASCII-escaped, so characters are bytes
    return value if len(text) <= MAX_ACTUAL_BYTES else text[: MAX_ACTUAL_BYTES - len(TRUNCATED)] + TRUNCATED


def _env_origin(config: FinalObservation, environ: Mapping[str, str], allowed: Sequence[str], allowed_env: str | None) -> tuple[str, str]:
    """Resolve `origin_env` to (origin, "") or ("", reason); the reason names variables, never their values."""
    value = environ.get(config.origin_env)
    if not value:
        return "", f"observer origin env {config.origin_env} missing"
    extra = [entry.strip() for entry in environ.get(allowed_env, "").split(",") if entry.strip()] if allowed_env else []
    try:
        allowed = {normalize_origin(entry, "allowed origin") for entry in (*allowed, *extra)}
    except ValueError:
        return "", f"observer allowed_origins_env {allowed_env} holds an invalid origin"
    try:
        origin = normalize_origin(value, "origin")
    except ValueError:
        origin = ""
    if origin not in allowed:
        return "", f"observer origin env {config.origin_env} is not an allowed origin"
    return origin, ""


def sent_request(result: dict) -> bool:
    return not str(result.get("reason", "")).startswith(PRE_REQUEST)


def observe(
    target: str,
    config: FinalObservation,
    run_id: str,
    transport: httpx.BaseTransport | None = None,
    fixture_id: str | None = None,
    variables: dict[str, int] | None = None,
    environ: Mapping[str, str] | None = None,
    allowed_origins: Sequence[str] = (),
    allowed_origins_env: str | None = None,
) -> dict:
    """Read the final state once. `environ` (default: the process environment) resolves env refs."""
    environ = os.environ if environ is None else environ
    origin = config.origin or target
    if config.origin_env:
        origin, reason = _env_origin(config, environ, allowed_origins, allowed_origins_env)
        if reason:
            return {"assertion": config.assertion, "status": "unknown", "reason": reason}
    headers = {"X-LiteTraffic-Run": run_id}
    if fixture_id:
        headers["X-LiteTraffic-Fixture"] = fixture_id
    if config.bearer_token_env:
        token = environ.get(config.bearer_token_env)
        if not token:
            return {"assertion": config.assertion, "status": "unknown", "reason": "observer bearer token missing"}
        headers["Authorization"] = f"Bearer {token}"
    for header, env in config.headers_env.items():
        value = environ.get(env)
        if not value:
            return {"assertion": config.assertion, "status": "unknown", "reason": f"observer header env {env} missing"}
        headers[header] = value

    try:
        status, content = bounded_request("GET", origin + config.path, headers, transport)
        if status != 200:
            return {"assertion": config.assertion, "status": "unknown", "reason": f"observer HTTP {status}"}
        document = json.loads(content)
    except (httpx.HTTPError, ValueError, DeadlineExceeded) as exc:
        return {"assertion": config.assertion, "status": "unknown", "reason": f"observer unavailable: {failure(exc)}"}

    expected_values = resolve_expected(config.expected, variables or {})
    actual, missing, checks = {}, [], {}
    for pointer, expected in expected_values.items():
        try:
            value, found = _pointer(document, pointer), True
        except KeyError:
            value, found = None, False
            missing.append(pointer)
        matcher = as_matcher(expected)
        actual[pointer] = _recorded(next(iter(matcher)), found, value)
        passed, reason = _matches(matcher, found, value)
        checks[pointer] = {"matcher": matcher, "actual": actual[pointer], "pass": passed}
        if reason:
            checks[pointer]["reason"] = reason
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
