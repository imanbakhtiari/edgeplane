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


def vhost_filename(vhost):
    """Return an operator-friendly, collision-safe managed NGINX filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", vhost.name.lower()).strip("-")[:48] or "vhost"
    return f"{slug}--{vhost.id}.conf"


async def render(bundle, settings, release):
    vhosts = sorted([v for v in bundle.vhosts if v.enabled], key=lambda v: str(v.id))
    for v in vhosts:
        if v.tls_mode == "terminate" and not v.tls:
            raise ValueError("POP_TLS_CERTIFICATE_REQUIRED: TLS termination requires a certificate and key")
    terminated = [v for v in vhosts if v.tls and v.tls_mode in {"auto", "terminate"}]
    passthrough = [v for v in vhosts if not v.tls and v.tls_mode in {"auto", "passthrough"}
                   and all(o.scheme == "https" for o in v.origins)]
    addresses = {}
    for v in vhosts:
        addresses[str(v.id)] = []
        for origin in v.origins:
            ips = await resolve(origin, settings.allow_private_origins)
            addresses[str(v.id)].append((origin, ips))
    geo_enabled = any(
        v.geo.mode != "off" or (v.analytics.mode != "off" and v.analytics.geography) for v in vhosts
    )
    if geo_enabled and not settings.maxmind_country_db.is_file():
        raise ValueError("MAXMIND_DATABASE_MISSING: install the Country MMDB before enabling geography")
    if any(v.geo.city_ids for v in vhosts) and not settings.maxmind_city_db.is_file():
        raise ValueError("MAXMIND_CITY_DATABASE_MISSING: city rules require the City MMDB")
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
        terminated=terminated,
        passthrough=passthrough,
    )
    files = {
        "http/00-base.conf": env.get_template("base.conf.j2").render(**context),
        "default.vcl": env.get_template("default.vcl.j2").render(**context),
        "stream/00-tls.conf": env.get_template("stream.conf.j2").render(**context),
    }
    for v in vhosts:
        files[f"http/{vhost_filename(v)}"] = env.get_template("vhost.conf.j2").render(v=v, **context)
    candidate_temp = settings.state_root / "nginx-candidate"
    files["candidate.conf"] = (
        f"include /etc/nginx/modules-enabled/*.conf;\n"
        f"pid {settings.state_root}/candidate.pid;\n"
        "error_log stderr;\n"
        "events {}\n"
        f"stream {{ include {release}/stream/*.conf; }}\n"
        "http {\n"
        "  include /etc/nginx/mime.types;\n"
        f"  client_body_temp_path {candidate_temp}/body;\n"
        f"  proxy_temp_path {candidate_temp}/proxy;\n"
        f"  fastcgi_temp_path {candidate_temp}/fastcgi;\n"
        f"  uwsgi_temp_path {candidate_temp}/uwsgi;\n"
        f"  scgi_temp_path {candidate_temp}/scgi;\n"
        f"  include {release}/http/*.conf;\n"
        "}\n"
    )
    return files
