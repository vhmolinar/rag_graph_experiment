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

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from rag.domain.enums import RankingStage
from rag.domain.errors import EmbeddingDimensionError
from rag.domain.query import EditionFilter
from rag.domain.runs import RankedCandidate
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

# T9-05: teto do orçamento de candidatos. `LIMIT -1` = sem limite e `LIMIT 0`
# não é um erro no PostgreSQL, então um chamador descuidado recuperaria o
# acervo inteiro ou nada de forma silenciosa. Valores inválidos para T09.
MAX_SEARCH_LIMIT = 100


class VectorSearchRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def search(
        self,
        query_vector: list[float],
        *,
        embedding_version_id: UUID,
        filters: EditionFilter | None = None,
        limit: int = 20,
    ) -> list[RankedCandidate]:
        if not isinstance(embedding_version_id, UUID):
            raise TypeError(
                "embedding_version_id deve ser UUID, "
                f"recebido {type(embedding_version_id).__name__}"
            )
        # T9-05: validação de TIPO em runtime, antes de qualquer comparação de
        # faixa — anotação de tipo não é validação no Python. `bool` é subtipo de
        # `int` (`True == 1`), então precisa ser rejeitado explicitamente; `float`
        # como `1.5` passa nas comparações de faixa e chegaria ao SQL se não barrado.
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError(
                "limit deve ser um inteiro: valores bool e float são rejeitados; "
                "uma string numérica também não é aceita."
            )
        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise ValueError(
                f"limit deve ser um inteiro entre 1 e {MAX_SEARCH_LIMIT} (recebido {limit}) — "
                "valor não-positivo ou acima do teto viola o orçamento de candidatos."
            )
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
            "embedding_version_id": embedding_version_id,
        }
        conditions = [
            "p.embedding_version_id = %(embedding_version_id)s",
            # T9-01: seleção explícita do conjunto corrente. (a) a passagem
            # pertence à execução ATIVA da sua edição; (b) é legada
            # (`index_run_id IS NULL`) e a edição NÃO tem execução ativa —
            # compatibilidade que nunca reintroduz um conjunto inativo.
            "("
            "EXISTS (SELECT 1 FROM index_runs ir WHERE ir.id = p.index_run_id AND ir.is_active) "
            "OR (p.index_run_id IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM index_runs ir2 WHERE ir2.edition_id = p.edition_id AND ir2.is_active"
            "))"
            ")",
        ]

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
