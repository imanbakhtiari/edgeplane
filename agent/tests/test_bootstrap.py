"""First-boot NGINX metrics must be available before the exporter starts."""

import importlib.util
from pathlib import Path

import pytest


BOOTSTRAP = Path(__file__).resolve().parents[1] / "install" / "bootstrap.py"
spec = importlib.util.spec_from_file_location("cdn_bootstrap", BOOTSTRAP)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_first_boot_status_release_is_loopback_only(tmp_path):
    assert bootstrap.ensure_nginx_status_configuration(tmp_path)
    current = tmp_path / "current"
    status = (current / "http" / "00-status.conf").read_text()
    assert "listen 127.0.0.1:8081" in status
    assert "stub_status" in status
    assert "deny all" in status
    assert not bootstrap.ensure_nginx_status_configuration(tmp_path)


def test_existing_release_is_not_replaced(tmp_path):
    release = tmp_path / "releases" / "active"
    release.mkdir(parents=True)
    (tmp_path / "current").symlink_to(release)
    assert not bootstrap.ensure_nginx_status_configuration(tmp_path)
    assert (tmp_path / "current").resolve() == release


def test_broken_release_is_not_silently_replaced(tmp_path):
    (tmp_path / "current").symlink_to(tmp_path / "missing")
    with pytest.raises(RuntimeError, match="broken"):
        bootstrap.ensure_nginx_status_configuration(tmp_path)


def test_nginx_exporter_must_report_up(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"nginx_up 1\n"

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    bootstrap.wait_for_nginx_exporter(attempts=1)


def test_nginx_exporter_rejects_down_state(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"nginx_up 0\n"

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="NGINX_EXPORTER_UNHEALTHY"):
        bootstrap.wait_for_nginx_exporter(attempts=1)
