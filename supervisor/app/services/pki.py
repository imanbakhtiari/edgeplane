from datetime import datetime, timedelta, timezone
import ipaddress
import json
import ssl
import tempfile
from contextlib import contextmanager
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from sqlalchemy import select, text
from app.models.entities import SystemSetting
from app.core.security import encrypt, decrypt


def key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM).decode()


async def authority(db):
    row = await db.scalar(select(SystemSetting).where(SystemSetting.key == "internal-pki"))
    if row:
        return json.loads(decrypt(row.value["encrypted"]))
    await db.execute(text("SELECT pg_advisory_xact_lock(7391043)"))
    row = await db.scalar(select(SystemSetting).where(SystemSetting.key == "internal-pki"))
    if row:
        return json.loads(decrypt(row.value["encrypted"]))
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CDN management CA")])
    now = datetime.now(timezone.utc)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    data = {"ca": cert_pem(ca), "ca_key": key_pem(key)}
    data.update(issue(data, "supervisor", client=True))
    db.add(SystemSetting(key="internal-pki", value={"encrypted": encrypt(json.dumps(data))}))
    return data


def issue(pki, identity, hostname=None, client=False):
    key = ec.generate_private_key(ec.SECP256R1())
    ca = x509.load_pem_x509_certificate(pki["ca"].encode())
    ca_key = serialization.load_pem_private_key(pki["ca_key"].encode(), None)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=True,
        )
    )
    if hostname:
        try:
            san = x509.IPAddress(ipaddress.ip_address(hostname))
        except ValueError:
            san = x509.DNSName(hostname)
        builder = builder.add_extension(x509.SubjectAlternativeName([san]), critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    return {
        "client_cert" if client else "server_cert": cert_pem(cert),
        "client_key" if client else "server_key": key_pem(key),
    }


@contextmanager
def tls_context(pki):
    # Transient SSL library inputs only; persistent PKI lives encrypted in PostgreSQL.
    with tempfile.TemporaryDirectory() as directory:
        from pathlib import Path

        cert = Path(directory) / "client.pem"
        key = Path(directory) / "client.key"
        cert.write_text(pki["client_cert"])
        key.touch(mode=0o600)
        key.write_text(pki["client_key"])
        ctx = ssl.create_default_context(cadata=pki["ca"])
        ctx.load_cert_chain(cert, key)
        yield ctx
