# Provisioning Ubuntu 24.04

1. Start Supervisor, configure public HTTPS and secrets, and change bootstrap password.
2. Prepare a dedicated Ubuntu 24.04 edge with a reachable management address.
3. Open Nodes → Add CDN node. Enter name, SSH hostname, management HTTPS URL,
   public IP, location, provider and SSH credentials. SSH user must be root or have
   noninteractive sudo for the fixed installer.
4. Compare the discovered SHA-256 host fingerprint with the trusted server console.
5. Approve the host identity and provision. View Jobs for durable step events.
6. The installer validates existing NGINX, installs missing packages, checks disk,
   creates directories/backups, installs the Agent and mTLS identity, configures
   Varnish/exporters, validates the controlled NGINX include, then starts services.
7. The worker verifies Agent schema support, sends the newest full desired bundle,
   and compares applied revision and SHA-256 before marking synchronization successful.
8. Create a vhost, wait for all targets, point customer test DNS, and curl the public edge.

Normal changes use the Agent API. Reprovision/upgrade is another SSH bootstrap job,
with a new identity and the same safety checks. Retry creates a durable job for
failed targets; the installer uses existing directories and package manager state.

Existing `nginx.conf` is backed up before controlled insertion into the HTTP context.
If `nginx -t` fails, the original file is restored and no reload is issued. Backup
retention is five runs. Existing customer vhost files are not deleted. Conflicting
listeners/includes fail validation rather than silently overwriting traffic rules.

Firewall provisioning is optional via the credentials API (`firewall=true` and
explicit `management_cidrs`). It first preserves SSH access, allows 80/443, then
restricts 9443. Exporters are loopback-only; use an explicitly reviewed scrape proxy
or tunnel for external Prometheus. The GUI wizard currently leaves firewall off.

The host flow has not been accepted against an external SSH server in this workspace.
Do a staging VM rehearsal before customer use; consult the security boundary notes.
