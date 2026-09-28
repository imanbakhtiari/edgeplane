from sqlalchemy import select

from app.core.settings import settings
from app.models import entities as m


DEFAULTS = {
    "geoip": {"country_enabled": True, "city_enabled": True},
    "monitoring": {"mode": settings.analytics_mode},
    "analytics": {"mode": settings.analytics_mode},
}


async def get_group(db, key: str) -> dict:
    row = await db.scalar(select(m.SystemSetting).where(m.SystemSetting.key == key))
    return {**DEFAULTS[key], **(row.value if row else {})}


async def put_group(db, key: str, value: dict) -> dict:
    row = await db.scalar(select(m.SystemSetting).where(m.SystemSetting.key == key))
    if not row:
        row = m.SystemSetting(key=key, value={})
        db.add(row)
    row.value = {**DEFAULTS[key], **value}
    return row.value
