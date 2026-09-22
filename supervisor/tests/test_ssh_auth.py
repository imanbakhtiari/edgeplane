import pytest

from app.api.resources import CredentialInput
from app.services.provisioning import auth_mode, ssh_options, ubuntu_release


def test_password_only_never_loads_supervisor_key():
    options = ssh_options({"auth_mode": "password", "password": "example"})
    assert options == {"password": "example", "client_keys": []}


def test_private_key_mode_does_not_send_login_password(monkeypatch):
    monkeypatch.setattr(
        "app.services.provisioning.asyncssh.import_private_key", lambda value: "parsed-key"
    )
    options = ssh_options(
        {"auth_mode": "private_key", "private_key": "key-material", "password": "ignored"}
    )
    assert options == {"password": None, "client_keys": ["parsed-key"]}


def test_key_and_password_sends_both(monkeypatch):
    monkeypatch.setattr(
        "app.services.provisioning.asyncssh.import_private_key", lambda value: "parsed-key"
    )
    options = ssh_options(
        {"auth_mode": "key_and_password", "private_key": "key-material", "password": "example"}
    )
    assert options == {"password": "example", "client_keys": ["parsed-key"]}


def test_dedicated_key_used_when_no_private_key_is_pasted(monkeypatch):
    monkeypatch.setattr(
        "app.services.provisioning.asyncssh.read_private_key", lambda path: "dedicated-key"
    )
    options = ssh_options({"auth_mode": "key_and_password", "password": "example", "private_key": ""})
    assert options == {"password": "example", "client_keys": ["dedicated-key"]}


def test_legacy_credentials_choose_existing_method():
    assert auth_mode({"password": "example"}) == "password"
    assert auth_mode({"private_key": "key-material"}) == "private_key"
    assert auth_mode({"private_key": "key-material", "password": "example"}) == "key_and_password"


def test_api_infers_legacy_authentication_mode():
    assert CredentialInput(username="iman", password="example").auth_mode == "password"
    assert CredentialInput(username="iman", private_key="key-material").auth_mode == "private_key"
    assert CredentialInput(username="iman", password="example", private_key="key-material").auth_mode == "key_and_password"
    assert CredentialInput(username="iman").auth_mode == "private_key"


@pytest.mark.parametrize("mode", ["password", "key_and_password"])
def test_selected_method_requires_its_credentials(mode):
    with pytest.raises(ValueError):
        CredentialInput(username="iman", auth_mode=mode)


def test_key_mode_allows_empty_private_key():
    assert CredentialInput(username="iman", auth_mode="private_key").private_key is None


@pytest.mark.parametrize("version", ["22.04", "24.04"])
def test_supported_ubuntu_releases(version):
    assert ubuntu_release(f'ID=ubuntu\nVERSION_ID="{version}"\n') == version


def test_ubuntu_20_has_explicit_non_destructive_failure():
    with pytest.raises(ValueError, match="UBUNTU_20_HOST_BOOTSTRAP_UNAVAILABLE"):
        ubuntu_release('ID=ubuntu\nVERSION_ID="20.04"\n')
