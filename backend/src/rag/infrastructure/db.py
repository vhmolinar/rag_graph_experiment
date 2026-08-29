"""Pool de conexões psycopg3 com suporte a pgvector."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from rag.infrastructure.config import DatabaseSettings


async def _configure_connection(conn: AsyncConnection) -> None:
    await register_vector_async(conn)


class Database:
    """Pool assíncrono. Uso: `async with db.connection() as conn: ...`"""

    def __init__(self, settings: DatabaseSettings, *, max_size: int = 10) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=settings.dsn,
            configure=_configure_connection,
            max_size=max_size,
            open=False,
            kwargs={"autocommit": False},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Cede conexão com transação; commit no sucesso, rollback na exceção."""
        async with self._pool.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
