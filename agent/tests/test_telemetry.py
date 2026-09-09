import json
from uuid import uuid4
from app.core.settings import Settings
from app.services.deploy import Deployment
from app.system.adapter import SandboxSystemAdapter
from app.services.telemetry import TrafficCollector


def test_telemetry_incremental_restart_rotation_and_metrics(tmp_path):
    settings = Settings(
        agent_mode="host",
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
        nginx_config_root=tmp_path / "nginx",
        varnish_config_root=tmp_path / "varnish",
    )
    deployment = Deployment(settings, SandboxSystemAdapter())
    vhost = str(uuid4())
    customer = str(uuid4())
    deployment.save(
        {
            "revision": 1,
            "hash": "x",
            "vhosts": [vhost],
            "telemetry": {vhost: {"customer_id": customer, "mode": "full", "geography": True}},
        }
    )
    path = settings.log_root / "metrics" / f"{vhost}.log"
    row = (
        json.dumps({"country": "DE", "bytes_sent": 100, "bytes_received": 20, "status": 200, "cache": "HIT"})
        + "\n"
    )
    path.write_text(row * 3)
    collector = TrafficCollector(settings, deployment)
    collector.collect()
    assert collector.snapshot()["items"][0]["requests"] == 3
    collector.collect()
    assert collector.snapshot()["items"][0]["bytes_sent"] == 300
    # Persisted observed offsets and counters restart together without replaying logs.
    restarted = TrafficCollector(settings, deployment)
    restarted.collect()
    assert restarted.snapshot()["items"][0]["requests"] == 3
    path.rename(path.with_suffix(".log.1"))
    path.write_text(row)
    restarted.collect()
    assert restarted.snapshot()["items"][0]["requests"] == 4
    assert 'cdn_customer_bytes_sent_total{customer_id="' + customer + '"} 400' in restarted.metrics()
    assert 'country="DE"' in restarted.metrics()
    assert "client_ip" not in restarted.metrics()
