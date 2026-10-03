import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from app.core.settings import Settings
from app.schemas.config import Bundle, Vhost, Origin, TLS, WAFPolicy
from app.services.waf import installed, rules
from app.renderers.config import render, vhost_filename


def installation(root):
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps({'crs_version': '4.0.0'}))
    (root / 'coraza-base.conf').write_text('# test fixture, not a production ruleset\n')
    crs = root / 'crs-4.0.0'
    (crs / 'rules').mkdir(parents=True)
    (crs / 'crs-setup.conf').write_text('# fixture\n')
    (crs / 'rules' / 'test.conf').write_text('# fixture\n')


def test_off_never_requires_installation(tmp_path):
    assert rules(WAFPolicy(), tmp_path) == ''
    assert not installed(tmp_path)['configured']


def test_enabled_requires_exact_pinned_installation(tmp_path):
    policy = WAFPolicy(mode='blocking', crs_version='4.0.0')
    with pytest.raises(ValueError, match='WAF_CRS_VERSION_NOT_INSTALLED'):
        rules(policy, tmp_path)
    root = tmp_path / 'waf'
    installation(root)
    assert installed(root)['configured']
    with pytest.raises(ValueError, match='WAF_CRS_VERSION_NOT_INSTALLED'):
        rules(policy.model_copy(update={'crs_version': '4.1.0'}), root)
    (root / 'coraza-base.conf').unlink()
    assert not installed(root)['configured']


@pytest.mark.parametrize('mode,engine', [('blocking', 'On'), ('detection', 'DetectionOnly')])
@pytest.mark.parametrize('profile,level,threshold', [('low', 1, 10), ('standard', 1, 5), ('high', 2, 5), ('custom', 3, 8)])
def test_modes_profiles_exclusions(tmp_path, mode, engine, profile, level, threshold):
    root = tmp_path / 'waf'
    installation(root)
    policy = WAFPolicy(mode=mode, crs_version='4.0.0', profile=profile,
                       paranoia_level=3, inbound_threshold=8, excluded_rule_ids=[942100,942100])
    result = rules(policy, root)
    assert f'SecRuleEngine {engine}\n' in result
    assert f'tx.blocking_paranoia_level={level}' in result
    assert f'tx.inbound_anomaly_score_threshold={threshold}' in result
    assert result.endswith('SecRuleRemoveById 942100\n')
    assert result.index('coraza-base.conf') < result.index('/rules/*.conf')
    assert 'SecRequestBodyNoFilesLimit' not in result  # not supported by Coraza


@pytest.mark.parametrize('options', [{'mode':'blocking'}, {'crs_version':'../../escape'}, {'excluded_rule_ids':[1]}])
def test_invalid_policy(options):
    with pytest.raises(ValidationError):
        WAFPolicy(**options)


@pytest.mark.parametrize('path', ['/x" deny', '/x\nSecRuleEngine Off', '/x%{TX.a}', '/x\\y', 'admin'])
def test_path_blocks_reject_rule_injection(path):
    with pytest.raises(ValidationError):
        WAFPolicy(path_blocks=[{'path':path}])


def test_body_controls_and_virtual_patch_rules(tmp_path):
    root = tmp_path / 'waf'
    installation(root)
    policy = WAFPolicy(mode='blocking',crs_version='4.0.0',request_body=False,
                       request_body_limit_mb=8,response_body=True,response_body_limit_kb=128,
                       response_mime_types=['application/json'],
                       path_blocks=[{'path':'/admin/','match':'prefix','status':403},
                                    {'path':'/legacy','match':'exact','status':406}])
    result = rules(policy, root)
    assert 'SecRequestBodyAccess Off\n' in result
    assert 'SecRequestBodyLimit 8388608\n' in result
    assert 'SecResponseBodyAccess On\n' in result
    assert 'SecResponseBodyLimit 131072\n' in result
    assert 'SecResponseBodyMimeTypesClear\nSecResponseBodyMimeType application/json\n' in result
    assert 'REQUEST_FILENAME "@beginsWith /admin/" "id:1100000,phase:1,t:none,deny,status:403' in result
    assert 'REQUEST_FILENAME "@streq /legacy" "id:1100001,phase:1,t:none,deny,status:406' in result
    assert result.index('id:1100001') < result.index('/rules/*.conf')


@pytest.mark.parametrize('options', [{'request_body_limit_mb':0}, {'response_body_limit_kb':4097},
                                    {'response_mime_types':['application/json\nSecRuleEngine Off']},
                                    {'path_blocks':[{'path':'/admin','status':200}]}])
def test_body_and_action_bounds(options):
    with pytest.raises(ValidationError):
        WAFPolicy(**options)


async def test_waf_both_listeners_and_vhost_isolation(monkeypatch, tmp_path):
    from app.services import waf
    monkeypatch.setattr(waf, 'rules', lambda policy: 'SecRuleEngine On\n')
    protected = Vhost(id=uuid4(), name='protected', domains=['protected.example.com'],
                      origins=[Origin(host='127.0.0.1')], tls=TLS(certificate='cert', private_key='key'),
                      waf=WAFPolicy(mode='blocking', crs_version='4.0.0'))
    ordinary = Vhost(id=uuid4(), name='ordinary', domains=['ordinary.example.com'], origins=[Origin(host='127.0.0.1')])
    settings = Settings(allow_private_origins=True)
    files = await render(Bundle(revision=1,vhosts=[protected,ordinary]), settings, tmp_path)
    config = files['http/' + vhost_filename(protected)]
    assert config.count('coraza on;') == 2
    assert config.count(f'coraza_rules_file {tmp_path}/waf/{protected.id}.conf;') == 2
    assert 'coraza' not in files['http/' + vhost_filename(ordinary)]
    assert f'waf/{ordinary.id}.conf' not in files
    protected.tls = None
    with pytest.raises(ValueError, match='WAF_REQUIRES_POP_TLS'):
        await render(Bundle(revision=1,vhosts=[protected]), settings, Path('/release'))
