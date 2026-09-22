from pathlib import Path

import pytest

from app.core.settings import Settings
from app.system.lab import LabSystemAdapter


def test_container_mode_uses_isolated_persistent_roots():
    settings = Settings(agent_mode="container")
    assert settings.nginx_config_root == Path("/tmp/cdn-sandbox/nginx")
    assert settings.varnish_config_root == Path("/tmp/cdn-sandbox/varnish")
    assert settings.state_root == Path("/tmp/cdn-sandbox/state")
    assert settings.log_root == Path("/tmp/cdn-sandbox/logs")


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="Invalid Agent mode"):
        Settings(agent_mode="demo")


def test_management_networks_are_normalized_and_required():
    settings = Settings(management_allowed_cidrs=["192.0.2.7/24"])
    assert settings.management_allowed_cidrs == ["192.0.2.0/24"]
    with pytest.raises(ValueError):
        Settings(management_allowed_cidrs=[])


@pytest.mark.asyncio
async def test_container_adapter_executes_real_commands():
    result = await LabSystemAdapter().run("/bin/sh", "-c", "printf edgeplane")
    assert result.code == 0
    assert result.stdout == "edgeplane"
