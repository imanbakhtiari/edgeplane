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


def test_write_and_copy_skip_unchanged_files(tmp_path):
    target = tmp_path / "config"
    assert bootstrap.write(target, "same\n")
    first_mtime = target.stat().st_mtime_ns
    assert not bootstrap.write(target, "same\n")
    assert target.stat().st_mtime_ns == first_mtime
    source = tmp_path / "source"
    source.write_text("mmdb contents")
    destination = tmp_path / "database"
    assert bootstrap.copy_if_changed(source, destination)
    copied_mtime = destination.stat().st_mtime_ns
    assert not bootstrap.copy_if_changed(source, destination)
    assert destination.stat().st_mtime_ns == copied_mtime


def test_source_digest_changes_only_for_source_contents(tmp_path):
    (tmp_path / "app").mkdir()
    code = tmp_path / "app" / "main.py"
    code.write_text("version = 1\n")
    first = bootstrap.source_digest(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.pyc").write_bytes(b"cache")
    assert bootstrap.source_digest(tmp_path) == first
    code.write_text("version = 2\n")
    assert bootstrap.source_digest(tmp_path) != first


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


def test_hostname_preserves_other_addresses_and_is_idempotent():
    original = '127.0.0.1 localhost\n127.0.1.1 old alias\n::1 localhost\n192.0.2.1 origin\n'
    result = bootstrap.hostname_hosts(original, 'cdn-shiraz')
    assert '127.0.1.1\tcdn-shiraz alias' in result
    assert '192.0.2.1 origin' in result
    assert '127.0.0.1 localhost' in result
    assert bootstrap.hostname_hosts(result, 'cdn-shiraz') == result


def test_hostname_preserves_a_file_without_self_mapping():
    result = bootstrap.hostname_hosts('127.0.0.1 localhost\n', 'cdn-tabriz.example')
    assert '127.0.0.1 localhost' in result
    assert '127.0.1.1\tcdn-tabriz.example cdn-tabriz' in result


@pytest.mark.parametrize('name', ['bad name', '-bad', 'bad;touch', 'bad\nname', 'a' * 64])
def test_hostname_rejects_invalid_names(name):
    with pytest.raises(ValueError):
        bootstrap.hostname_hosts('', name)


@pytest.fixture
def agent_releases(tmp_path, monkeypatch):
    state_path = tmp_path / 'agent-releases.json'
    override = tmp_path / 'release.conf'
    old = {'version': 'old-release', 'source': '/old/source', 'python': '/old/venv/bin/python'}
    new = {'version': 'new-release', 'source': '/new/source', 'python': '/new/venv/bin/python'}
    state_path.write_text(bootstrap.json.dumps({'current': old, 'previous': None}))
    monkeypatch.setattr(bootstrap, 'AGENT_RELEASE_STATE', state_path)
    monkeypatch.setattr(bootstrap, 'AGENT_RELEASE_OVERRIDE', override)
    monkeypatch.setattr(bootstrap.time, 'sleep', lambda _: None)
    commands = []
    monkeypatch.setattr(bootstrap, 'run', lambda *args: commands.append(args))
    monkeypatch.setattr(bootstrap, 'require_active_service', lambda _: None)
    return state_path, override, old, new, commands


def test_agent_release_retains_previous_and_only_restarts_agent(agent_releases):
    state_path, override, old, new, commands = agent_releases
    bootstrap.activate_agent_release(new)
    assert bootstrap.release_state() == {'current': new, 'previous': old}
    assert 'ExecStart=/new/venv/bin/python -m app.main' in override.read_text()
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert commands == [('systemctl', 'daemon-reload'), ('systemctl', 'restart', 'cdn-agent')]
    bootstrap.activate_agent_release(new)
    assert bootstrap.release_state()['previous'] == old


@pytest.mark.parametrize('existing_override', [None, '[Service]\nExecStart=/old/python\n'])
def test_failed_agent_activation_restores_previous_override(agent_releases, monkeypatch, existing_override):
    state_path, override, old, new, commands = agent_releases
    if existing_override:
        override.write_text(existing_override)
    def fail(_):
        raise RuntimeError('not active')
    monkeypatch.setattr(bootstrap, 'require_active_service', fail)
    with pytest.raises(RuntimeError, match='previous release restored'):
        bootstrap.activate_agent_release(new)
    assert bootstrap.release_state() == {'current': old, 'previous': None}
    assert (override.read_text() if override.exists() else None) == existing_override
    assert all(command[-1] == 'cdn-agent' for command in commands if 'restart' in command)


def test_rollback_requires_retained_executable(agent_releases):
    with pytest.raises(RuntimeError, match='No previous Agent release'):
        bootstrap.rollback_agent_release()


def test_rollback_switches_to_retained_environment(agent_releases, tmp_path):
    state_path, override, old, new, commands = agent_releases
    executable = tmp_path / 'previous-python'
    executable.touch()
    old['python'] = str(executable)
    state_path.write_text(bootstrap.json.dumps({'current': new, 'previous': old}))
    bootstrap.rollback_agent_release()
    assert bootstrap.release_state() == {'current': old, 'previous': new}
    assert commands == [('systemctl', 'daemon-reload'), ('systemctl', 'restart', 'cdn-agent')]
