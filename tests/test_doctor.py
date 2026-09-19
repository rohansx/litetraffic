import os
from pathlib import Path

import httpx
import pytest

from litetraffic.doctor import run_doctor


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "k6"
    path.write_text("#!/bin/sh\necho 'k6 v1.2.3'\n")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def test_reports_k6_version(tmp_path):
    report = run_doctor(k6_path=str(executable(tmp_path)))
    assert report.ok is True
    assert report.checks[0].detail == "k6 v1.2.3"


def test_reports_missing_k6(monkeypatch):
    monkeypatch.setenv("PATH", os.devnull)
    report = run_doctor()
    assert report.ok is False
    assert "not found" in report.checks[0].detail


def test_reports_reachable_target(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    report = run_doctor(target="http://example.test/health", k6_path=str(executable(tmp_path)), transport=transport)
    assert report.ok is True
    assert "204" in report.checks[1].detail


def test_reports_unreachable_target(tmp_path):
    def fail(request):
        raise httpx.ConnectError("refused", request=request)
    report = run_doctor(target="http://example.test/health", k6_path=str(executable(tmp_path)), transport=httpx.MockTransport(fail))
    assert report.ok is False
    assert "refused" in report.checks[1].detail


def test_rejects_non_http_target(tmp_path):
    with pytest.raises(ValueError, match="http or https"):
        run_doctor(target="file:///etc/passwd", k6_path=str(executable(tmp_path)))

