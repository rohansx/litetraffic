from __future__ import annotations

import hashlib
from pathlib import Path

MANIFEST = "artifacts.json"


def artifact_files(run_dir: Path) -> list[dict]:
    """Size and sha256 of every run file except the manifest itself, sorted by path."""
    files = []
    for path in sorted(run_dir.rglob("*")):
        relative = path.relative_to(run_dir).as_posix()
        if path.is_file() and relative != MANIFEST:
            data = path.read_bytes()
            files.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return files
