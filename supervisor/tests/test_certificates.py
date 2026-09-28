import pytest
from pydantic import ValidationError
from app.api.resources import CertificateInput
from app.services.acme_http_hook import public_url


def test_manual_certificate_requires_pem_pair():
    with pytest.raises(ValidationError):
        CertificateInput(name="manual", source="manual")


def test_certbot_certificate_requires_domains_and_email():
    with pytest.raises(ValidationError):
        CertificateInput(name="managed", source="certbot", domains=[])
    value = CertificateInput(
        name="managed", source="certbot", domains=["cdn.example.com"], email="ops@example.com"
    )
    assert value.auto_renew is True
    assert value.renew_before_days == 30
    assert value.challenge == "dns-01"


def test_certbot_http_challenge_is_supported():
    value = CertificateInput(
        name="managed", source="certbot", challenge="http-01",
        domains=["cdn.example.com"], email="ops@example.com",
    )
    assert value.challenge == "http-01"


def test_http_challenge_urls_support_ipv4_and_ipv6():
    assert public_url("192.0.2.10", "abc") == "http://192.0.2.10/.well-known/acme-challenge/abc"
    assert public_url("2001:db8::10", "abc") == "http://[2001:db8::10]/.well-known/acme-challenge/abc"


def test_wildcard_certificate_requires_dns_challenge():
    with pytest.raises(ValidationError, match="Wildcard certificates require DNS-01"):
        CertificateInput(
            name="wildcard", source="certbot", challenge="http-01",
            domains=["*.example.com"], email="ops@example.com",
        )
