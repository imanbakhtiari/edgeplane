from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy import select
from app.models import entities as m


class VarnishSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage: Literal["file", "malloc"] = "file"
    size_mb: int = Field(default=1024, ge=256, le=1048576, strict=True)
    log_retention_days: int = Field(default=7, ge=1, le=3650, strict=True)


async def desired(db, node_id):
    row = await db.scalar(select(m.SystemSetting).where(m.SystemSetting.key == f"varnish:{node_id}"))
    return VarnishSettings.model_validate(row.value).model_dump() if row else None
