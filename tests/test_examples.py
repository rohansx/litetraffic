import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ["checkout", "inventory", "reporting", "cached_search", "tenant_api"]


def _evidence_calls(script: str) -> list[str]:
    calls = []
    for match in re.finditer(r"lt\.evidence\(", script):
        depth, end = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(script[end], 0)
            end += 1
        calls.append(script[match.end() : end - 1])
    return calls


@pytest.mark.parametrize("example", EXAMPLES)
def test_example_assertions_emit_expected_and_actual(example):
    calls = _evidence_calls((ROOT / "examples" / example / "journeys.js").read_text())

    assert calls
    for call in calls:
        assert "expected:" in call and "actual:" in call, call


def test_checkout_duplicate_evidence_expects_one_effect():
    script = (ROOT / "examples" / "checkout" / "journeys.js").read_text()

    assert "expected: 1, actual: body.effects" in script


@pytest.mark.parametrize("doc", ["README.md", "docs/quickstart.md"])
def test_docs_show_the_failed_expected_actual_line(doc):
    text = (ROOT / doc).read_text()

    assert "one_effect_per_payment" in text
    assert '{"actual": 2, "detail": null, "expected": 1, "logical_key": ' in text
