import json

import pytest

from litetraffic.compare import compare_runs
from litetraffic.evidence import _read_metrics
from litetraffic.human import format_diff, format_verify
from litetraffic.models import Journey
from litetraffic.report import render_report
from litetraffic.runner import verify
from test_compare import write_run
from test_runner import assertion, fake_k6, point
from test_scenario import manifest, write_bundle

UNEXPECTED = {"samples": 6, "failed": 3, "rate": 0.5}


def _journeys(expected: dict) -> list[Journey]:
    return [Journey(name="purchase", max_requests=3, max_writes=1, expected_statuses=expected)]


def _write_points(tmp_path):
    path = tmp_path / "metrics.jsonl"
    lines = [
        point("http_req_failed", 1, operation="create", status="409"),  # declared rejection
        point("http_req_failed", 1, operation="create", status="500"),
        point("http_req_failed", 0, operation="create", status="201"),
        point("http_req_failed", 1, operation="create", status="0"),  # transport failure still counts
        point("http_req_failed", 1, operation="read", status="404"),  # no declaration: k6 decides
        point("http_req_failed", 0, operation="read", status="200"),
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_unexpected_failure_rate_excludes_declared_statuses(tmp_path):
    metrics, malformed = _read_metrics(_write_points(tmp_path), _journeys({"create": [409]}))

    assert malformed == 0
    assert metrics["unexpected_http_failure_rate"] == UNEXPECTED
    assert metrics["http_req_failed_rate"] == {"samples": 6, "failed": 4, "rate": 0.666667, "transport": 1}


def test_expected_statuses_merge_across_journeys(tmp_path):
    journeys = _journeys({"create": [409]}) + [
        Journey(name="refund", max_requests=1, max_writes=1, expected_statuses={"create": [500], "read": [404]})
    ]

    metrics, _ = _read_metrics(_write_points(tmp_path), journeys)

    assert metrics["unexpected_http_failure_rate"] == {"samples": 6, "failed": 1, "rate": 0.166667}


def test_unexpected_failure_rate_is_absent_without_declarations(tmp_path):
    assert "unexpected_http_failure_rate" not in _read_metrics(_write_points(tmp_path))[0]
    assert "unexpected_http_failure_rate" not in _read_metrics(_write_points(tmp_path), _journeys({}))[0]


@pytest.mark.parametrize("expected", [{"create": [99]}, {"create": [600]}, {"create": []}, {"": [409]}, {"create": [True]}])
def test_expected_statuses_reject_invalid_declarations(expected):
    with pytest.raises(Exception, match="expected_statuses"):
        _journeys(expected)


def _result(**metrics) -> dict:
    return {"run_id": "run_1", "verdict": "pass", "lifecycle": "finished", "planned_journeys": 2, "assertions": [],
            "limitations": [], "metrics": {"iterations": 2} | metrics}


def test_report_and_human_verify_show_unexpected_failure_rate_only_when_defined(tmp_path):
    run = {"scenario": "checkout", "seed": 1, "target": "http://example.test", "engine": "k6"}

    report = render_report(_result(unexpected_http_failure_rate=UNEXPECTED), run)
    assert "<dt>Unexpected HTTP failure rate</dt><dd>50.0%</dd>" in report
    assert "Unexpected HTTP failure rate" not in render_report(_result(), run)
    assert "unexpected HTTP failure rate: 50.0% (3/6)" in format_verify(_result(unexpected_http_failure_rate=UNEXPECTED), tmp_path)
    assert not any("unexpected" in line for line in format_verify(_result(), tmp_path))


def _with_unexpected(run_dir, rate):
    result = json.loads((run_dir / "result.json").read_text())
    result["metrics"]["unexpected_http_failure_rate"] = {"samples": 10, "failed": int(rate * 10), "rate": rate}
    (run_dir / "result.json").write_text(json.dumps(result))
    return run_dir


def test_diff_reports_unexpected_failure_rate_when_either_run_defines_it(tmp_path):
    baseline = _with_unexpected(write_run(tmp_path / "baseline", "baseline"), 0.1)
    candidate = _with_unexpected(write_run(tmp_path / "candidate", "candidate"), 0.3)

    comparison = compare_runs(baseline, candidate)

    assert comparison["performance"]["unexpected_http_error_rate"] == {
        "baseline": 0.1, "candidate": 0.3, "change_percentage_points": 20.0,
    }
    assert "unexpected HTTP failure rate: 10.0% -> 30.0% (+20.0pp)" in format_diff(comparison)


def test_diff_omits_unexpected_failure_rate_when_neither_run_defines_it(tmp_path):
    comparison = compare_runs(write_run(tmp_path / "baseline", "baseline"), write_run(tmp_path / "candidate", "candidate"))

    assert "unexpected_http_error_rate" not in comparison["performance"]
    assert not any("unexpected" in line for line in format_diff(comparison))


def test_verify_records_unexpected_failure_rate_from_scenario_journeys(tmp_path, monkeypatch):
    journeys = [{"name": "purchase", "max_requests": 3, "max_writes": 1, "expected_statuses": {"create": [409]}}]
    scenario = write_bundle(tmp_path / "scenario", manifest(journeys=journeys))
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    rejected = (point("http_req_failed", 1, operation="create", status="409"),)

    result = verify("http://example.test", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events, extra_metric_lines=rejected)))

    failed, unexpected = result["metrics"]["http_req_failed_rate"], result["metrics"]["unexpected_http_failure_rate"]
    assert unexpected["samples"] == failed["samples"]
    assert unexpected["failed"] == failed["failed"] - 1  # only the declared 409 is excused
