"""The bundled runtime's schedule must make real k6 admit exactly the planned journeys."""

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "src" / "litetraffic" / "k6" / "runtime.js"

SCHEDULES = [
    [{"seconds": 1, "rate": 1}],
    [{"seconds": 1, "rate": 7}],
    [{"seconds": 1, "rate": 100}],
    [{"seconds": 1, "rate": 2000}],
    [{"seconds": 2, "rate": 7}, {"seconds": 1, "rate": 2000}, {"seconds": 1, "rate": 100}, {"seconds": 1, "rate": 1}],
    [{"seconds": 1, "rate": 2000}, {"seconds": 1, "rate": 0}, {"seconds": 1, "rate": 7}],
    [{"seconds": 1, "rate": 100}, {"seconds": 1, "rate": 0}],
]


@pytest.mark.real_k6
@pytest.mark.skipif(shutil.which("k6") is None, reason="real k6 not installed")
@pytest.mark.parametrize("phases", SCHEDULES, ids=lambda phases: "+".join(f"{p['rate']}x{p['seconds']}s" for p in phases))
def test_real_k6_admits_exactly_planned_journeys(tmp_path, phases):
    (tmp_path / "litetraffic").mkdir()
    shutil.copy(RUNTIME, tmp_path / "litetraffic" / "runtime.js")
    (tmp_path / "script.js").write_text(
        'import * as lt from "./litetraffic/runtime.js";\nexport const options = lt.options();\nexport default function () {}\n'
    )
    schedule = [{"name": f"p{index}", **phase} for index, phase in enumerate(phases)]
    env = os.environ | {"LT_SCHEDULE_JSON": json.dumps(schedule), "LT_MAX_IN_FLIGHT": "50", "K6_NO_USAGE_REPORT": "true"}
    summary = tmp_path / "summary.json"
    subprocess.run(
        ["k6", "run", "--quiet", "--no-color", f"--summary-export={summary}", "script.js"],
        cwd=tmp_path, env=env, check=True, capture_output=True, timeout=60,
    )
    metrics = json.loads(summary.read_text())["metrics"]
    planned = sum(math.ceil(phase["seconds"] * phase["rate"]) for phase in phases)
    assert metrics["iterations"]["count"] == planned
    assert metrics.get("dropped_iterations", {}).get("count", 0) == 0
