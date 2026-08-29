"""Repository de obras e contribuidores."""

from uuid import UUID

from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row

from rag.domain.errors import ConflictError
from rag.domain.library import Contributor, Work


class WorksRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, work: Work) -> Work:
        try:
            async with self._conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO works (id, canonical_title, original_title, language,
                                       created_at, updated_at)
                    VALUES (%(id)s, %(canonical_title)s, %(original_title)s, %(language)s,
                            %(created_at)s, %(updated_at)s)
                    """,
                    {
                        "id": work.id,
                        "canonical_title": work.canonical_title,
                        "original_title": work.original_title,
                        "language": work.language,
                        "created_at": work.created_at,
                        "updated_at": work.updated_at,
                    },
                )
                for author in work.authors:
                    await cur.execute(
                        "INSERT INTO contributors (work_id, ordinal, name, role) "
                        "VALUES (%s, %s, %s, %s)",
                        (work.id, author.ordinal, author.name, author.role.value),
                    )
        except errors.UniqueViolation as exc:
            raise ConflictError(
                "Obra duplicada.", cause=exc, context={"work_id": str(work.id)}
            ) from exc
        return work

    async def get(self, work_id: UUID) -> Work | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, canonical_title, original_title, language, created_at, updated_at "
                "FROM works WHERE id = %s",
                (work_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            await cur.execute(
                "SELECT name, role, ordinal FROM contributors WHERE work_id = %s ORDER BY ordinal",
                (work_id,),
            )
            authors = [Contributor(**r) for r in await cur.fetchall()]
        return Work(**(row | {"authors": authors}))

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> list[Work]:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id FROM works ORDER BY canonical_title LIMIT %s OFFSET %s",
                (limit, offset),
            )
            ids = [row["id"] for row in await cur.fetchall()]
        works: list[Work] = []
        for work_id in ids:
            work = await self.get(work_id)
            if work is not None:
                works.append(work)
        return works
