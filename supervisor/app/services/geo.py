from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from app.core.settings import settings


@lru_cache(maxsize=1)
def reader():
    import maxminddb

    if not Path(settings.maxmind_country_db).is_file():
        raise HTTPException(503, "MAXMIND_DATABASE_MISSING")
    return maxminddb.open_database(settings.maxmind_country_db)


def lookup(ip):
    data = reader().get(ip) or {}
    return {
        "ip": ip,
        "country": data.get("country", {}).get("iso_code", "ZZ"),
        "country_name": data.get("country", {}).get("names", {}).get("en", "Unknown"),
        "city_id": data.get("city", {}).get("geoname_id"),
        "city": data.get("city", {}).get("names", {}).get("en", "Unknown"),
        "location": data.get("location", {}),
    }
