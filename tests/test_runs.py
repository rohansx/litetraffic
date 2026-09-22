import json

import pytest

from litetraffic.runs import RunNotFoundError, list_runs, resolve
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
