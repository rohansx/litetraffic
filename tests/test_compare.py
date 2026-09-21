import json
from pathlib import Path

import pytest

from litetraffic.compare import ComparisonError, compare_runs


def write_run(
    path: Path,
    run_id: str,
    *,
    scenario_sha256: str = "same-scenario",
    verdict: str = "pass",
    p95: float = 100,
    samples: int = 300,
) -> Path:
    path.mkdir()
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "seed": 42,
        "scenario_sha256": scenario_sha256,
        "engine": "k6 v2.2.0",
        "resolved_schedule": [{"name": "measure", "seconds": 10, "rate": 2}],
    }
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "verdict": verdict,
        "assertions": [
            {"id": "report_complete", "status": "fail" if verdict == "fail" else "pass", "samples": samples}
        ],
        "metrics": {
            "iterations": samples,
            "http_reqs": samples,
            "http_req_duration_ms": {"samples": samples, "p95": p95},
            "http_req_failed_rate": {"samples": samples, "failed": 0, "rate": 0},
            "iterations_per_second": 20,
            "http_reqs_per_second": 20,
        },
    }
    (path / "run.json").write_text(json.dumps(run))
    (path / "result.json").write_text(json.dumps(result))
    return path


def test_compare_runs_fails_an_explicit_p95_regression_gate(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline", p95=100)
    candidate = write_run(tmp_path / "candidate", "candidate", p95=130)

    comparison = compare_runs(baseline, candidate, max_p95_regression_percent=20)

    assert comparison["comparable"] is True
    assert comparison["verdict"] == "fail"
    assert comparison["correctness"]["regression"] is False
    assert comparison["performance"]["p95"] == {
        "baseline_ms": 100.0,
        "candidate_ms": 130.0,
        "change_percent": 30.0,
        "threshold_percent": 20,
        "samples": {"baseline": 300, "candidate": 300},
        "status": "regression",
    }
    assert comparison["performance"]["http_error_rate"] == {
        "baseline": 0.0,
        "candidate": 0.0,
        "change_percentage_points": 0.0,
    }
    assert comparison["performance"]["http_reqs_per_second"] == {
        "baseline": 20.0,
        "candidate": 20.0,
        "change_percent": 0.0,
    }
    assert comparison["progress"] == {
        "iterations": {"baseline": 300, "candidate": 300},
        "http_reqs": {"baseline": 300, "candidate": 300},
    }


def test_compare_runs_refuses_to_grade_incompatible_scenarios(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline", scenario_sha256="scenario-a", p95=100)
    candidate = write_run(tmp_path / "candidate", "candidate", scenario_sha256="scenario-b", p95=200)

    comparison = compare_runs(baseline, candidate, max_p95_regression_percent=20)

    assert comparison["comparable"] is False
    assert comparison["incompatibilities"] == ["scenario_sha256"]
    assert comparison["verdict"] == "inconclusive"
    assert comparison["performance"]["p95"]["change_percent"] == 100.0
    assert comparison["performance"]["p95"]["status"] == "incomparable"


def test_compare_runs_makes_a_low_sample_latency_gate_inconclusive(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline", p95=100, samples=50)
    candidate = write_run(tmp_path / "candidate", "candidate", p95=130, samples=50)

    comparison = compare_runs(baseline, candidate, max_p95_regression_percent=20)

    assert comparison["verdict"] == "inconclusive"
    assert comparison["performance"]["p95"]["status"] == "inconclusive"


def test_compare_runs_reports_a_business_assertion_regression(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline")
    candidate = write_run(tmp_path / "candidate", "candidate", verdict="fail")

    comparison = compare_runs(baseline, candidate)

    assert comparison["verdict"] == "fail"
    assert comparison["correctness"]["regression"] is True
    assert comparison["correctness"]["assertion_regressions"] == ["report_complete"]


def test_compare_runs_rejects_a_non_finite_latency_gate(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline")
    candidate = write_run(tmp_path / "candidate", "candidate")

    with pytest.raises(ComparisonError, match="finite non-negative"):
        compare_runs(baseline, candidate, max_p95_regression_percent=float("nan"))


def test_compare_runs_rejects_malformed_result_artifacts(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline")
    candidate = write_run(tmp_path / "candidate", "candidate")
    result_path = candidate / "result.json"
    result = json.loads(result_path.read_text())
    result["metrics"]["http_req_duration_ms"] = []
    result_path.write_text(json.dumps(result))

    with pytest.raises(ComparisonError, match="invalid result artifact"):
        compare_runs(baseline, candidate)


def test_compare_runs_does_not_pass_a_non_finite_latency_measurement(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline")
    candidate = write_run(tmp_path / "candidate", "candidate", p95=float("nan"))

    comparison = compare_runs(baseline, candidate, max_p95_regression_percent=20)

    assert comparison["verdict"] == "inconclusive"
    assert comparison["performance"]["p95"]["status"] == "unavailable"


def test_compare_runs_treats_invalid_sample_counts_as_inconclusive(tmp_path):
    baseline = write_run(tmp_path / "baseline", "baseline")
    candidate = write_run(tmp_path / "candidate", "candidate")
    result_path = candidate / "result.json"
    result = json.loads(result_path.read_text())
    result["metrics"]["http_req_duration_ms"]["samples"] = "many"
    result_path.write_text(json.dumps(result))

    comparison = compare_runs(baseline, candidate, max_p95_regression_percent=20)

    assert comparison["verdict"] == "inconclusive"
    assert comparison["performance"]["p95"]["samples"]["candidate"] == 0
