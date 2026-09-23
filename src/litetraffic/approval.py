from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from litetraffic.runner import _write_text
from litetraffic.target import validate_target

APPROVALS_PATH = Path(".litetraffic/approvals.json")  # relative to the working directory
_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin(target: str) -> str:
    """Normalize a target URL to scheme://host[:port], dropping path and default port."""
    parts = urlsplit(validate_target(target))  # canonical host; rejects link-local/metadata hosts
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError(f"target must be an http(s) URL with a host: {target!r}")
    if parts.username or parts.password:
        raise ValueError("target must not contain URL credentials")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = parts.port  # raises ValueError for an invalid port
    return f"{scheme}://{host}" + (f":{port}" if port not in (None, _DEFAULT_PORTS[scheme]) else "")


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))["approvals"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read approvals file {path}: {exc}") from exc
    if not isinstance(records, list) or not all(
        isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("digest", "profile", "target_origin"))
        for r in records
    ):
        raise ValueError(f"invalid approvals file {path}: expected a list of records with str digest, profile and target_origin")
    return records


def approve(digest: str, profile: str, target: str, path: Path = APPROVALS_PATH) -> dict:
    """Bind a bundle digest to a target profile and origin; re-approving the same binding refreshes it."""
    record = {
        "digest": digest,
        "profile": profile,
        "target_origin": origin(target),
        "approved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    key = (digest, profile, record["target_origin"])
    kept = [r for r in _records(path) if (r["digest"], r["profile"], r["target_origin"]) != key]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, json.dumps({"approvals": [*kept, record]}, indent=2, sort_keys=True) + "\n")
    return record


def require_approval(digest: str, target: str, approved_digest: str | None, path: Path = APPROVALS_PATH) -> None:
    """Raise ValueError unless the digest is approved for the target origin (or matches --approved-digest)."""
    if approved_digest is not None:
        if approved_digest.lower() != digest:
            raise ValueError(f"--approved-digest does not match the scenario digest {digest}")
        return
    target_origin = origin(target)
    if not any(r["digest"] == digest and r["target_origin"] == target_origin for r in _records(path)):
        raise ValueError(f"scenario digest {digest} is not approved for {target_origin}; run 'litetraffic approve'")
