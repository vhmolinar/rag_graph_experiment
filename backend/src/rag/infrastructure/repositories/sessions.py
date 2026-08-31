"""Repository mínimo de sessões (SPEC §10.3, T14).

Histórico de contexto e reescrita de follow-up são T15 (`session_entries`);
esta tarefa só cria, lê, toca e exclui sessões para os endpoints de API.
"""

from uuid import UUID

from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row

from rag.domain.errors import ConflictError, NotFoundError
from rag.domain.sessions import Session
from rag.domain.versions import utcnow


class SessionsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, session: Session) -> Session:
        try:
            async with self._conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO sessions (id, created_at, last_activity_at) "
                    "VALUES (%(id)s, %(created_at)s, %(last_activity_at)s)",
                    {
                        "id": session.id,
                        "created_at": session.created_at,
                        "last_activity_at": session.last_activity_at,
                    },
                )
        except errors.UniqueViolation as exc:
            raise ConflictError(
                "Sessão duplicada.", cause=exc, context={"session_id": str(session.id)}
            ) from exc
        return session

    async def get(self, session_id: UUID) -> Session | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, created_at, last_activity_at FROM sessions WHERE id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Session(**row)

    async def touch(self, session_id: UUID) -> Session:
        """Atualiza `last_activity_at`; 404 se a sessão não existir."""
        async with self._conn.cursor() as cur:
            await cur.execute(
                "UPDATE sessions SET last_activity_at = %(now)s WHERE id = %(id)s RETURNING id",
                {"now": utcnow(), "id": session_id},
            )
            if await cur.fetchone() is None:
                raise NotFoundError(
                    "Sessão não encontrada.", context={"session_id": str(session_id)}
                )
        updated = await self.get(session_id)
        if updated is None:  # pragma: no cover - o UPDATE acabou de gravar
            raise RuntimeError("sessão não encontrada após atualização")
        return updated

    async def delete(self, session_id: UUID) -> None:
        """Exclui a sessão; os `session_entries` caem por CASCADE (T03)."""
        async with self._conn.cursor() as cur:
            await cur.execute("DELETE FROM sessions WHERE id = %s RETURNING id", (session_id,))
            if await cur.fetchone() is None:
                raise NotFoundError(
                    "Sessão não encontrada.", context={"session_id": str(session_id)}
                )
