"""Coraza configuration contract for an operator-provisioned, pinned installation.

Presence is not a runtime security attestation. NGINX must validate every release.
"""
import json
import re
from pathlib import Path


def installed(root=Path("/etc/cdn-agent/waf")):
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        version = manifest["crs_version"]
        if not re.fullmatch(r"4\.[0-9]+\.[0-9]+", version):
            raise ValueError("Invalid CRS version")
        crs = root / ("crs-" + version)
        if not (root / "coraza-base.conf").is_file():
            raise ValueError("Pinned Coraza request-body configuration missing")
        if not (crs / "crs-setup.conf").is_file() or not list((crs / "rules").glob("*.conf")):
            raise ValueError("CRS files missing")
        return {"configured": True, "crs_version": version,
                "engine_version": str(manifest.get("engine_version", "unknown")),
                "connector_version": str(manifest.get("connector_version", "unknown"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {"configured": False, "reason": "Pinned Coraza/CRS installation not configured"}


def rules(policy, root=Path("/etc/cdn-agent/waf")):
    if policy.mode == "off":
        return ""
    capability = installed(root)
    if not capability["configured"] or capability["crs_version"] != policy.crs_version:
        raise ValueError("WAF_CRS_VERSION_NOT_INSTALLED")
    level, threshold = {"low": (1, 10), "standard": (1, 5), "high": (2, 5),
                        "custom": (policy.paranoia_level, policy.inbound_threshold)}[policy.profile]
    crs = root / ("crs-" + policy.crs_version)
    result = [f"Include {root}/coraza-base.conf",
              "SecRuleEngine " + ("On" if policy.mode == "blocking" else "DetectionOnly"),
              "SecRequestBodyAccess On", "SecRequestBodyLimit 33554432",
              "SecRequestBodyLimitAction Reject",
              "SecResponseBodyAccess Off", "SecAuditEngine Off", "SecDebugLogLevel 0",
              'SecDefaultAction "phase:1,pass,log"', 'SecDefaultAction "phase:2,pass,log"',
              f'SecAction "id:900000,phase:1,pass,nolog,setvar:tx.blocking_paranoia_level={level}"',
              f'SecAction "id:900110,phase:1,pass,nolog,setvar:tx.inbound_anomaly_score_threshold={threshold}"',
              f"Include {crs}/crs-setup.conf", f"Include {crs}/rules/*.conf"]
    if policy.excluded_rule_ids:
        result.append("SecRuleRemoveById " + " ".join(map(str, policy.excluded_rule_ids)))
    return "\n".join(result) + "\n"
