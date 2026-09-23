import httpx
import pytest
from pydantic import ValidationError

from litetraffic.doctor import run_doctor
from litetraffic.models import FinalObservation
from litetraffic.target import validate_target

from test_approval import approve, workspace  # noqa: F401 (fixture)
from test_doctor import executable
from test_scenario import write_bundle

BLOCKED = [
    "http://169.254.169.254/",
    "http://169.254.169.254./",
    "http://[fe80::1]/",
    "http://[fe80::1%25eth0]/",
    "http://[::ffff:169.254.169.254]/",
    "http://[::ffff:a9fe:a9fe]/",
    "http://[fd00:ec2::254]/",
    "http://[fd00:0ec2:0000:0000:0000:0000:0000:0254]/",
    "http://[FD00:EC2:0::254]/",
    "http://metadata.google.internal/",
    "http://metadata.google.internal./",
    "http://2852039166/",  # decimal 169.254.169.254
    "http://0xA9FEA9FE/",  # hex
    "http://0251.0376.0251.0376/",  # octal
    "http://0xa9.0xfe.0xa9.0xfe/",
    "http://169.254.43518/",  # a.b.c short form
    "http://169.16689662/",  # a.b short form
    "http://2852039166./",
    "http://169\u3002254\u3002169\u3002254/",  # ideographic full stop
    "http://169\uff0e254\uff0e169\uff0e254/",  # fullwidth full stop
    "http://169\uff61254\uff61169\uff61254/",  # halfwidth ideographic full stop
    "http://169\u3002254\uff0e169\uff61254./",  # mixed
    "http://metadata\u3002google\u3002internal/",
    "http://METADATA.GOOGLE.INTERNAL\u3002/",
]

# Forms httpx refuses to parse at all; they must be rejected, never passed through.
UNPARSEABLE = [
    "http://\uff11\uff16\uff19.\uff12\uff15\uff14.\uff11\uff16\uff19.\uff12\uff15\uff14/",  # fullwidth digits
    "http://\uff11\uff16\uff19\u3002254\u3002169\u3002254/",  # fullwidth digits + ideographic stops
    "http://[fd00\uff1aec2::254]/",  # fullwidth colon
    "http://[fd00:ec2::\uff12\uff15\uff14]/",  # fullwidth digits
    "http://[\uff46d00:ec2::254]/",  # fullwidth letter
]


@pytest.mark.parametrize("target", BLOCKED)
def test_validate_target_rejects_metadata_and_link_local_in_any_form(target):
    with pytest.raises(ValueError, match="link-local/metadata address not allowed"):
        validate_target(target)


@pytest.mark.parametrize(
    "target",
    ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000", "http://10.0.0.5", "http://2130706433",
     "http://app.example.test", "http://0x7f000001", "http://1234.example.test", "http://999999999999"],
)
def test_validate_target_allows_ordinary_hosts(target):
    assert validate_target(target) == target


@pytest.mark.parametrize("target", UNPARSEABLE)
def test_validate_target_rejects_unparseable_unicode_hosts(target):
    with pytest.raises(ValueError):
        validate_target(target)


@pytest.mark.parametrize(
    ("target", "canonical"),
    [("http://App.Example.TEST/p/", "http://app.example.test/p"),
     ("http://app\u3002example\uff0etest:8000", "http://app.example.test:8000"),
     ("http://\u00c4\u00d6.example", "http://xn--4ca0b.example")],
)
def test_validate_target_returns_canonical_url(target, canonical):
    assert validate_target(target) == canonical


def test_doctor_probes_the_canonical_url(tmp_path):
    seen = []
    run_doctor(target="http://app\u3002example\u3002test/", k6_path=str(executable(tmp_path)),
               transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200)))
    assert seen == ["http://app.example.test"]


def test_doctor_rejects_integer_encoded_metadata_target(tmp_path):
    def forbidden(request):
        raise AssertionError("doctor must not contact a metadata target")
    with pytest.raises(ValueError, match="link-local/metadata address not allowed"):
        run_doctor(target="http://0xA9FEA9FE/", k6_path=str(executable(tmp_path)), transport=httpx.MockTransport(forbidden))


def test_observation_origin_rejects_ec2_ipv6_metadata_in_long_form():
    with pytest.raises(ValidationError, match="link-local"):
        FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1},
                         origin="http://[fd00:ec2:0:0:0:0:0:254]")


@pytest.mark.parametrize("target", ["http://169%2e254%2e169%2e254", "http://metadata.google.internal%2e"])
def test_percent_encoded_host_passes_only_because_httpx_keeps_it_encoded(target):
    # If httpx ever decodes %-escapes in hosts, validate_target must decode before checking.
    assert validate_target(target) == target
    assert "%2e" in httpx.URL(target).host


@pytest.mark.parametrize("target", ["http://169.254.169.254/", "http://2852039166/", "http://[fd00:ec2::254]/"])
def test_approve_rejects_metadata_targets(workspace, capsys, target):  # noqa: F811
    scenario = write_bundle(workspace / "traffic")

    assert approve(scenario, target=target) == 3
    assert "link-local/metadata address not allowed" in capsys.readouterr().out
    assert not (workspace / ".litetraffic" / "approvals.json").exists()
