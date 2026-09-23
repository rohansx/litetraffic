"""The dashboard's typed client (web/apps/dashboard/src/api) is written against these fixtures; the real API must serve them back."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from litetraffic import dashboard

FIXTURES = Path(__file__).parents[1] / "web/apps/dashboard/src/api/__fixtures__"
DETAILS = ("run-pass", "run-fail", "run-inconclusive", "run-activity")
MAPS = {"by_operation", "overlap"}  # keyed by operation name, not part of the schema


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _shape(value, key=""):
    """Nested key structure; lists by their first item, maps keyed by name collapsed."""
    if isinstance(value, dict):
        return "map" if key in MAPS else {k: _shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(value[0])] if value and isinstance(value[0], dict) else "list"
    return "value"


@pytest.fixture
def served(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    for name in DETAILS:
        detail = _fixture(name)
        run = root / detail["run_id"]
        run.mkdir()
        for part in ("run", "result", "observation", "fixture", "activity"):
            if detail.get(part) is not None:
                (run / f"{part}.json").write_text(json.dumps(detail[part]))
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html>")
    server = dashboard.make_server(root, 0, ui)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path) as response:
        return json.loads(response.read())


@pytest.mark.parametrize("name", DETAILS)
def test_run_detail_fixture_is_what_the_api_serves(served, name):
    fixture = _fixture(name)
    body = _get(served, f"/api/runs/{fixture['run_id']}")
    assert {k: v for k, v in body.items() if k != "artifacts"} == {k: v for k, v in fixture.items() if k != "artifacts"}
    assert _shape(body["artifacts"]) == _shape(fixture["artifacts"])


def test_list_trend_and_meta_fixtures_match_the_api(served):
    assert _shape(_get(served, "/api/runs")) == _shape(_fixture("runs"))
    scenario = _fixture("run-pass")["run"]["scenario"]
    assert _shape(_get(served, f"/api/scenarios/{scenario}/trend")) == _shape(_fixture("trend"))
    assert _shape(_get(served, "/api/meta")) == _shape(_fixture("meta"))
    assert _shape(_get(served, "/api/scenarios")) == _shape(_fixture("scenarios"))


def test_diff_fixture_matches_the_api(served):
    # These two fixture runs are from different scenarios, like diff-incomparable.json.
    baseline, candidate = _fixture("run-pass")["run_id"], _fixture("run-fail")["run_id"]
    body = _get(served, f"/api/diff?baseline={baseline}&candidate={candidate}")
    assert body["performance"].pop("unexpected_http_error_rate").keys() == body["performance"]["http_error_rate"].keys()  # optional in types.ts
    assert _shape(body) == _shape(_fixture("diff-incomparable")) == _shape(_fixture("diff"))
