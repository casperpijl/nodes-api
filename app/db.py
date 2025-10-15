from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from .config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

async def get_session():
    async with SessionLocal() as session:
        yield session
