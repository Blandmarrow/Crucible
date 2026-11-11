from sqlalchemy import event, text
from sqlalchemy.dialects.sqlite.base import SQLiteCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings


def _sqlite_from_hint_text(self, table, text):
    """Let `Select.with_hint(..., "sqlite")` reach the SQL.

    SQLAlchemy's SQLite dialect implements no table hints: its compiler inherits a
    `get_from_hint_text` that returns None, so a hint is dropped **without a word**
    and the statement runs under whatever index SQLite picks (checked on 2.1.6).
    SQLite's own table hints, `INDEXED BY <index>` and `NOT INDEXED`, go directly
    after the table name, which is where the compiler puts what this returns.

    Only a hint written for the `"sqlite"` dialect arrives here, and only
    `indexed_by` below writes one. `backend/tests/test_stats_plan.py` reads the
    compiled SQL and the plan, so an upgrade that changes this fails a test rather
    than quietly un-pinning a query.
    """
    return text


SQLiteCompiler.get_from_hint_text = _sqlite_from_hint_text


def indexed_by(stmt, entity, index_name: str):
    """`stmt`, reading `entity`'s table through the named index and no other.

    A pin, not a suggestion: SQLite raises ("no such index", or "no query solution"
    when the index cannot serve the statement) rather than fall back. That is the
    point: a pin that silently stopped applying would go unseen. Say at the call
    site why that index, and when the choice should be timed again.
    """
    return stmt.with_hint(entity, f"INDEXED BY {index_name}", "sqlite")

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
