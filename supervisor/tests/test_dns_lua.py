import json
import pytest
from pydantic import ValidationError
from app.services.dns_lua import Policy, render


def test_acl_country_continent_precedence_and_fallback():
    result = render(Policy(rules=[
        {"kind": "netmask", "matches": ["192.0.2.12/24"], "address": "192.0.2.1"},
        {"kind": "country", "matches": ["ir"], "address": "185.79.98.207"},
        {"kind": "continent", "matches": ["EU"], "address": "185.79.97.93"},
    ], fallback="203.0.113.10"))
    assert "if netmask({'192.0.2.0/24'})" in result["script"]
    assert "elseif country({'IR'})" in result["script"]
    assert "elseif continent({'EU'})" in result["script"]
    assert result["script"].endswith("else return '203.0.113.10' end")
    decoder = json.JSONDecoder()
    encoded = result["content"][2:]
    parts = []
    while encoded.strip():
        value, offset = decoder.raw_decode(encoded.lstrip())
        parts.append(value)
        encoded = encoded.lstrip()[offset:]
    assert ''.join(parts) == result["script"]
    assert all(len(part.encode()) <= 255 for part in parts)


@pytest.mark.parametrize("rule", [
    {"kind": "country", "matches": ["IR'); os.execute('id')"], "address": "192.0.2.1"},
    {"kind": "continent", "matches": ["XX"], "address": "192.0.2.1"},
    {"kind": "netmask", "matches": ["invalid"], "address": "192.0.2.1"},
    {"kind": "country", "matches": ["IR"], "address": "::1"},
])
def test_reject_code_and_invalid_input(rule):
    with pytest.raises(ValidationError):
        Policy(rules=[rule], fallback="192.0.2.1")
