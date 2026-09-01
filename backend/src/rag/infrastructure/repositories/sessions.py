"""Repository de sessões e do seu histórico (SPEC §10.3, T14/T15).

T14 cria, lê, toca e exclui sessões. T15 alimenta `session_entries` (tabela já
existente desde T03): histórico limitado (`list_entries` + `append_entry`) para
a reescrita de follow-up (AC-13). A exclusão da sessão cai em CASCADE sobre
`session_entries` (T03) — o histórico é removido com a sessão.
"""

from uuid import UUID, uuid4

from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row

from rag.domain.errors import ConflictError, NotFoundError
from rag.domain.sessions import Session, SessionEntry
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

    async def list_entries(self, session_id: UUID, *, limit: int) -> list[SessionEntry]:
        """Histórico limitado da sessão: as `limit` rodadas MAIS RECENTES, em
        ordem cronológica (AC-13; NOTES.md §4 — janela de calibração)."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, session_id, ordinal, question_original, question_anonymized, "
                "rewritten_query, answer_run_id, created_at FROM session_entries "
                "WHERE session_id = %s ORDER BY ordinal DESC LIMIT %s",
                (session_id, limit),
            )
            rows = await cur.fetchall()
        return [SessionEntry(**row) for row in reversed(rows)]

    async def append_entry(
        self,
        *,
        session_id: UUID,
        question_original: str,
        question_anonymized: str,
        rewritten_query: str | None,
        answer_run_id: UUID | None,
    ) -> SessionEntry:
        """Registra uma rodada; o ordinal é o próximo livre da sessão (AC-13).

        O MAX é lido na mesma transação; uma corrida concorrente na
        `UNIQUE (session_id, ordinal)` falha fechado (`ConflictError`)."""
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT COALESCE(MAX(ordinal) + 1, 0) AS next_ordinal "
                "FROM session_entries WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
            if row is None:  # pragma: no cover - MAX sobre filas existentes nunca é None
                raise RuntimeError("impossível calcular o próximo ordinal da sessão")
            next_ordinal = int(row["next_ordinal"])
            entry_id = uuid4()
            created_at = utcnow()
            try:
                await cur.execute(
                    "INSERT INTO session_entries (id, session_id, ordinal, question_original, "
                    "question_anonymized, rewritten_query, answer_run_id, created_at) "
                    "VALUES (%(id)s, %(session_id)s, %(ordinal)s, %(question_original)s, "
                    "%(question_anonymized)s, %(rewritten_query)s, %(answer_run_id)s, "
                    "%(created_at)s)",
                    {
                        "id": entry_id,
                        "session_id": session_id,
                        "ordinal": next_ordinal,
                        "question_original": question_original,
                        "question_anonymized": question_anonymized,
                        "rewritten_query": rewritten_query,
                        "answer_run_id": answer_run_id,
                        "created_at": created_at,
                    },
                )
            except errors.UniqueViolation as exc:
                raise ConflictError(
                    "Rodada de sessão duplicada.",
                    cause=exc,
                    context={"session_id": str(session_id)},
                ) from exc
        return SessionEntry(
            id=entry_id,
            session_id=session_id,
            ordinal=next_ordinal,
            question_original=question_original,
            question_anonymized=question_anonymized,
            rewritten_query=rewritten_query,
            answer_run_id=answer_run_id,
            created_at=created_at,
        )
