def tls_summary(certificate_id, options, origins):
    """Describe desired mode, never claim a certificate is deployed or valid."""
    mode = (options or {}).get("tls_mode", "auto")
    if mode == "http_only":
        effective = "HTTP only"
    elif certificate_id and mode != "passthrough":
        effective = "POP TLS termination"
    elif mode == "terminate":
        effective = "Certificate required"
    elif origins and all(origin.get("scheme", "http") == "https" for origin in origins):
        effective = "TLS passthrough"
    else:
        effective = "HTTPS unavailable"
    return {
        "certificate_assigned": bool(certificate_id),
        "https_mode": effective,
        "http_policy_scope": "Ports 80 + 443" if effective == "POP TLS termination" else "Port 80 only",
    }
