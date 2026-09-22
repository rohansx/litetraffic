import json
import stat

import pytest

import litetraffic.activity as activity
from litetraffic.activity import up
from litetraffic.runner import RunnerError
from test_runner import assertion, fake_k6
from test_scenario import write_bundle


def _events(monkeypatch):
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    return events


def test_up_runs_bounded_slices_and_writes_a_verdict_free_activity(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    k6 = fake_k6(tmp_path, _events(monkeypatch))

    result = up("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(k6), seed=7, max_slices=2)

    activity_dir = tmp_path / "runs" / result["activity_id"]
    path = activity_dir / "activity.json"
    assert json.loads(path.read_text()) == result
    assert "verdict" not in path.read_text()
    assert result["mode"] == "background"
    assert result["status"] == "completed"
    assert result["scenario"] == "checkout"
    assert result["artifact_dir"] == str(activity_dir.resolve())
    assert [item["seed"] for item in result["slices"]] == [7, 8]
    for item in result["slices"]:
        assert item["lifecycle"] == "finished"
        assert (activity_dir / item["run_id"] / "result.json").is_file()
    assert result["finished_at"]
    assert stat.S_IMODE(activity_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_up_ctrl_c_between_slices_finalizes_as_stopped(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    k6 = fake_k6(tmp_path, _events(monkeypatch))
    seeds = []

    def slice_then_interrupt(target, scenario, output_dir, k6_path, seed):
        if seeds:
            raise KeyboardInterrupt
        seeds.append(seed)
        return {"run_id": "run_one", "seed": seed, "lifecycle": "finished", "verdict": "pass", "metrics": {}}

    monkeypatch.setattr(activity, "verify", slice_then_interrupt)

    result = up("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(k6))

    assert result["status"] == "stopped"
    assert [item["run_id"] for item in result["slices"]] == ["run_one"]
    saved = json.loads((tmp_path / "runs" / result["activity_id"] / "activity.json").read_text())
    assert saved["status"] == "stopped" and saved["finished_at"]


def test_up_stops_when_a_slice_is_cancelled(tmp_path, monkeypatch):
    scenario = write_bundle(tmp_path / "scenario")
    k6 = fake_k6(tmp_path, _events(monkeypatch))
    monkeypatch.setattr(
        activity,
        "verify",
        lambda *args: {"run_id": "run_x", "seed": 0, "lifecycle": "cancelled", "verdict": "inconclusive", "metrics": {}},
    )

    result = up("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(k6))

    assert result["status"] == "stopped"
    assert len(result["slices"]) == 1


def test_up_rejects_non_positive_max_slices(tmp_path):
    with pytest.raises(RunnerError, match="max-slices"):
        up("http://127.0.0.1:8000", tmp_path, tmp_path / "runs", max_slices=0)


def test_up_validates_before_creating_an_activity(tmp_path):
    scenario = write_bundle(tmp_path / "scenario")

    with pytest.raises(RunnerError, match="k6"):
        up("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(tmp_path / "missing-k6"), max_slices=1)

    assert not (tmp_path / "runs").exists()
