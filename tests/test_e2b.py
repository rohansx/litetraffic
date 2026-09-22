import pytest

from litetraffic.e2b import resolve_target


def test_resolves_an_existing_e2b_sandbox_port():
    assert resolve_target(None, "sandbox-abc-123", 8767) == "https://8767-sandbox-abc-123.e2b.app"


@pytest.mark.parametrize(
    ("target", "sandbox_id", "port", "message"),
    [
        (None, None, None, "target or E2B"),
        ("http://example.test", "sandbox-1", 8000, "exactly one"),
        (None, "sandbox-1", None, "requires both"),
        (None, None, 8000, "requires both"),
        (None, "../../host", 8000, "invalid E2B sandbox ID"),
        (None, "sandbox-1", 70000, "invalid E2B port"),
    ],
)
def test_rejects_ambiguous_or_invalid_e2b_targets(target, sandbox_id, port, message):
    with pytest.raises(ValueError, match=message):
        resolve_target(target, sandbox_id, port)
