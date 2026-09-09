from pathlib import Path
import sys
import hashlib
import json
import re
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from app.services.origins import resolve

template_root = Path(__file__).parents[2] / "templates"
if not template_root.exists():
    template_root = Path(sys.prefix) / "share/cdn-agent/templates"

env = Environment(
    loader=FileSystemLoader(template_root),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
)
env.filters["regex"] = lambda x: re.escape(x).replace("\\", "\\\\")


async def render(bundle, settings, release):
    vhosts = sorted([v for v in bundle.vhosts if v.enabled], key=lambda v: str(v.id))
    addresses = {}
    for v in vhosts:
        addresses[str(v.id)] = []
        for origin in v.origins:
            ips = await resolve(origin, settings.allow_private_origins)
            addresses[str(v.id)].append((origin, ips))
    geo_enabled = any(
        v.geo.mode != "off" or (v.analytics.mode != "off" and v.analytics.geography) for v in vhosts
    )
    if geo_enabled and not settings.maxmind_city_db.is_file():
        raise ValueError("MAXMIND_DATABASE_MISSING: install the City MMDB before enabling geography")
    cache_ids = {
        str(v.id): hashlib.sha256(json.dumps(v.cache.model_dump(), sort_keys=True).encode()).hexdigest()[:16]
        for v in vhosts
    }
    unique_caches = {cache_ids[str(v.id)]: v.cache for v in vhosts}
    context = dict(
        vhosts=vhosts,
        s=settings,
        release=str(release),
        addresses=addresses,
        geo_enabled=geo_enabled,
        cache_ids=cache_ids,
        cache_policies=sorted(unique_caches.items()),
        rates=sorted({v.rate.rate for v in vhosts if v.rate.enabled}),
    )
    files = {
        "http/00-base.conf": env.get_template("base.conf.j2").render(**context),
        "default.vcl": env.get_template("default.vcl.j2").render(**context),
    }
    for v in vhosts:
        files[f"http/{v.id}.conf"] = env.get_template("vhost.conf.j2").render(v=v, **context)
    files["candidate.conf"] = (
        f"include /etc/nginx/modules-enabled/*.conf;\npid {settings.state_root}/candidate.pid;\nerror_log stderr;\nevents {{}}\nhttp {{ include /etc/nginx/mime.types; include {release}/http/*.conf; }}\n"
    )
    return files
