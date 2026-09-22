# Provisioning Ubuntu 22.04 and 24.04

1. Start Supervisor, configure public HTTPS and secrets, and change bootstrap password.
2. Prepare a dedicated Ubuntu 22.04 or 24.04 edge with a reachable management
   address. The host installer checks the OS and Python 3.10+ before changing
   packages. Ubuntu 20.04 is detected but not host-provisioned: its standard
   repositories do not contain every required GeoIP2/exporter component, so
   the job fails before installation. Use 22.04/24.04 for a full host node.
3. Open Nodes → Add CDN node. Enter name, SSH hostname, management HTTPS URL,
   public IP, location, provider and SSH credentials. SSH user must be root or have
   passwordless sudo or the separately configured sudo password.
4. Compare the discovered SHA-256 host fingerprint with the trusted server console.
5. Approve the host identity and provision. View Jobs for durable step events.
6. The installer validates existing NGINX, installs distro packages, checks disk,
   creates directories/backups, installs the uploaded `agent/` source in a Python
   virtual environment, installs per-node mTLS identity and optional MaxMind DB,
   configures Varnish, Prometheus, NGINX/node/Varnish exporters and optional BIRD, inserts
   only the controlled NGINX include, validates NGINX, then starts services.
7. The worker verifies Agent schema support, sends the newest full desired bundle,
   and compares applied revision and SHA-256 before marking synchronization successful.
8. Create a vhost, wait for all targets, point customer test DNS, and curl the public edge.

Normal changes use the Agent API. Reprovision/upgrade is another SSH bootstrap job:
it verifies the approved fingerprint, checks OS/Python/resources, uploads the
current `agent/` package and identities, runs the installer, then contacts the
management API to validate and apply the latest full desired bundle (all active
vhosts and their policies). The worker checks the returned revision and hash
before marking the node ready. Retry creates a durable job for failed targets;
the installer reuses existing directories and package-manager state. Reprovision
restarts managed services and can briefly interrupt traffic; use a maintenance
window for customer POPs.

The remote node uses host-installed NGINX, Varnish, Prometheus and the Python
Agent as a systemd service. Docker Compose runs Supervisor, worker, PostgreSQL
and the Supervisor-side Prometheus only; it does not install the remote Agent
in a Docker container.

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
