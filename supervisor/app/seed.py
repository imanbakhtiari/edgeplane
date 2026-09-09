import asyncio
from sqlalchemy import select
from app.db.session import Session
from app.models import entities as m
from app.core.settings import settings
from app.core.security import passwords
from app.schemas import config as c
from app.services.config import revision, configuration_lock
from app.services.pki import authority


async def seed():
    async with Session.begin() as db:
        await configuration_lock(db)
        if not await db.scalar(select(m.User).limit(1)):
            db.add(
                m.User(
                    username=settings.admin_username,
                    password_hash=passwords.hash(settings.admin_password),
                    role="ADMIN",
                )
            )
        for model, schema in [
            (m.RateLimitPolicy, c.RatePolicy),
            (m.RealIPPolicy, c.RealIPPolicy),
            (m.HeaderPolicy, c.HeaderPolicy),
        ]:
            if not await db.scalar(select(model).limit(1)):
                db.add(model(name="Global default", config=schema().model_dump(), is_default=True))
        presets = {
            "Generic Website": {},
            "Static Assets": {"static_ttl": 86400},
            "WordPress": {"bypass_paths": ["/wp-admin", "/wp-login.php"]},
            "WooCommerce": {
                "bypass_paths": ["/cart", "/checkout", "/my-account", "/wp-admin"],
                "bypass_cookies": ["woocommerce", "wp_woocommerce_session"],
            },
            "API No Cache": {"enabled": False},
            "Aggressive Static CDN": {"static_ttl": 604800, "max_ttl": 604800},
        }
        for name, config in presets.items():
            if not await db.scalar(select(m.CachePolicy).where(m.CachePolicy.name == name)):
                db.add(
                    m.CachePolicy(
                        name=name,
                        config=c.CachePolicy(**config).model_dump(),
                        is_default=name == "Generic Website",
                    )
                )
        await authority(db)
        from app.services.config import latest
        from app.core.security import decrypt

        current = await latest(db)
        if not current:
            await revision(db, deploy=False)
        elif c.Bundle.model_validate_json(decrypt(current.encrypted_bundle)).digest() != current.config_hash:
            await revision(db)


if __name__ == "__main__":
    asyncio.run(seed())
