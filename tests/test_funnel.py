import json

import pytest
from pydantic import ValidationError

from litetraffic import explain
from litetraffic.compare import compare_runs
from litetraffic.funnel import journey_funnels
from litetraffic.human import format_diff, format_inspect
from litetraffic.models import Journey
from litetraffic.runner import _read_events, verify
from test_compare import write_run
from test_runner import assertion, fake_k6
from litetraffic.scenario import load_scenario
from test_scenario import manifest, write_bundle

STAGES = ["created_record", "read_back"]


def journey(name="purchase", stages=STAGES):
    return Journey(name=name, max_requests=3, max_writes=1, stages=stages)


def stage(name, key):
    return {"schema_version": 1, "type": "stage", "stage": name, "logical_key": key}


@pytest.mark.parametrize(
    "stages",
    [[], ["a"] * 2, ["Upper"], ["1st"], ["a" * 41], [f"s{i}" for i in range(13)], ["with-dash"]],
)
def test_journey_rejects_bad_stages(stages):
    with pytest.raises(ValidationError):
        journey(stages=stages)


def test_journey_stages_are_optional_and_ordered():
    assert journey(stages=None).stages is None
    assert journey(stages=["b", "a" * 40]).stages == ["b", "a" * 40]


def test_read_events_keeps_stage_events_in_order_and_rejects_bad_ones(tmp_path):
    records = [
        stage("created_record", "k1"),
        assertion("a"),
        stage("read_back", "k1"),
        stage("Bad Name", "k1"),
        stage("read_back", ""),
        {k: v for k, v in stage("read_back", "k1").items() if k != "logical_key"},
    ]
    path = tmp_path / "console.log"
    path.write_text("".join(f"LT_EVENT {json.dumps(record | {'run_id': 'r'})}\n" for record in records))

    events, malformed = _read_events(path, "r")

    assert malformed == 3
    assert [(e["sequence"], e["type"]) for e in events] == [(1, "stage"), (2, "assertion"), (3, "stage")]


def test_funnel_counts_distinct_journeys_stalls_and_undeclared_names():
    events = [stage("created_record", f"k{i}") for i in range(4)]
    events += [stage("created_record", "k0"), stage("read_back", "k0"), stage("read_back", "k1"), stage("retry", "k2")]
    events.append(stage("read_back", "k9"))  # a later stage counts even without the earlier one

    funnels, limitations = journey_funnels([journey()], events, 5.0)

    assert funnels == {
        "purchase": {
            "started": 5,
            "stages": [{"name": "created_record", "reached": 4}, {"name": "read_back", "reached": 3}],
            "stalled": [{"after": "start", "count": 1}, {"after": "created_record", "count": 2}],
            "undeclared": ["retry"],
        }
    }
    assert limitations == ["journey purchase reported undeclared stage(s): retry"]


def test_funnel_is_empty_without_declared_stages_and_attributes_by_name():
    assert journey_funnels([journey(stages=None)], [stage("x", "k")], 1) == ({}, [])
    journeys = [journey("a", ["one"]), journey("b", ["two"]), journey("c", None)]
    funnels, limitations = journey_funnels(journeys, [stage("two", "k1"), stage("zzz", "k2")], 10)
    # k6 iterations are run-wide, so with several journeys only stage emitters count as started.
    assert funnels["a"]["started"] == 0 and funnels["b"]["stages"] == [{"name": "two", "reached": 1}]
    assert limitations == ["stage events match no single journey: zzz"]


def _staged_run(tmp_path, monkeypatch, events):
    data = manifest()
    data["journeys"][0]["stages"] = STAGES
    scenario = write_bundle(tmp_path / "scenario", data)
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    return verify("http://127.0.0.1:8000", scenario, tmp_path / "runs", str(fake_k6(tmp_path, events)))


