# Supervisor SSH identity

`edgeplane_ed25519` is the dedicated private SSH key used only for provisioning
Edgeplane nodes. Its public half is `edgeplane_ed25519.pub`.

Install the public key in the target account's `~/.ssh/authorized_keys`. The
private key is ignored by Git and mounted read-only into Supervisor containers.
Protect it as a deployment secret in production and rotate it independently of
the Agent mTLS identities.

The Supervisor and worker containers run as UID 10001. A host key with mode
`0600` owned by another UID cannot be read through the bind mount. On Linux,
grant only that UID read access with `setfacl -m u:10001:r
supervisor/ssh_keys/edgeplane_ed25519` from the repository root, or provision
the secret owned by UID 10001 with mode `0600`. If ACLs are unavailable, use a
dedicated host group, set the key to mode `0640`, and add that group's numeric
GID to both containers through `SUPERVISOR_SSH_KEY_GID` in `supervisor/.env`.
For the local checkout, the key is group-owned by GID 1000 and Compose defaults
to that GID. Verify the owner/group with `stat -c '%a %u %g'` before deploying
elsewhere. Do not use mode `0644`.
