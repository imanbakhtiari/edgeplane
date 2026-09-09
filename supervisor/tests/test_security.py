import pytest
from app.core.security import encrypt, decrypt, redact, passwords
from app.schemas.config import Bundle


def test_authenticated_encryption():
    value = encrypt("private SSH credential")
    assert "private" not in value
    assert decrypt(value) == "private SSH credential"
    assert encrypt("private SSH credential") != value
    with pytest.raises(Exception):
        decrypt(value[:20] + "AAAA" + value[24:])


def test_redaction():
    data = redact(
        {"password": "secret", "nested": {"private_key": "key", "name": "safe"}, "encrypted": "cipher"}
    )
    assert data["password"] == "[REDACTED]"
    assert data["nested"] == {"private_key": "[REDACTED]", "name": "safe"}


def test_password_hash():
    hashed = passwords.hash("test-password")
    assert hashed != "test-password"
    assert passwords.verify(hashed, "test-password")


def test_hash_excludes_revision():
    assert Bundle(revision=1).digest() == Bundle(revision=100).digest()


def test_pki_purpose_separation():
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from app.services.pki import issue, key_pem, cert_pem

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test CA")])
    now = datetime.now(timezone.utc)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    pki = {"ca": cert_pem(ca), "ca_key": key_pem(key)}
    server = issue(pki, "node-1", "127.0.0.1")
    client = issue(pki, "supervisor", client=True)
    cert = x509.load_pem_x509_certificate(server["server_cert"].encode())
    assert (
        ExtendedKeyUsageOID.SERVER_AUTH
        in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert (
        ExtendedKeyUsageOID.CLIENT_AUTH
        not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert "client_key" in client
