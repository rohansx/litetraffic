import json
from datetime import UTC, datetime

import pytest

from litetraffic.cli import main
from litetraffic.runs import RunNotFoundError, list_runs, prune, resolve
from test_compare import write_run


def _finish(path, *, scenario, finished_at, seed=42, lifecycle="finished"):
    run = json.loads((path / "run.json").read_text())
    result = json.loads((path / "result.json").read_text())
    run.update({"scenario": scenario, "seed": seed, "lifecycle": lifecycle})
    result.update({"finished_at": finished_at, "seed": seed, "lifecycle": lifecycle})
    (path / "run.json").write_text(json.dumps(run))
    (path / "result.json").write_text(json.dumps(result))
    return path


def test_list_runs_returns_runs_and_series_newest_first(tmp_path):
    old = _finish(write_run(tmp_path / "run_a", "run_a"), scenario="checkout", finished_at="2026-01-01T00:00:00+00:00")
    new = _finish(
        write_run(tmp_path / "run_b", "run_b", verdict="fail"),
        scenario="checkout",
        finished_at="2026-01-03T00:00:00+00:00",
        seed=7,
    )
    series = {
        "schema_version": 1,
        "series_id": "series_x",
        "mode": "repeat",
        "starting_seed": 42,
        "lifecycle": "finished",
        "verdict": "pass",
        "runs": [{"run_id": "run_a", "finished_at": "2026-01-02T00:00:00+00:00"}],
    }
    (tmp_path / "series_x.json").write_text(json.dumps(series))

    runs = list_runs(tmp_path)

    assert [entry["run_id"] for entry in runs] == ["run_b", "series_x", "run_a"]
    assert runs[0] == {
        "run_id": "run_b",
        "kind": "run",
        "scenario": "checkout",
        "verdict": "fail",
        "lifecycle": "finished",
        "finished_at": "2026-01-03T00:00:00+00:00",
        "seed": 7,
        "path": str(new.resolve()),
    }
    assert runs[1] == {
        "run_id": "series_x",
        "kind": "series",
        "scenario": "checkout",
        "verdict": "pass",
        "lifecycle": "finished",
        "finished_at": "2026-01-02T00:00:00+00:00",
        "seed": 42,
        "path": str((tmp_path / "series_x.json").resolve()),
    }
    assert runs[2]["path"] == str(old.resolve())


def test_list_runs_marks_corrupt_and_partial_runs_unreadable(tmp_path):
    corrupt = tmp_path / "run_corrupt"
    corrupt.mkdir()
    (corrupt / "run.json").write_text("{not json")
    partial = tmp_path / "run_partial"
    partial.mkdir()
    (partial / "run.json").write_text(
        json.dumps({"run_id": "run_partial", "scenario": "s", "seed": 1, "lifecycle": "running"})
    )
    (tmp_path / "series_bad.json").write_text("[]")

    runs = {entry["run_id"]: entry for entry in list_runs(tmp_path)}

    assert set(runs) == {"run_corrupt", "run_partial", "series_bad"}
    assert all(entry["verdict"] == "unreadable" for entry in runs.values())
    assert runs["run_partial"]["lifecycle"] == "running"
    assert runs["run_partial"]["scenario"] == "s"
    assert runs["run_corrupt"]["scenario"] is None


def test_list_runs_of_missing_directory_is_empty(tmp_path):
    assert list_runs(tmp_path / "missing") == []


def test_resolve_accepts_a_path_or_a_bare_run_id(tmp_path):
    (tmp_path / "runs").mkdir()
    run = write_run(tmp_path / "runs" / "run_a", "run_a")

    assert resolve(str(run), tmp_path / "elsewhere") == run.resolve()
    assert resolve("run_a", tmp_path / "runs") == run.resolve()


@pytest.mark.parametrize("ref", ["run_missing", "nested/run_a", ""])
def test_resolve_rejects_unknown_ids(tmp_path, ref):
    (tmp_path / "runs" / "nested" / "run_a").mkdir(parents=True)
    with pytest.raises(RunNotFoundError):
        resolve(ref, tmp_path / "runs")


NOW = datetime(2026, 1, 10, tzinfo=UTC)


def _runs(root, *days):
    root.mkdir(exist_ok=True)
    return [
        _finish(write_run(root / f"run_{day:02d}", f"run_{day:02d}"), scenario="s", finished_at=f"2026-01-{day:02d}T00:00:00+00:00")
        for day in days
    ]


def _names(root):
    return sorted(child.name for child in root.iterdir())


def test_prune_keep_deletes_oldest_runs_beyond_n(tmp_path):
    _runs(tmp_path, 1, 2, 3, 4)

    deleted = prune(tmp_path, keep=2)

    assert [path.name for path in deleted] == ["run_02", "run_01"]
    assert _names(tmp_path) == ["run_03", "run_04"]


def test_prune_older_than_deletes_runs_finished_before_cutoff(tmp_path):
    _runs(tmp_path, 1, 5, 9)

    deleted = prune(tmp_path, older_than_days=4, now=NOW)

    assert [path.name for path in deleted] == ["run_05", "run_01"]
    assert _names(tmp_path) == ["run_09"]


