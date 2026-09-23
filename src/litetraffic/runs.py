"""Index of run artifacts in an output directory; shared by `diff`, the dashboard and `prune`."""

from __future__ import annotations

import json
import math
import shutil
from datetime import UTC, datetime, timedelta
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


def _activity(path: Path) -> dict:
    activity = _read(path / "activity.json") or {}
    # A background activity has a status, never a verdict; "background" is its badge.
    entry = _entry(path, path.name, "activity", activity, None, lifecycle=activity.get("status"), seed=activity.get("starting_seed"))
    return {**entry, "verdict": "background"}


def _sort_key(entry: dict) -> str:
    stamp = entry["finished_at"]
    if isinstance(stamp, str):
        return stamp
    # ponytail: unfinished/unreadable runs sort by mtime, which assumes ISO UTC finished_at stamps.
    try:
        return datetime.fromtimestamp(Path(entry["path"]).stat().st_mtime, UTC).isoformat()
    except OSError:  # deleted since it was listed: sorts last
        return ""


def list_runs(runs_dir: Path) -> list[dict]:
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    entries = [
        (_activity(child) if (child / "activity.json").is_file() else _run(child)) if child.is_dir() else _series(child, root)
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


def _finished(entry: dict) -> datetime:
    try:
        stamp = datetime.fromisoformat(_sort_key(entry))
    except ValueError:
        stamp = datetime.fromtimestamp(Path(entry["path"]).stat().st_mtime, UTC)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def prune(
    runs_dir: Path,
    keep: int | None = None,
    older_than_days: float | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[Path]:
    """Delete run directories beyond the newest `keep` or finished more than `older_than_days` ago.

    Only real (non-symlink) directories directly under `runs_dir` that contain run.json are candidates.
    Returns the selected paths, newest first; with `dry_run` nothing is deleted.
    """
    if keep is None and older_than_days is None:
        raise ValueError("prune needs --keep, --older-than, or both")
    if keep is not None and keep < 0:
        raise ValueError("--keep must be at least 0")
    if older_than_days is not None and not (math.isfinite(older_than_days) and older_than_days >= 0):
        raise ValueError("--older-than must be a finite number of days, at least 0")
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    candidates = [
        _run(child)
        for child in root.iterdir()
        if child.is_dir() and not child.is_symlink() and (child / "run.json").is_file()
    ]
    candidates.sort(key=_finished, reverse=True)
    cutoff = None
    if older_than_days is not None:
        try:
            cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
        except OverflowError:  # cutoff predates datetime.min: no run is old enough
            cutoff = datetime.min.replace(tzinfo=UTC)
    selected = [
        Path(entry["path"])
        for index, entry in enumerate(candidates)
        if (keep is not None and index >= keep) or (cutoff is not None and _finished(entry) < cutoff)
    ]
    if not dry_run:
        for path in selected:
            shutil.rmtree(path)
    return selected
