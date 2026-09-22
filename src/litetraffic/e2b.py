from __future__ import annotations

import re


def resolve_target(target: str | None, sandbox_id: str | None, port: int | None) -> str:
    if target and (sandbox_id or port is not None):
        raise ValueError("provide exactly one of target or E2B sandbox coordinates")
    if target:
        return target
    if sandbox_id is None and port is None:
        raise ValueError("target or E2B sandbox coordinates are required")
    if sandbox_id is None or port is None:
        raise ValueError("E2B target requires both sandbox ID and port")
    if not re.fullmatch(r"[A-Za-z0-9-]+", sandbox_id) or len(f"{port}-{sandbox_id}") > 63:
        raise ValueError("invalid E2B sandbox ID")
    if not 1 <= port <= 65535:
        raise ValueError("invalid E2B port")
    return f"https://{port}-{sandbox_id}.e2b.app"