@pytest.mark.parametrize("days", [1_000_000, 1e9, 1e300])
def test_prune_older_than_beyond_datetime_range_deletes_nothing(tmp_path, days):
    _runs(tmp_path, 1, 2)

    assert prune(tmp_path, older_than_days=days, now=NOW) == []
    assert _names(tmp_path) == ["run_01", "run_02"]


def test_prune_command_with_huge_older_than_exits_zero(tmp_path, capsys):
    _runs(tmp_path, 1)

    assert main(["prune", "--runs-dir", str(tmp_path), "--older-than", "1000000", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pruned"] == []
    assert _names(tmp_path) == ["run_01"]


def test_prune_dry_run_deletes_nothing(tmp_path):
    _runs(tmp_path, 1, 2, 3)

    assert [path.name for path in prune(tmp_path, keep=1, dry_run=True)] == ["run_02", "run_01"]
    assert _names(tmp_path) == ["run_01", "run_02", "run_03"]


def test_prune_only_touches_run_dirs_directly_under_runs_dir(tmp_path):
    _runs(tmp_path, 1)
    (tmp_path / "notes").mkdir()
    (tmp_path / "series_x.json").write_text("{}")
    _runs(tmp_path / "nested", 2)
    outside = _runs(tmp_path.parent / f"{tmp_path.name}_outside", 3)[0]
    (tmp_path / "run_link").symlink_to(outside, target_is_directory=True)

    assert [path.name for path in prune(tmp_path, keep=0)] == ["run_01"]
    assert _names(tmp_path) == ["nested", "notes", "run_link", "series_x.json"]
    assert (tmp_path / "nested" / "run_02" / "run.json").exists()
    assert (outside / "run.json").exists()


@pytest.mark.parametrize("options", [{}, {"keep": -1}, {"older_than_days": -1}, {"older_than_days": float("nan")}])
def test_prune_rejects_missing_or_invalid_retention(tmp_path, options):
    with pytest.raises(ValueError):
        prune(tmp_path, **options)


def test_prune_command_reports_and_deletes(tmp_path, capsys):
    _runs(tmp_path, 1, 2, 3)

    assert main(["prune", "--runs-dir", str(tmp_path), "--keep", "2", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "dry_run": True, "pruned": [str((tmp_path / "run_01").resolve())]}
    assert _names(tmp_path) == ["run_01", "run_02", "run_03"]

    assert main(["prune", "--runs-dir", str(tmp_path), "--older-than", "0"]) == 0
    assert capsys.readouterr().out == "".join(
        f"deleted: {(tmp_path / name).resolve()}\n" for name in ("run_03", "run_02", "run_01")
    )
    assert _names(tmp_path) == []


def test_prune_command_without_retention_is_an_error(tmp_path, capsys):
    assert main(["prune", "--runs-dir", str(tmp_path), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["ok"] is False


def _fail_rmtree_on(monkeypatch, name):
    import shutil

    real = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if path.name == name:
            raise PermissionError(f"denied: {path}")
        real(path, *args, **kwargs)

    monkeypatch.setattr("litetraffic.runs.shutil.rmtree", rmtree)


def test_prune_command_reports_partial_failure_as_json(tmp_path, capsys, monkeypatch):
    _runs(tmp_path, 1, 2, 3)
    _fail_rmtree_on(monkeypatch, "run_02")

    assert main(["prune", "--runs-dir", str(tmp_path), "--older-than", "0", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["dry_run"] is False
    assert payload["pruned"] == [str((tmp_path / "run_03").resolve())]
    assert payload["failed"] == str((tmp_path / "run_02").resolve())
    assert "denied" in payload["error"]
    assert _names(tmp_path) == ["run_01", "run_02"]


def test_prune_command_reports_partial_failure_as_text(tmp_path, capsys, monkeypatch):
    _runs(tmp_path, 1, 2, 3)
    _fail_rmtree_on(monkeypatch, "run_02")

    assert main(["prune", "--runs-dir", str(tmp_path), "--older-than", "0"]) == 3
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"deleted: {(tmp_path / 'run_03').resolve()}"
    assert lines[1] == f"failed: {(tmp_path / 'run_02').resolve()}"
    assert lines[2].startswith("error: ") and "denied" in lines[2]


@pytest.mark.parametrize("verdict", [[], {}, 5, None])
def test_list_runs_marks_non_string_verdicts_unreadable(tmp_path, verdict):
    run = write_run(tmp_path / "run_bad", "run_bad")
    result = json.loads((run / "result.json").read_text())
    result["verdict"] = verdict
    (run / "result.json").write_text(json.dumps(result))
    (tmp_path / "series_bad.json").write_text(json.dumps({"scenario": "s", "verdict": verdict}))

    runs = {entry["run_id"]: entry for entry in list_runs(tmp_path)}

    assert runs["run_bad"]["verdict"] == "unreadable"
    assert runs["series_bad"]["verdict"] == "unreadable"
