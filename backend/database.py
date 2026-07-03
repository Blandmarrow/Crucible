from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    """Apply per-connection SQLite pragmas on every new DBAPI connection.

    foreign_keys and synchronous default per connection and are NOT persisted in the DB
    file, so they must be set here (init_db only touches one pooled connection).
    journal_mode=WAL is persistent per-DB-file and stays in init_db.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        # WAL is a persistent per-DB-file setting; foreign_keys/synchronous are set
        # per-connection in the connect listener above.
        await conn.execute(text("PRAGMA journal_mode=WAL"))
