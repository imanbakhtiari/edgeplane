from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.settings import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=20, max_overflow=20)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def session():
    async with Session() as db:
        async with db.begin():
            yield db
