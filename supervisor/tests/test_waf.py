from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.api.resources import VhostInput
from app.schemas.config import Bundle, Vhost, Origin, WAFPolicy


def test_api_requires_pop_certificate_and_typed_waf():
    data = dict(name='WAF', domains=['test.example.com'], origins=[{'host':'192.0.2.1'}],
                options={'waf': {'mode':'blocking', 'crs_version':'4.0.0'}})
    with pytest.raises(ValidationError, match='WAF_REQUIRES_POP_CERTIFICATE'):
        VhostInput(**data)
    valid = VhostInput(**data, certificate_id=uuid4())
    assert valid.options['waf']['mode'] == 'blocking'
    data['options']['waf']['raw_rules'] = 'arbitrary directives'
    with pytest.raises(ValidationError):
        VhostInput(**data, certificate_id=uuid4())


def test_disabled_waf_preserves_wire_compatibility_and_hashes():
    host = Vhost(id=uuid4(),name='test',domains=['test.example.com'],origins=[Origin(host='192.0.2.1')])
    old_wire = host.model_dump(mode='json')
    assert 'waf' not in old_wire
    assert Vhost.model_validate(old_wire).digest() == host.digest()
    assert 'waf' not in Bundle(revision=1,vhosts=[host]).model_dump()['vhosts'][0]
    host.waf = WAFPolicy(mode='detection',crs_version='4.0.0')
    assert host.model_dump()['waf']['mode'] == 'detection'
