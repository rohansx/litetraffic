import json

from litetraffic.cli import main
from litetraffic.runs import list_runs, prune
from test_compare import write_run
from test_dashboard import get, runs_dir, server  # noqa: F401 - pytest fixtures
from test_runs import _finish

SENTINEL = "SENTINEL_OUTSIDE_RUNS_DIR"


def _outside(tmp_path, name="outside"):
    return _finish(write_run(tmp_path / name, name, verdict="pass"), scenario=SENTINEL, finished_at="2026-03-01T00:00:00+00:00")


def test_symlinked_run_dir_and_series_file_are_not_listed(tmp_path, runs_dir, server):
    outside = _outside(tmp_path)
    (runs_dir / "run_link").symlink_to(outside, target_is_directory=True)
    series = tmp_path / "series_real.json"
    series.write_text(json.dumps({"verdict": "pass", "runs": [{"run_id": "outside"}], "starting_seed": 1}))
    (runs_dir / "series_link.json").symlink_to(series)

    ids = [entry["run_id"] for entry in list_runs(runs_dir)]
    assert "run_link" not in ids and "series_link" not in ids
    assert SENTINEL not in get(server, "/")[2]
    assert get(server, "/runs/run_link")[0] == 404


def test_series_never_reads_run_json_outside_runs_dir(runs_dir):
    _outside(runs_dir.parent, "escape")
    (runs_dir / "series_x.json").write_text(json.dumps({"verdict": "pass", "runs": [{"run_id": "../escape"}]}))

    [series] = [entry for entry in list_runs(runs_dir) if entry["kind"] == "series"]
    assert series["scenario"] is None


def test_metadata_symlinks_to_external_files_are_unreadable(tmp_path, runs_dir, server):
    outside = _outside(tmp_path)
    run = runs_dir / "run_evil"
    run.mkdir()
    (run / "run.json").symlink_to(outside / "run.json")
    (run / "result.json").symlink_to(outside / "result.json")

    [entry] = [entry for entry in list_runs(runs_dir) if entry["run_id"] == "run_evil"]
    assert entry["verdict"] == "unreadable"
    assert entry["scenario"] is None
    for path in ("/", "/api/runs", "/runs/run_evil", "/diff?a=run_evil&b=run_a", f"/scenarios/{SENTINEL}"):
        assert SENTINEL not in get(server, path)[2]


def test_activity_symlink_is_not_read(tmp_path, runs_dir, server):
    (tmp_path / "activity.json").write_text(json.dumps({"status": SENTINEL}))
    run = runs_dir / "act"
    run.mkdir()
    (run / "activity.json").symlink_to(tmp_path / "activity.json")

    assert SENTINEL not in get(server, "/")[2]
    status, _, body = get(server, "/runs/act")
    assert status == 200 and SENTINEL not in body
    assert "Activity act" not in body  # a symlinked activity.json is missing, so this renders as an unreadable run


def test_prune_never_reads_symlinked_metadata(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    real = _finish(write_run(root / "run_real", "run_real"), scenario="s", finished_at="2026-01-01T00:00:00+00:00")
    outside = _finish(write_run(tmp_path / "outside", "outside"), scenario="s", finished_at="2099-01-01T00:00:00+00:00")
    (real / "result.json").unlink()
    (real / "result.json").symlink_to(outside / "result.json")
    evil = root / "run_evil"
    evil.mkdir()
    (evil / "run.json").symlink_to(outside / "run.json")

    assert prune(root, keep=0) == [real.resolve()]
    assert (outside / "result.json").exists() and (outside / "run.json").exists()
    assert (root / "run_evil").is_dir()


def test_diff_by_run_id_refuses_symlinked_run_dir(tmp_path, capsys):
    root = tmp_path / "runs"
    root.mkdir()
    write_run(root / "a", "a")
    (root / "link").symlink_to(_outside(tmp_path), target_is_directory=True)
    (root / "inner").symlink_to(root / "a", target_is_directory=True)

    assert main(["diff", "a", "link", "--runs-dir", str(root)]) == 3
    assert main(["diff", "a", "inner", "--runs-dir", str(root)]) == 3
    assert SENTINEL not in capsys.readouterr().out


def test_diff_refuses_symlinked_run_artifacts(tmp_path, capsys):
    outside = _outside(tmp_path)
    run = tmp_path / "runs" / "run_evil"
    run.mkdir(parents=True)
    (run / "run.json").symlink_to(outside / "run.json")
    (run / "result.json").symlink_to(outside / "result.json")

    assert main(["diff", str(outside), str(run)]) == 3
    assert "must not be symlinks" in capsys.readouterr().out


def test_series_never_reads_run_json_through_symlinked_run_dir(tmp_path, runs_dir):
    (runs_dir / "linked").symlink_to(_outside(tmp_path), target_is_directory=True)
    (runs_dir / "series_y.json").write_text(json.dumps({"verdict": "pass", "runs": [{"run_id": "linked"}]}))

    [series] = [entry for entry in list_runs(runs_dir) if entry["run_id"] == "series_y"]
    assert series["scenario"] is None


def test_symlink_to_sibling_run_dir_is_404(runs_dir, server):
    [real] = [child for child in runs_dir.iterdir() if child.is_dir()][:1]
    (runs_dir / "alias").symlink_to(real, target_is_directory=True)

    assert get(server, "/runs/alias")[0] == 404
