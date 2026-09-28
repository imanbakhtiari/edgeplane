from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.resources import VhostInput
from app.schemas.config import RedirectRule, OriginRoute, Vhost, Origin


def test_redirect_can_be_saved_as_typed_vhost_option():
    rule = {"path": "/old", "match": "exact", "target": "https://example.net/new", "status": 303}
    body = VhostInput(
        name="Example", domains=["www.example.com"], origins=[Origin(host="origin.example.com")],
        options={"redirect_rules": [rule]},
    )
    vhost = Vhost(id=uuid4(), name=body.name, domains=body.domains, origins=body.origins, **body.options)
    assert vhost.redirect_rules == [RedirectRule.model_validate(rule)]


@pytest.mark.parametrize("target", ["javascript:alert(1)", "https://example.net/$host", "https://example.net/;return 200"])
def test_redirect_rejects_nonliteral_or_unsafe_target(target):
    with pytest.raises(ValidationError):
        RedirectRule(path="/old", target=target)


def test_origin_route_is_accepted_through_api_options():
    rule = {"path": "/api/", "match": "prefix", "origin_index": 1}
    origins = [Origin(host="a.example.com"), Origin(host="b.example.com")]
    body = VhostInput(name="Routes", domains=["www.example.com"], origins=origins,
                      options={"origin_routes": [rule]})
    vhost = Vhost(id=uuid4(), name=body.name, domains=body.domains, origins=body.origins, **body.options)
    assert vhost.origin_routes == [OriginRoute.model_validate(rule)]


def test_https_redirect_requires_edge_certificate():
    with pytest.raises(ValidationError, match="HTTP_TO_HTTPS_REDIRECT_REQUIRES_EDGE_CERTIFICATE"):
        VhostInput(
            name="No edge cert", domains=["www.example.com"],
            origins=[Origin(host="origin.example.com")], options={"redirect_https": True},
        )


@pytest.mark.parametrize("mode,scheme,cert,valid", [
    ("auto", "http", None, True),
    ("auto", "https", None, True),
    ("passthrough", "https", None, True),
    ("passthrough", "http", None, False),
    ("terminate", "http", None, False),
    ("terminate", "http", uuid4(), True),
    ("terminate", "https", uuid4(), True),
    ("http_only", "https", uuid4(), False),
    ("passthrough", "https", uuid4(), False),
])
def test_tls_mode_api_validation(mode, scheme, cert, valid):
    def create():
        return VhostInput(name="TLS", domains=["www.example.com"],
                          origins=[Origin(host="origin.example.com", scheme=scheme)],
                          certificate_id=cert, options={"tls_mode": mode})
    if valid:
        assert create().options["tls_mode"] == mode
    else:
        with pytest.raises(ValidationError):
            create()
