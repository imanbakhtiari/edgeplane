from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from app.services.dns import reconcile, record_name, record_values
from app.core.settings import settings


class Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


async def test_dns_only_eligible_nodes_and_replace_not_duplicate():
    nodes = [SimpleNamespace(public_ipv4="192.0.2.10", public_ipv6=None)]
    hosts = [SimpleNamespace(cdn_hostname="a.edge.example.net", enabled=True, deleted_at=None)]
    db = AsyncMock()
    db.scalars.side_effect = [Rows(nodes), Rows(hosts)]
    db.scalar.return_value = None
    added = []
    db.add = lambda row: added.append(row)
    response = SimpleNamespace(raise_for_status=lambda: None)
    client = AsyncMock()
    client.patch.return_value = response
    with (
        patch.object(settings, "powerdns_api_url", "https://dns.example.net"),
        patch("app.services.dns.httpx.AsyncClient") as factory,
    ):
        factory.return_value.__aenter__.return_value = client
        result = await reconcile(db)
    assert result["changed"] == 2
    rrsets = client.patch.call_args.kwargs["json"]["rrsets"]
    assert rrsets[0]["changetype"] == "REPLACE"
    assert rrsets[0]["records"] == [{"content": "192.0.2.10", "disabled": False}]
    assert rrsets[1]["changetype"] == "DELETE"
    assert all(row.status == "SUCCESS" for row in added)


def test_operator_records_are_bounded_to_managed_zone():
    config = {"zone": "edge.example.net"}
    assert record_name(config, "status") == "status.edge.example.net."
    assert record_name(config, "@") == "edge.example.net."
    try:
        record_name(config, "outside.example.org")
        assert False, "out-of-zone record accepted"
    except ValueError:
        pass
    assert record_values("A", ["192.0.2.1"]) == ("A", ["192.0.2.1"])
