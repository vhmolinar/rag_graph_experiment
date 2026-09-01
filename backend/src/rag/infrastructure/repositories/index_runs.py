"""Repository de execuções de indexação (`IndexRun`; T06, correção T6-01).

Nunca desativa e ativa fora de uma transação já protegida por
`pg_advisory_xact_lock` (ver `IndexingService`) — evitar duas execuções
ativas simultâneas para a mesma edição depende dessa serialização, não
apenas do índice único parcial `index_runs_one_active_per_edition` (que é a
rede de segurança final, não o mecanismo primário).
"""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.indexing import IndexRun


class IndexRunsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def get_active(self, edition_id: UUID) -> IndexRun | None:
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, edition_id, extraction_version_id, chunking_version_id, "
                "embedding_version_id, model_endpoint_version_id, is_active, created_at "
                "FROM index_runs WHERE edition_id = %s AND is_active",
                (edition_id,),
            )
            row = await cur.fetchone()
        return IndexRun(**row) if row else None

    async def create(self, run: IndexRun) -> IndexRun:
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO index_runs (id, edition_id, extraction_version_id,
                                        chunking_version_id, embedding_version_id,
                                        model_endpoint_version_id, is_active, created_at)
                VALUES (%(id)s, %(edition_id)s, %(extraction_version_id)s,
                        %(chunking_version_id)s, %(embedding_version_id)s,
                        %(model_endpoint_version_id)s, %(is_active)s, %(created_at)s)
                """,
                {
                    "id": run.id,
                    "edition_id": run.edition_id,
                    "extraction_version_id": run.extraction_version_id,
                    "chunking_version_id": run.chunking_version_id,
                    "embedding_version_id": run.embedding_version_id,
                    "model_endpoint_version_id": run.model_endpoint_version_id,
                    "is_active": run.is_active,
                    "created_at": run.created_at,
                },
            )
        return run

    async def deactivate(self, run_id: UUID) -> None:
        """Não é mutação de uma tabela de versão (SPEC §6): `index_runs`
        registra qual conjunto de passagens está ativo, não o conteúdo de uma
        versão imutável — por isso não carrega o trigger de imutabilidade."""
        async with self._conn.cursor() as cur:
            await cur.execute("UPDATE index_runs SET is_active = false WHERE id = %s", (run_id,))
