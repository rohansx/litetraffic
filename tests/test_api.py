import json

import litetraffic
from litetraffic.cli import main
from test_runner import assertion, fake_k6
from test_scenario import write_bundle

PUBLIC = ["verify", "repeat_verify", "compare_runs", "load_scenario", "run_doctor", "list_runs"]
# Fields that differ between any two runs, however they are started.
VOLATILE = {"run_id", "finished_at", "metrics"}


def test_public_api_is_importable_and_declared():
    from litetraffic import compare_runs, list_runs, load_scenario, repeat_verify, run_doctor, verify  # noqa: F401

    assert sorted(litetraffic.__all__) == sorted([*PUBLIC, "__version__"])
    assert all(callable(getattr(litetraffic, name)) for name in PUBLIC)


def test_library_verify_returns_the_same_result_as_the_cli(tmp_path, monkeypatch, capsys):
    scenario = write_bundle(tmp_path / "scenario")
    events = [assertion("accepted_orders_persist", True) for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    k6 = fake_k6(tmp_path, events)

    library = litetraffic.verify("http://example.test", scenario, tmp_path / "runs", str(k6))
    main(["verify", str(scenario), "--target", "http://example.test", "--output-dir", str(tmp_path / "runs"), "--k6-path", str(k6), "--json"])
    cli = json.loads(capsys.readouterr().out)

    assert library["verdict"] == "pass"
    assert {k: v for k, v in library.items() if k not in VOLATILE} == {k: v for k, v in cli.items() if k not in VOLATILE}
    assert library["metrics"].keys() == cli["metrics"].keys()
