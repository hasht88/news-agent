import os
import re
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv
load_dotenv(".env.local")
Base = declarative_base()

def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    if url.strip():
        # Handle Render, Neon, Supabase, Heroku postgres prefixes
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+psycopg://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        # Ensure query params compatible with asyncpg (remove sslmode if present or adapt)
        # asyncpg prefers ?ssl=require over ?sslmode=require
        # url = re.sub(r"[?&]sslmode=require", "?ssl=require", url)
        return url
    
    # Fallback to local SQLite using aiosqlite
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "app.db"
    return f"sqlite+aiosqlite:///{db_file}"

DATABASE_URL = get_database_url()

# Configure engine kwargs
engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def get_db():
    """FastAPI dependency for obtaining an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    """Create all database tables if they do not exist."""
    # Import db_models so Base.metadata is fully populated
    from app import db_models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
