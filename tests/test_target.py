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


def test_doctor_rejects_integer_encoded_metadata_target(tmp_path):
    def forbidden(request):
        raise AssertionError("doctor must not contact a metadata target")
    with pytest.raises(ValueError, match="link-local/metadata address not allowed"):
        run_doctor(target="http://0xA9FEA9FE/", k6_path=str(executable(tmp_path)), transport=httpx.MockTransport(forbidden))


def test_observation_origin_rejects_ec2_ipv6_metadata_in_long_form():
    with pytest.raises(ValidationError, match="link-local"):
        FinalObservation(path="/rows", assertion="rows_ok", expected={"/n": 1},
                         origin="http://[fd00:ec2:0:0:0:0:0:254]")


@pytest.mark.parametrize("target", ["http://169.254.169.254/", "http://2852039166/", "http://[fd00:ec2::254]/"])
def test_approve_rejects_metadata_targets(workspace, capsys, target):  # noqa: F811
    scenario = write_bundle(workspace / "traffic")

    assert approve(scenario, target=target) == 3
    assert "link-local/metadata address not allowed" in capsys.readouterr().out
    assert not (workspace / ".litetraffic" / "approvals.json").exists()
