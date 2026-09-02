"""Serviço de planejamento de consulta (T10; SPEC §8.2-§8.3, AC-07, AC-11).

`PlannerService` compone as funções determinísticas do domínio
(`domain/planning.py`) com a geração limitada de subperguntas/aliases do
provedor de planejamento (só na estratégia `expanded`) e o catálogo de obras
para resolver filtros naturais. Produz um `QueryPlan` validado, com estratégia
RESOLVIDA e explicação estruturada (SPEC §8.3).

Filtros inferidos ficam em `QueryPlan.inferred_filters` (voltam ao cliente como
chips editáveis); `QueryPlan.effective_filters` é o filtro efetivo com a
prioridade de filtros explícitos aplicada via `domain.planning.merge_filters`
antes da recuperação (SPEC §8.2, AC-07; T10-01 da revisão).

A estratégia `expanded` exige um provedor de planejamento: sem provedor não há
geração limitada de subperguntas/aliases, e o plano NUNCA declara expansão sem
expansão a executar — falha fechada com `ModelUnavailableError` (T10-02 da
revisão).
"""

from psycopg import AsyncConnection

from rag.domain.enums import SearchStrategy
from rag.domain.errors import ModelUnavailableError
from rag.domain.planning import (
    CatalogEntry,
    build_lexical_query,
    build_semantic_query,
    classify_intent,
    diversity_for,
    hierarchical_for,
    merge_filters,
    normalize_text,
    resolve_natural_filters,
    resolve_strategy,
)
from rag.domain.providers import PlannerProvider, PlanningRequest
from rag.domain.query import MAX_SUBQUESTIONS, QueryPlan, QueryRequest
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.works import WorksRepository


class PlannerService:
    def __init__(self, planner_provider: PlannerProvider | None = None) -> None:
        self._provider = planner_provider

    async def plan(self, conn: AsyncConnection, request: QueryRequest) -> QueryPlan:
        question = request.question
        intent = classify_intent(question)
        lexical = build_lexical_query(question)
        semantic = build_semantic_query(question)

        strategy, explanation = resolve_strategy(request.search_strategy, intent)

        subquestions: tuple[str, ...] = ()
        aliases: tuple[str, ...] = ()
        concept_labels: tuple[str, ...] = ()
        if strategy is SearchStrategy.EXPANDED:
            if self._provider is None:
                raise ModelUnavailableError(
                    "A estratégia 'expanded' exige um provedor de planejamento "
                    "configurado; sem provedor não há expansão a executar."
                )
            suggestion = await self._provider.plan(
                PlanningRequest(question=question, depth=request.depth)
            )
            # Geração limitada (SPEC §8.2): nunca excede o orçamento declarado.
            subquestions = suggestion.subquestions[:MAX_SUBQUESTIONS]
            aliases = suggestion.aliases
            concept_labels = suggestion.concept_labels
            if suggestion.semantic_query:
                semantic = suggestion.semantic_query

        catalog = await self._load_catalog(conn)
        inferred = resolve_natural_filters(question, catalog)
        effective = merge_filters(request.explicit_filter(), inferred)

        return QueryPlan(
            intent=intent,
            lexical_query=lexical,
            semantic_query=semantic,
            strategy=strategy,
            strategy_explanation=explanation,
            subquestions=subquestions,
            aliases=aliases,
            concept_labels=concept_labels,
            inferred_filters=inferred,
            effective_filters=effective,
            needs_diversity=diversity_for(intent),
            needs_hierarchical=hierarchical_for(intent),
        )

    @staticmethod
    async def _load_catalog(conn: AsyncConnection) -> dict[str, CatalogEntry]:
        """Catálogo título canônico normalizado → entrada (NOTES.md §10.11 item 8)."""
        works_repo = WorksRepository(conn)
        editions_repo = EditionsRepository(conn)
        catalog: dict[str, CatalogEntry] = {}
        for work in await works_repo.list_all():
            edition_list = await editions_repo.list_by_work(work.id)
            catalog[normalize_text(work.canonical_title)] = CatalogEntry(
                work_id=work.id,
                title=work.canonical_title,
                edition_ids=tuple(edition.id for edition in edition_list),
            )
        return catalog
