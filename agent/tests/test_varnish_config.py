import importlib.util
import json
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location("varnish_config", Path(__file__).resolve().parents[1] / "install/varnish_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


@pytest.mark.parametrize("value", [{"storage": "persistent"}, {"size_mb": 1}, {"size_mb": True},
                                  {"log_retention_days": 0}, {"path": "/"}])
def test_reject_unsafe_settings(value):
    with pytest.raises(ValueError):
        config.validate(value)


def prepare(tmp_path):
    vcl = tmp_path / "etc/varnish/cdn-managed/current/default.vcl"
    vcl.parent.mkdir(parents=True)
    vcl.write_text("vcl 4.1;")
    (tmp_path / "var/lib/cdn-agent").mkdir(parents=True)


def test_idempotence_and_log_retention_do_not_restart_cache(tmp_path):
    prepare(tmp_path)
    calls = []
    run = lambda args, **kwargs: calls.append(args)
    value = {"storage": "file", "size_mb": 256, "log_retention_days": 14}
    assert config.apply(value, tmp_path, run)["restarted"]
    calls.clear()
    assert not config.apply(value, tmp_path, run)["restarted"]
    value["log_retention_days"] = 3
    assert not config.apply(value, tmp_path, run)["restarted"]
    assert not any("restart" in call for call in calls)
    rotation = (tmp_path / "etc/logrotate.d/cdn-agent").read_text()
    assert "maxage 3" in rotation and "/var/log/nginx/cdn/*/*.log" in rotation
    assert "/var/lib/" not in rotation


def test_failed_restart_restores_previous_unit_and_does_not_acknowledge(tmp_path):
    prepare(tmp_path)
    value = {"storage": "file", "size_mb": 256, "log_retention_days": 7}
    config.apply(value, tmp_path, lambda *a, **kw: None)
    path = tmp_path / "etc/systemd/system/varnish.service.d/cdn.conf"
    old = path.read_text()
    calls = []

    def fail_once(args, **kwargs):
        calls.append(args)
        if args == ["systemctl", "restart", "varnish"] and calls.count(args) == 1:
            raise subprocess.CalledProcessError(1, args)

    with pytest.raises(subprocess.CalledProcessError):
        config.apply({**value, "storage": "malloc"}, tmp_path, fail_once)
    assert path.read_text() == old
    assert json.loads((tmp_path / "var/lib/cdn-agent/varnish-settings.json").read_text()) == value


def test_active_cache_symlink_rejected(tmp_path):
    prepare(tmp_path)
    (tmp_path / "var/lib/cdn-agent/cache").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        config.apply({}, tmp_path, lambda *a, **kw: None)
