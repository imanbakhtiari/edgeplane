# Security model and production boundary

- Argon2 password hashing; random opaque HttpOnly sessions with SameSite=Strict;
  CSRF token required on authenticated mutations; secure cookie in production.
- ADMIN/OPERATOR/VIEWER checks are enforced in API dependencies. Bootstrap accounts
  must change password before infrastructure API access.
- AES-GCM master key is environment supplied. SSH credentials, TLS private keys,
  snapshots and internal CA keys are encrypted in PostgreSQL. Redaction prevents
  secret-bearing fields and PEM material appearing in normal API/audit records.
- Agent host startup requires certificate-based TLS. The CA issues clientAuth only
  to Supervisor and serverAuth identities to Agents. Supervisor validates chain
  and management hostname. Polling also verifies the returned node UUID.
- SSH discovery fetches only a public host key. The administrator must approve the
  fingerprint from a trusted channel; provisioning uses that exact known key.
- No shell/exec endpoint, raw NGINX snippets, user-selected configuration filenames,
  public purge, or arbitrary-file log access is provided.
- Origin resolution rejects link-local/metadata, multicast and unspecified targets.
  Non-global addresses require explicit private-origin enablement. Resolved numeric
  addresses are rendered to prevent DNS rebinding between validation and use.
- Public NGINX strips all internal request/response headers with headers-more, uses
  a request header allowlist, and overwrites forwarding identity itself. Trusted
  Real-IP ranges are empty by default; blanket internet trust is rejected.

## Remaining production work

The host systemd unit currently runs the Agent as root inside filesystem and
capability restrictions. This is a **material remaining privilege boundary**:
replace it with a reviewed narrow privileged helper before multi-tenant production.
The unit uses ProtectSystem, ProtectHome, NoNewPrivileges and bounded writable
paths, but those do not make Python service compromise harmless.

Also required before a public production rollout: login throttling/account lockout,
certificate/CA renewal and revocation operations, backup/restore drills, explicit
management network policy, third-party security review, stronger origin egress
controls, resource quotas and real-host provisioning acceptance. No claim of a
full WAF, DDoS protection, or externally audited security is made.

Do not expose the sandbox HTTP Agent beyond an isolated lab network. Rootless
sandbox containers are default; the real data-plane lab is explicit and has no
host mounts. Never pass production credentials to development tests.
