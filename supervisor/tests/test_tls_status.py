import pytest
from app.services.tls_status import tls_summary


@pytest.mark.parametrize("certificate,mode,origins,expected", [
    (None, "auto", [{"scheme": "https"}], "TLS passthrough"),
    ("cert", "auto", [{"scheme": "http"}], "POP TLS termination"),
    ("cert", "terminate", [{"scheme": "https"}], "POP TLS termination"),
    (None, "terminate", [{"scheme": "https"}], "Certificate required"),
    (None, "http_only", [{"scheme": "https"}], "HTTP only"),
    (None, "auto", [{"scheme": "http"}], "HTTPS unavailable"),
    (None, "auto", [], "HTTPS unavailable"),
])
def test_tls_summary(certificate, mode, origins, expected):
    result = tls_summary(certificate, {"tls_mode": mode}, origins)
    assert result["https_mode"] == expected
    assert result["certificate_assigned"] == bool(certificate)
    assert (result["http_policy_scope"] == "Ports 80 + 443") == (expected == "POP TLS termination")
