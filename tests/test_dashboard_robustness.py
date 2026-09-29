import re
import shutil

from litetraffic import runs
from litetraffic.report import _failure_tables
from test_compare import write_run
from test_dashboard import get, runs_dir, server, ui_dir  # noqa: F401 - pytest fixtures
from test_runs import _finish


def test_long_failing_sample_values_collapse_into_details():
    long_value = "x" * 301
    html = _failure_tables(
        [{"id": "a", "status": "fail", "failures": [{"expected": "short", "actual": long_value, "detail": "y" * 300}]}]
    )

    assert html.count("<details>") == 1
    assert "<td>short</td>" in html and f"<td>{'y' * 300}</td>" in html
    summary = re.search(r"<summary>(.*?)</summary>", html).group(1)
    assert len(summary) < 300 and summary.startswith("xxx")
    assert long_value in html.split("</summary>")[1]


def test_list_runs_tolerates_a_run_deleted_mid_listing(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    write_run(root / "gone", "gone")  # no finished_at, so it sorts by mtime
    _finish(write_run(root / "kept", "kept"), scenario="s", finished_at="2026-01-01T00:00:00+00:00")
    original = runs._run

    def vanishing(path):
        entry = original(path)
        if path.name == "gone":
            shutil.rmtree(path)
        return entry

    monkeypatch.setattr(runs, "_run", vanishing)

    assert "kept" in {entry["run_id"] for entry in runs.list_runs(root)}


def test_index_does_not_fail_when_a_run_vanishes(server, runs_dir, monkeypatch):
    write_run(runs_dir / "gone", "gone")
    original = runs._run

    def vanishing(path):
        entry = original(path)
        if path.name == "gone" and path.exists():
            shutil.rmtree(path)
        return entry

    monkeypatch.setattr(runs, "_run", vanishing)

    assert get(server, "/api/runs")[0] == 200
