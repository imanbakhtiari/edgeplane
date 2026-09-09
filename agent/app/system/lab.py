"""Real data plane restricted to an explicit lab container's own filesystem."""

from app.system.adapter import HostSystemAdapter, Result


class LabSystemAdapter(HostSystemAdapter):
    async def run(self, *args):
        if args[0] == "/usr/bin/systemctl":
            # Containers do not have systemd. Check actual process names instead.
            import psutil

            service = args[-1]
            names = {
                "varnish": "varnishd",
                "nginx": "nginx",
                "prometheus-node-exporter": "prometheus-node-exporter",
                "prometheus-nginx-exporter": "prometheus-nginx-exporter",
            }
            running = any(
                names.get(service)
                in {p.info["name"], __import__("os").path.basename((p.info["cmdline"] or [""])[0])}
                for p in psutil.process_iter(["name", "cmdline"])
            )
            return Result(0 if running else 1, stdout="active" if running else "inactive")
        return await super().run(*args)
