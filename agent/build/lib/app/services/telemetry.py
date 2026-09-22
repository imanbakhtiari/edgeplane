"""Bounded incremental traffic accounting, outside the request path.

The checkpoint is derived observed telemetry (offsets + counters), not desired or
customer business state. Offsets and totals commit atomically to prevent duplicate
accounting after an ordinary process restart. No Agent database is introduced.
"""

import json
import os
import threading
from collections import defaultdict
from uuid import uuid4, UUID

FIELDS = ("requests", "bytes_sent", "bytes_received", "cache_hits", "errors")


class TrafficCollector:
    def __init__(self, settings, deployment):
        self.s, self.deployment = settings, deployment
        self.file = settings.state_root / "traffic-checkpoint.json"
        self.guard = threading.Lock()
        self.state = {"epoch": str(uuid4()), "offsets": {}, "counters": {}, "malformed": 0}
        if self.file.exists():
            self.state = json.loads(self.file.read_text())
        self.lag_bytes = 0

    def collect(self):
        with self.guard:
            policy = self.deployment.current().get("telemetry", {})
            budget = self.s.telemetry_max_bytes
            seen = set()
            self.lag_bytes = 0
            directory = self.s.log_root / "metrics"
            files = sorted(directory.glob("*.log.1")) + sorted(directory.glob("*.log"))
            for path in files:
                vhost = path.name.split(".")[0]
                try:
                    UUID(vhost)
                except ValueError:
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                    continue
                options = policy.get(vhost, {})
                stat = path.stat()
                key = f"{stat.st_dev}:{stat.st_ino}"
                seen.add(key)
                offset = self.state["offsets"].get(key, 0)
                if offset > stat.st_size:
                    offset = 0
                if budget <= 0:
                    self.lag_bytes += max(0, stat.st_size - offset)
                    continue
                with path.open("rb") as stream:
                    stream.seek(offset)
                    chunk = stream.read(min(budget, stat.st_size - offset))
                end = chunk.rfind(b"\n") + 1
                if not end:
                    continue
                budget -= end
                self.state["offsets"][key] = offset + end
                self.lag_bytes += max(0, stat.st_size - offset - end)
                for line in chunk[:end].splitlines():
                    try:
                        row = json.loads(line)
                        country = row.get("country", "ZZ") if options.get("geography") else "ZZ"
                        if len(country) != 2 or not country.isalpha():
                            country = "ZZ"
                        counter_key = vhost + ":" + country.upper()
                        counter = self.state["counters"].setdefault(counter_key, dict.fromkeys(FIELDS, 0))
                        counter["requests"] += 1
                        counter["bytes_sent"] += max(0, int(row.get("bytes_sent", 0)))
                        counter["bytes_received"] += max(0, int(row.get("bytes_received", 0)))
                        counter["cache_hits"] += int(row.get("cache") == "HIT")
                        counter["errors"] += int(int(row.get("status", 0)) >= 500)
                    except (ValueError, TypeError, AttributeError):
                        self.state["malformed"] += 1
            self.state["offsets"] = {
                key: value for key, value in self.state["offsets"].items() if key in seen
            }
            temporary = self.file.with_suffix(".tmp")
            with temporary.open("w") as stream:
                json.dump(self.state, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, self.file)

    def snapshot(self):
        with self.guard:
            policies = self.deployment.current().get("telemetry", {})
            return {
                "epoch": self.state["epoch"],
                "lag_bytes": self.lag_bytes,
                "malformed_lines": self.state["malformed"],
                "items": [
                    {
                        "vhost_id": key.split(":")[0],
                        "country": key.split(":")[1],
                        **value,
                        "mode": policies.get(key.split(":")[0], {}).get("mode", "metrics"),
                    }
                    for key, value in self.state["counters"].items()
                ],
            }

    def metrics(self):
        snapshot = self.snapshot()
        policies = self.deployment.current().get("telemetry", {})
        def escaped(value):
            return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

        node_labels = ",".join(
            f'{key}="{escaped(value)}"'
            for key, value in {
                "agent_id": self.s.agent_id,
                "agent_name": self.s.agent_name,
                "pop_city": self.s.agent_city,
                "pop_country": self.s.agent_country,
                "provider": self.s.agent_provider,
            }.items()
        )
        rows = [
            "# TYPE cdn_vhost_requests_total counter",
            "# TYPE cdn_vhost_bytes_sent_total counter",
            f"cdn_traffic_collector_lag_bytes{{{node_labels}}} {snapshot['lag_bytes']}",
            f"cdn_traffic_collector_malformed_total{{{node_labels}}} {snapshot['malformed_lines']}",
        ]
        customers = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
        for item in snapshot["items"]:
            customer = policies.get(item["vhost_id"], {}).get("customer_id", "")
            labels = f'{node_labels},vhost_id="{item["vhost_id"]}",country="{item["country"]}",customer_id="{customer}"'
            for field in FIELDS:
                rows.append(f"cdn_vhost_{field}_total{{{labels}}} {item[field]}")
                if customer:
                    customers[customer][field] += item[field]
        for customer, totals in customers.items():
            for field, value in totals.items():
                rows.append(f'cdn_customer_{field}_total{{{node_labels},customer_id="{customer}"}} {value}')
        return "\n".join(rows) + "\n"
