"""Busca vetorial por cosseno (T09; SPEC §8.5, AC-05, AC-06, AC-07).

Usa a coluna `passages.embedding` (vector(1024)) e o índice HNSW
`passages_embedding_hnsw` (`vector_cosine_ops`) criados na migration 0001
(T03). Distância padrão: cosseno (SPEC §8.5); o score exposto é
`1 - distance` (similaridade de cosseno).

Filtros por obra/edição são aplicados no SQL ANTES da seleção (AC-07 — uma
obra excluída não chega nem à fusão nem ao reranker) e passagens-pai
(`embedding_version_id IS NULL`) nunca são candidatas (NOTES.md §10.6 item 2,
anunciado para T08/T09). A dimensão do vetor de consulta é conferida contra a
capacidade do schema antes de consultar (`EmbeddingDimensionError` tipado,
falha fechada — nunca um `DataError` cru de psycopg).

Nada do texto do usuário é interpolado: o vetor de consulta e os IDs de
filtros são sempre parâmetros ligados.
"""

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.enums import RankingStage
from rag.domain.errors import EmbeddingDimensionError
from rag.domain.query import EditionFilter
from rag.domain.runs import RankedCandidate
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS


class VectorSearchRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def search(
        self,
        query_vector: list[float],
        *,
        filters: EditionFilter | None = None,
        limit: int = 20,
    ) -> list[RankedCandidate]:
        if len(query_vector) != EMBEDDING_COLUMN_DIMENSIONS:
            raise EmbeddingDimensionError(
                "Dimensão do vetor de consulta incompatível com o schema.",
                context={
                    "esperado": EMBEDDING_COLUMN_DIMENSIONS,
                    "recebido": len(query_vector),
                },
            )
        filters = filters if filters is not None else EditionFilter()
        params: dict[str, object] = {
            "query": query_vector,
            "limit": limit,
        }
        conditions = ["p.embedding_version_id IS NOT NULL"]
        joins = ""
        if filters.include_work_ids or filters.exclude_work_ids:
            joins = "JOIN editions e ON e.id = p.edition_id"
        if filters.include_edition_ids:
            params["include_edition_ids"] = list(filters.include_edition_ids)
            conditions.append("p.edition_id = ANY(%(include_edition_ids)s)")
        if filters.exclude_edition_ids:
            params["exclude_edition_ids"] = list(filters.exclude_edition_ids)
            conditions.append("p.edition_id <> ALL(%(exclude_edition_ids)s)")
        if filters.include_work_ids:
            params["include_work_ids"] = list(filters.include_work_ids)
            conditions.append("e.work_id = ANY(%(include_work_ids)s)")
        if filters.exclude_work_ids:
            params["exclude_work_ids"] = list(filters.exclude_work_ids)
            conditions.append("e.work_id <> ALL(%(exclude_work_ids)s)")

        where_clause = " AND ".join(conditions)
        sql = (
            "SELECT p.id AS passage_id, "  # noqa: S608
            "(1 - (p.embedding <=> %(query)s::vector)) AS score "
            f"FROM passages p {joins} "
            f"WHERE {where_clause} "
            "ORDER BY (p.embedding <=> %(query)s::vector) ASC, p.id "
            "LIMIT %(limit)s"
        )

        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        return [
            RankedCandidate(
                passage_id=row["passage_id"],
                stage=RankingStage.VECTOR,
                score=float(row["score"]),
                rank=rank,
            )
            for rank, row in enumerate(rows)
        ]
