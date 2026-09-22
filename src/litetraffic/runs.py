"""Index of run artifacts in an output directory; shared by `diff` and the dashboard."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class RunNotFoundError(ValueError):
    """A run reference names neither an existing directory nor a run ID."""


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _entry(path: Path, run_id: str, kind: str, data: dict, verdict: object, **fields: object) -> dict:
    # Anything we cannot trust is reported as unreadable rather than raised.
    readable = verdict in {"pass", "fail", "inconclusive", "error"}
    entry = {
        "run_id": run_id,
        "kind": kind,
        "scenario": data.get("scenario"),
        "verdict": verdict if readable else "unreadable",
        "lifecycle": data.get("lifecycle"),
        "finished_at": data.get("finished_at"),
        "seed": data.get("seed"),
        "path": str(path.resolve()),
    }
    return {**entry, **fields}


def _run(path: Path) -> dict:
    run, result = _read(path / "run.json") or {}, _read(path / "result.json") or {}
    data = {**run, **result}
    return _entry(path, path.name, "run", data, result.get("verdict"))


def _series(path: Path, runs_dir: Path) -> dict:
    series = _read(path) or {}
    runs = series.get("runs") if isinstance(series.get("runs"), list) else []
    first = runs[0] if runs and isinstance(runs[0], dict) else {}
    last = runs[-1] if runs and isinstance(runs[-1], dict) else {}
    scenario = (_read(runs_dir / str(first.get("run_id", "")) / "run.json") or {}).get("scenario") if first else None
    return _entry(
        path,
        path.stem,
        "series",
        series,
        series.get("verdict"),
        scenario=scenario,
        finished_at=last.get("finished_at"),
        seed=series.get("starting_seed"),
    )


def _sort_key(entry: dict) -> str:
    stamp = entry["finished_at"]
    if isinstance(stamp, str):
        return stamp
    # ponytail: unfinished/unreadable runs sort by mtime, which assumes ISO UTC finished_at stamps.
    return datetime.fromtimestamp(Path(entry["path"]).stat().st_mtime, UTC).isoformat()


def list_runs(runs_dir: Path) -> list[dict]:
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    entries = [
        _run(child) if child.is_dir() else _series(child, root)
        for child in root.iterdir()
        if child.is_dir() or (child.name.startswith("series_") and child.suffix == ".json")
    ]
    return sorted(entries, key=_sort_key, reverse=True)


def resolve(ref: str, runs_dir: Path) -> Path:
    if ref and Path(ref).is_dir():
        return Path(ref).resolve()
    candidate = Path(runs_dir) / ref
    if ref and Path(ref).name == ref and ref not in {".", ".."} and candidate.is_dir():
        return candidate.resolve()
    raise RunNotFoundError(f"no run directory or run ID {ref!r} in {Path(runs_dir).resolve()}")
