# Testing one real local agent

This installs and controls the host's real NGINX, Varnish, Prometheus, and systemd
services. Prefer an Ubuntu 22.04/24.04 VM if the workstation already serves traffic.

1. Add `supervisor/ssh_keys/edgeplane_ed25519.pub` to the target account's
   `~/.ssh/authorized_keys`, and ensure that account has root or sudo access.
2. In **Nodes → Add CDN node**, use:
   - Name: `local-edge`
   - Hostname: `host.docker.internal` when Supervisor runs in Docker
   - Management URL: `https://host.docker.internal:9443`
   - Public IPv4: a numeric LAN/public address, never `localhost`
   - SSH username and port: the target host account and `22`
3. Select the SSH authentication method that the server permits:
   **Password only**, **SSH key**, or **SSH key + SSH password** (for servers
   requiring both factors). In either key mode, leaving the private-key field
   empty uses `supervisor/ssh_keys/edgeplane_ed25519`; pasting a key overrides
   it for that node. The remote account must authorize the corresponding public
   key. Password-only login fails on a `publickey`-only server. The dedicated
   key must be readable by the Supervisor and worker containers. Configure sudo
   separately: direct root, passwordless
   sudo, or **Sudo password required** with the sudo password. An SSH login
   password is never reused silently as a sudo password.
4. Compare and approve the SSH host-key fingerprint, then provision.
5. Create a vhost, choose its cache/rate/real-IP policies, save and deploy. Watch
   the Jobs page until the node reports the current revision.

`127.0.0.1` inside the Supervisor container is the Supervisor container itself,
not the Docker host. A management URL without `https://` is not a URL, and the
Public IPv4 field only accepts a numeric IPv4 address.
