from types import SimpleNamespace

from app.api.auth import effective_sections


def user(role, sections=None):
    return SimpleNamespace(role=role, section_permissions=sections or [])


def test_role_defaults_are_least_privilege():
    assert "users" in effective_sections(user("ADMIN"))
    assert "users" not in effective_sections(user("OPERATOR"))
    assert "dns" not in effective_sections(user("OPERATOR"))
    assert "vhosts" in effective_sections(user("VIEWER"))
    assert "settings" not in effective_sections(user("VIEWER"))


def test_custom_sections_narrow_role_access():
    assert effective_sections(user("ADMIN", ["traffic", "not-a-section"])) == ["traffic"]
