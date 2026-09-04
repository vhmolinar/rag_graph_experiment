"""Executor da estratégia `expanded`: subperguntas e aliases alteram candidatos
(R03; SPEC §8.3, B02, AC-05/AC-11/AC-15).

`ExpansionExecutor` compone o plano do planejador (T10) com a recuperação
(T09):

- `build_expansions`: valida e deduplica a consulta principal, as
  subperguntas e os aliases do plano, aplicando o orçamento total
  (`max_expansions`) — uma expansão vazia ou duplicada nunca é executada;
- `search`: executa busca lexical E vetorial por expansão, aplicando os
  filtros em TODAS as consultas e registrando a origem de cada candidato
  (`ExpansionResult`).

A fusão RRF, o teto total da fusão (`fused_top_k`), a seleção para o reranker
e a persistência ficam no `RetrievalService` (orquestrador da recuperação),
que consume as listas por expansão deste executor.

Falha fechada: qualquer falha do provedor (embedding) durante UMA expansão
propagha como erro tipado — nunca se devolve um subconjunto como sucesso
silencioso (B02/R03, checklist §9).
"""

from uuid import UUID

from psycopg import AsyncConnection

from rag.domain.enums import ExpansionKind
from rag.domain.planning import build_lexical_query, build_semantic_query, normalize_text
from rag.domain.providers import EmbeddingProvider
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan
from rag.domain.retrieval import (
    Expansion,
    ExpansionBudget,
    ExpansionResult,
)
from rag.infrastructure.repositories.search import LexicalSearchRepository
from rag.infrastructure.repositories.vector import VectorSearchRepository


class ExpansionExecutor:
    """Recuperação multiquery da estratégia `expanded` (SPEC §8.3)."""

    def __init__(self, embedding_provider: EmbeddingProvider) -> None:
        self._embedding = embedding_provider

    @staticmethod
    def build_expansions(plan: QueryPlan, budget: ExpansionBudget) -> tuple[Expansion, ...]:
        """Valida e deduplica consulta principal + subperguntas + aliases.

        Deduplicação por texto normalizado (minúsculas, sem acentos): duas
        expansões idênticas nunca são executadas duas vezes. A quantidade
        total é limitada pelo orçamento (`max_expansions`) — a principal
        ocupa o primeiro lugar; subperguntas e aliases entram na ordem do
        plano até o limite.
        """
        expansions: list[Expansion] = []
        seen: set[str] = set()

        def _add(kind: ExpansionKind, semantic_query: str, lexical_query: LexicalQuery) -> None:
            normalized = normalize_text(semantic_query)
            if not normalized:
                return
            if normalized in seen:
                return
            seen.add(normalized)
            expansions.append(
                Expansion(
                    kind=kind,
                    semantic_query=semantic_query,
                    lexical_query=lexical_query,
                )
            )

        def _semantic(text: str) -> str:
            return build_semantic_query(text)

        _add(ExpansionKind.PRIMARY, plan.semantic_query, plan.lexical_query)
        for subquestion in plan.subquestions:
            semantic_query = _semantic(subquestion)
            if not normalize_text(semantic_query):
                continue
            _add(ExpansionKind.SUBQUESTION, semantic_query, build_lexical_query(subquestion))
        for alias in plan.aliases:
            semantic_query = _semantic(alias)
            if not normalize_text(semantic_query):
                continue
            _add(ExpansionKind.ALIAS, semantic_query, build_lexical_query(alias))
        return tuple(expansions[: budget.max_expansions])

    async def search(
        self,
        conn: AsyncConnection,
        *,
        expansions: tuple[Expansion, ...],
        embedding_version_id: UUID,
        filters: EditionFilter,
        budget: ExpansionBudget,
    ) -> tuple[ExpansionResult, ...]:
        """Executa busca lexical E vetorial por expansão, com os MESMOS
        filtros em todas as consultas (AC-07: uma obra excluída não aparece
        em NINGUMA expansão).

        Guarda degenerada: se o embedding da consulta for vazio (sem sinal
        semântico), o estágio vetorial dessa expansão é PULADO (nunca um
        cosseno NaN vira score não-persistível) — a busca lexical continua
        funcionando e a expansão nunca se perde inteira (B02/R03).
        """
        lexical_repo = LexicalSearchRepository(conn)
        vector_repo = VectorSearchRepository(conn)
        results: list[ExpansionResult] = []
        for expansion in expansions:
            lexical = await lexical_repo.search(
                expansion.lexical_query, filters=filters, limit=budget.lexical_top_k
            )
            query_vector = await self._embedding.embed_query(expansion.semantic_query)
            if any(value != 0.0 for value in query_vector):
                vector = await vector_repo.search(
                    query_vector,
                    embedding_version_id=embedding_version_id,
                    filters=filters,
                    limit=budget.vector_top_k,
                )
            else:
                vector = []
            results.append(
                ExpansionResult(
                    expansion=expansion,
                    lexical=tuple(lexical),
                    vector=tuple(vector),
                )
            )
        return tuple(results)
