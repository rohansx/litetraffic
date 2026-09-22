import os
import types
from pathlib import Path

import httpx
import pytest

from litetraffic import doctor
from litetraffic.doctor import run_doctor


def executable(tmp_path: Path, version: str = "v2.2.0") -> Path:
    path = tmp_path / "k6"
    path.write_text(f"#!/bin/sh\necho 'k6 {version}'\n")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def check(report, name):
    return next(item for item in report.checks if item.name == name)


def test_reports_pinned_k6_version(tmp_path):
    report = run_doctor(k6_path=str(executable(tmp_path)), output_dir=tmp_path / "runs")
    assert report.ok is True
    assert check(report, "k6").detail == "k6 v2.2.0"


def test_rejects_unpinned_k6_version(tmp_path):
    report = run_doctor(k6_path=str(executable(tmp_path, "v1.2.3")), output_dir=tmp_path / "runs")
    assert report.ok is False
    assert check(report, "k6").ok is False
    assert "v2.2.0" in check(report, "k6").detail
    assert "v1.2.3" in check(report, "k6").detail


def test_python_check_fails_below_3_11(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "sys", types.SimpleNamespace(version_info=(3, 10, 14)))
    report = run_doctor(k6_path=str(executable(tmp_path)), output_dir=tmp_path / "runs")
    assert check(report, "python").ok is False
    assert "3.10.14" in check(report, "python").detail


def test_python_check_passes_on_current_interpreter(tmp_path):
    assert check(run_doctor(k6_path=str(executable(tmp_path)), output_dir=tmp_path), "python").ok is True


def test_disk_check_reports_free_bytes_and_fails_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: types.SimpleNamespace(total=10, used=9, free=1024))
    report = run_doctor(k6_path=str(executable(tmp_path)), output_dir=tmp_path / "runs")
    assert check(report, "disk").ok is False
    assert "1024 bytes free" in check(report, "disk").detail


def test_disk_check_passes_above_threshold(tmp_path, monkeypatch):
    free = doctor.MIN_FREE_BYTES
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: types.SimpleNamespace(total=free, used=0, free=free))
    report = run_doctor(k6_path=str(executable(tmp_path)), output_dir=tmp_path / "runs")
    assert check(report, "disk").ok is True
    assert f"{free} bytes free" in check(report, "disk").detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_output_dir_check_fails_when_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        report = run_doctor(k6_path=str(executable(tmp_path)), output_dir=locked / "runs")
    finally:
        locked.chmod(0o700)
    assert report.ok is False
    assert check(report, "output_dir").ok is False
    assert "not writable" in check(report, "output_dir").detail
    assert not (locked / "runs").exists()


def test_output_dir_check_passes_for_missing_dir_under_writable_parent(tmp_path):
    report = run_doctor(k6_path=str(executable(tmp_path)), output_dir=tmp_path / "a" / "runs")
    assert check(report, "output_dir").ok is True
    assert not (tmp_path / "a").exists()


def test_reports_missing_k6(monkeypatch):
    monkeypatch.setenv("PATH", os.devnull)
    report = run_doctor()
    assert report.ok is False
    assert "not found" in report.checks[0].detail


def test_reports_reachable_target(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    report = run_doctor(target="http://example.test/health", k6_path=str(executable(tmp_path)), transport=transport, output_dir=tmp_path)
    assert report.ok is True
    assert "204" in check(report, "target").detail


@pytest.mark.parametrize("status", [503, 404, 500])
def test_non_ready_status_fails_target_check(tmp_path, status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    report = run_doctor(target="http://example.test/health", k6_path=str(executable(tmp_path)), transport=transport, output_dir=tmp_path)
    assert report.ok is False
    assert check(report, "target").detail == f"reachable but not ready (HTTP {status})"


def test_redirect_status_passes_target_check(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "/x"}))
    report = run_doctor(target="http://example.test/health", k6_path=str(executable(tmp_path)), transport=transport, output_dir=tmp_path)
    assert check(report, "target").ok is True


def test_reports_unreachable_target(tmp_path):
    def fail(request):
        raise httpx.ConnectError("refused", request=request)
    report = run_doctor(target="http://example.test/health", k6_path=str(executable(tmp_path)), transport=httpx.MockTransport(fail))
    assert report.ok is False
    assert "refused" in check(report, "target").detail


def test_rejects_non_http_target(tmp_path):
    with pytest.raises(ValueError, match="http or https"):
        run_doctor(target="file:///etc/passwd", k6_path=str(executable(tmp_path)))



@pytest.mark.parametrize("target", ["http://169.254.169.254/", "http://[fe80::1]/"])
def test_rejects_link_local_and_metadata_targets_without_sending(tmp_path, target):
    def forbidden(request):
        raise AssertionError("doctor must not contact a metadata target")
    with pytest.raises(ValueError, match="link-local/metadata address not allowed"):
        run_doctor(target=target, k6_path=str(executable(tmp_path)), transport=httpx.MockTransport(forbidden))


@pytest.mark.parametrize("target", ["http://localhost:8000/", "http://127.0.0.1:8000/"])
def test_accepts_loopback_targets(tmp_path, target):
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    assert run_doctor(target=target, k6_path=str(executable(tmp_path)), transport=transport, output_dir=tmp_path).ok is True