def test_green_run_where_no_journey_finished_is_inconclusive(tmp_path, monkeypatch):
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    events += [stage("created_record", f"k{i}") for i in range(20)]

    result = _staged_run(tmp_path, monkeypatch, events)

    assert result["verdict"] == "inconclusive"
    assert result["limitations"] == [
        "No journey reached its final stage 'read_back': the checks passed on journeys that never finished."
    ]
    assert result["journeys"]["purchase"]["stalled"][1] == {"after": "created_record", "count": 20}
    lines = (tmp_path / "runs" / result["run_id"] / "events" / "000001.jsonl").read_text().splitlines()
    assert sum(json.loads(line)["type"] == "stage" for line in lines) == 20


def test_run_where_journeys_finish_still_passes(tmp_path, monkeypatch):
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    events += [stage(name, f"k{i}") for i in range(20) for name in STAGES]

    result = _staged_run(tmp_path, monkeypatch, events)

    assert result["verdict"] == "pass"
    assert result["journeys"]["purchase"]["stages"][-1] == {"name": "read_back", "reached": 20}


def test_run_without_stages_has_no_journeys_block(tmp_path, monkeypatch):
    events = [assertion("accepted_orders_persist") for _ in range(20)]
    monkeypatch.setenv("FAKE_K6_EVENTS", json.dumps(events))
    result = verify("http://127.0.0.1:8000", write_bundle(tmp_path / "s"), tmp_path / "runs", str(fake_k6(tmp_path, events)))
    assert "journeys" not in result


FUNNEL = {
    "started": 10,
    "stages": [{"name": "created_record", "reached": 10}, {"name": "read_back", "reached": 7}],
    "stalled": [{"after": "start", "count": 0}, {"after": "created_record", "count": 3}],
    "undeclared": [],
}


def test_explain_says_where_journeys_stopped():
    detail = {"run": {}, "result": {"verdict": "fail", "assertions": [], "journeys": {"tenant-a": FUNNEL}}}
    sections = {s["title"]: s["items"] for s in explain.summarize(detail)["sections"]}
    assert sections["Where journeys stopped"] == ["tenant-a: 10 started, 10 created_record, 7 read_back; 3 stalled after created_record"]
    assert "journey funnel" in explain.build_prompt(detail)


def test_compare_reports_reached_deltas_without_changing_the_verdict(tmp_path):
    baseline, candidate = write_run(tmp_path / "a", "a"), write_run(tmp_path / "b", "b")
    for path, reached in ((baseline, 7), (candidate, 4)):
        result = json.loads((path / "result.json").read_text())
        funnel = json.loads(json.dumps(FUNNEL))
        funnel["stages"][1]["reached"] = reached
        result["journeys"] = {"tenant-a": funnel}
        (path / "result.json").write_text(json.dumps(result))

    comparison = compare_runs(baseline, candidate)

    assert comparison["verdict"] == "pass"
    assert comparison["journeys"] == {
        "tenant-a": {
            "created_record": {"baseline": 10, "candidate": 10, "change": 0},
            "read_back": {"baseline": 7, "candidate": 4, "change": -3},
        }
    }
    assert "journey tenant-a read_back: 7 -> 4 reached (-3)" in format_diff(comparison)
    assert "journeys" not in compare_runs(write_run(tmp_path / "c", "c"), write_run(tmp_path / "d", "d"))


def test_inspect_lists_stages():
    lines = format_inspect(
        {"name": "n", "script": "s", "scenario_sha256": "x", "planned_journeys": 1, "maximum_journey_requests": 1,
         "maximum_observation_requests": 0, "maximum_journey_writes": 0, "resolved_schedule": [], "assertions": [],
         "stages": {"purchase": STAGES}}
    )
    assert "stages purchase: created_record -> read_back" in lines


def test_manifest_rejects_a_stage_name_shared_by_two_journeys(tmp_path):
    # Stage events carry no journey name, so a shared stage would leave every such event unattributed.
    data = manifest()
    data["journeys"] = [{**data["journeys"][0], "stages": ["login", "done"]}, {"name": "refund", "max_requests": 3, "max_writes": 1, "stages": ["login"]}]
    with pytest.raises(ValidationError, match="unique across journeys"):
        load_scenario(write_bundle(tmp_path, data))
