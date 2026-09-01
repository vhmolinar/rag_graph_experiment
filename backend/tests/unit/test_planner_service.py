"""Serviço de planejamento (T10; SPEC §8.2-§8.3, AC-07, AC-11).

Cobre: estratégia automática resolvida com explicação estruturada; estratégias
explícitas respeitadas; geração limitada de subperguntas/aliases (provedor só
na `expanded`); falha do provedor propagha fechada; filtros naturais resolvidos
com o catálogo; diversidade/índice hierárquico derivados da intenção.
"""

import sys
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import FakePlannerProvider
from psycopg import AsyncConnection

from rag.application.planning import PlannerService
from rag.domain.enums import AnswerMode, Depth, Intent, SearchStrategy
from rag.domain.errors import ModelTimeoutError, ModelUnavailableError
from rag.domain.planning import CatalogEntry, merge_filters
from rag.domain.providers import PlannedQuery
from rag.domain.query import QueryRequest


def _conn() -> AsyncConnection:
    """Conexão falsa: `_load_catalog` é sempre monkeypatcheada nos testes."""
    return cast(AsyncConnection, object())


def _request(
    question: str,
    strategy: SearchStrategy = SearchStrategy.AUTOMATIC,
    *,
    include_edition_ids: list[UUID] | None = None,
    exclude_edition_ids: list[UUID] | None = None,
) -> QueryRequest:
    return QueryRequest(
        question=question,
        answer_mode=AnswerMode.DISSERTATIVE,
        search_strategy=strategy,
        include_edition_ids=include_edition_ids or [],
        exclude_edition_ids=exclude_edition_ids or [],
    )


@pytest.fixture
def empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _load(_conn: AsyncConnection) -> dict[str, CatalogEntry]:
        return {}

    monkeypatch.setattr(PlannerService, "_load_catalog", staticmethod(_load))


@pytest.fixture
def service(empty_catalog: None) -> PlannerService:
    return PlannerService()


@pytest.fixture
def service_with_provider(empty_catalog: None) -> PlannerService:
    return PlannerService(FakePlannerProvider())


class TestAutomaticStrategy:
    async def test_conceptual_resolves_expanded_with_explanation(
        self, service_with_provider: PlannerService
    ) -> None:
        plan = await service_with_provider.plan(
            _conn(), _request("Qual é a concepção de spleen em Dom Casmurro?")
        )
        assert plan.intent is Intent.CONCEPTUAL
        assert plan.strategy is SearchStrategy.EXPANDED
        assert plan.strategy_explanation.requested is SearchStrategy.AUTOMATIC
        assert plan.strategy_explanation.chosen is SearchStrategy.EXPANDED
        assert plan.strategy_explanation.intent_signals == ("intenção=conceptual",)
        assert plan.lexical_query.required_terms

    async def test_factual_prioritizes_relevance(self, service: PlannerService) -> None:
        """AC-07/§8.6: factual maximiza relevância (híbrida, sem diversidade)."""
        plan = await service.plan(_conn(), _request("Quem escreveu Dom Casmurro?"))
        assert plan.intent is Intent.FACTUAL
        assert plan.strategy is SearchStrategy.HYBRID
        assert plan.needs_diversity is False
        assert plan.needs_hierarchical is False

    async def test_comparative_seeks_coverage(self, service_with_provider: PlannerService) -> None:
        plan = await service_with_provider.plan(
            _conn(), _request("Compara o spleen em Dom Casmurro e Memórias Póstumas.")
        )
        assert plan.intent is Intent.COMPARATIVE
        assert plan.strategy is SearchStrategy.EXPANDED
        assert plan.needs_diversity is True
        assert plan.needs_hierarchical is True

    async def test_navigational_resolves_literal(self, service: PlannerService) -> None:
        plan = await service.plan(_conn(), _request("Em que capítulo fica o ciúme?"))
        assert plan.intent is Intent.NAVIGATIONAL
        assert plan.strategy is SearchStrategy.LITERAL


class TestExplicitStrategy:
    async def test_literal_is_respected(self, service: PlannerService) -> None:
        plan = await service.plan(
            _conn(), _request("Qual é a concepção de spleen?", SearchStrategy.LITERAL)
        )
        assert plan.strategy is SearchStrategy.LITERAL
        assert plan.strategy_explanation.requested is SearchStrategy.LITERAL
        assert plan.strategy_explanation.chosen is SearchStrategy.LITERAL

    async def test_expanded_is_respected(self, service_with_provider: PlannerService) -> None:
        plan = await service_with_provider.plan(
            _conn(), _request("Qual é a concepção de spleen?", SearchStrategy.EXPANDED)
        )
        assert plan.strategy is SearchStrategy.EXPANDED
        assert plan.subquestions, "expanded sem provedor não pode declarar expansão sem expandir"


class TestExpandedRequiresProvider:
    async def test_explicit_expanded_without_provider_fails_closed(
        self, service: PlannerService
    ) -> None:
        """T10-02: expanded SEM provedor NUNCA produz um plano que declara
        expansão sem expansão a executar — falha tipada."""
        with pytest.raises(ModelUnavailableError):
            await service.plan(
                _conn(), _request("Qual é a concepção de spleen?", SearchStrategy.EXPANDED)
            )

    async def test_automatic_expanded_without_provider_fails_closed(
        self, service: PlannerService
    ) -> None:
        """T10-02: automatic de pergunta conceitual resolve `expanded`; sem
        provedor, falha tipada em vez de devolver expansão vazia."""
        with pytest.raises(ModelUnavailableError):
            await service.plan(_conn(), _request("Qual é a concepção de spleen?"))
        with pytest.raises(ModelUnavailableError):
            await service.plan(
                _conn(), _request("Compara o spleen em Dom Casmurro e Memórias Póstumas.")
            )


class TestProviderIntegration:
    async def test_expanded_calls_provider_and_carries_suggestion(
        self, empty_catalog: None
    ) -> None:
        def suggestion(_request: object) -> PlannedQuery:
            return PlannedQuery(
                semantic_query="sugestão semântica",
                subquestions=("s1", "s2", "s3"),
                aliases=("spleen", "baço"),
                concept_labels=("spleen",),
            )

        provider = FakePlannerProvider(suggestion_factory=suggestion)
        service = PlannerService(provider)
        plan = await service.plan(
            _conn(), _request("Qual é a concepção de spleen?", SearchStrategy.EXPANDED)
        )
        assert provider.requests, "o provedor deve ser chamado na expanded"
        assert provider.requests[0].depth is Depth.STANDARD
        assert plan.semantic_query == "sugestão semântica"
        assert plan.subquestions == ("s1", "s2", "s3")
        assert plan.aliases == ("spleen", "baço")
        assert plan.concept_labels == ("spleen",)

    async def test_literal_does_not_call_provider(self, empty_catalog: None) -> None:
        provider = FakePlannerProvider()
        service = PlannerService(provider)
        plan = await service.plan(
            _conn(), _request("Quem escreveu Dom Casmurro?", SearchStrategy.LITERAL)
        )
        assert provider.requests == []
        assert plan.subquestions == ()
        assert plan.aliases == ()

    async def test_provider_failure_propagates_fail_closed(self, empty_catalog: None) -> None:
        provider = FakePlannerProvider(fail_with=[ModelTimeoutError()])
        service = PlannerService(provider)
        with pytest.raises(ModelTimeoutError):
            await service.plan(
                _conn(), _request("Qual é a concepção de spleen?", SearchStrategy.EXPANDED)
            )


class TestNaturalFilters:
    async def test_inferred_exclusion_from_question(self, monkeypatch: pytest.MonkeyPatch) -> None:
        work = uuid4()

        async def catalog(_conn: AsyncConnection) -> dict[str, CatalogEntry]:
            return {
                "dom casmurro": CatalogEntry(work_id=work, title="Dom Casmurro", edition_ids=())
            }

        monkeypatch.setattr(PlannerService, "_load_catalog", staticmethod(catalog))
        service = PlannerService()
        plan = await service.plan(_conn(), _request("Exceto Dom Casmurro, o que trata o ciúme?"))
        assert work in plan.inferred_filters.exclude_work_ids

    async def test_ambiguous_mention_is_not_inferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        work = uuid4()

        async def catalog(_conn: AsyncConnection) -> dict[str, CatalogEntry]:
            return {
                "dom casmurro": CatalogEntry(work_id=work, title="Dom Casmurro", edition_ids=())
            }

        monkeypatch.setattr(PlannerService, "_load_catalog", staticmethod(catalog))
        service = PlannerService()
        plan = await service.plan(_conn(), _request("Quem escreveu Dom Casmurro?"))
        assert plan.inferred_filters.is_empty()

    async def test_explicit_filters_do_not_enter_inferred(self, empty_catalog: None) -> None:
        edition = uuid4()
        service = PlannerService()
        plan = await service.plan(
            _conn(),
            _request(
                "Quem escreveu Dom Casmurro?",
                include_edition_ids=[edition],
            ),
        )
        # Os filtros explícitos pertencem a `QueryRequest`/`AnswerRun`; os
        # inferidos NÃO devem duplicá-los.
        assert plan.inferred_filters.is_empty()


class TestEffectiveFilters:
    def _catalog(self, monkeypatch: pytest.MonkeyPatch, work: UUID, edition: UUID) -> None:
        async def catalog(_conn: AsyncConnection) -> dict[str, CatalogEntry]:
            return {
                "dom casmurro": CatalogEntry(
                    work_id=work, title="Dom Casmurro", edition_ids=(edition,)
                )
            }

        monkeypatch.setattr(PlannerService, "_load_catalog", staticmethod(catalog))

    async def test_plan_carries_merged_effective_filters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T10-01: o plano do serviço expõe o filtro efetivo fundido
        (explicit + inferred com prioridade), pronto para a recuperação;
        `inferred_filters` permanece separado para as chips."""
        work, edition = uuid4(), uuid4()
        self._catalog(monkeypatch, work, edition)
        request = _request("No Dom Casmurro, o que trata o ciúme?", exclude_edition_ids=[edition])
        plan = await PlannerService().plan(_conn(), request)

        assert plan.inferred_filters.include_work_ids == frozenset({work})
        assert plan.inferred_filters.exclude_edition_ids == frozenset()
        assert plan.effective_filters == merge_filters(
            request.explicit_filter(), plan.inferred_filters
        )
        assert plan.effective_filters.include_work_ids == frozenset({work})
        assert plan.effective_filters.exclude_edition_ids == frozenset({edition})
        assert not (
            plan.effective_filters.include_edition_ids & plan.effective_filters.exclude_edition_ids
        )
        assert not (
            plan.effective_filters.include_work_ids & plan.effective_filters.exclude_work_ids
        )

    async def test_explicit_edition_exclusion_preserved_in_effective(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T10-01/AC-07: a decisão explícita (edição excluída) permanece no
        filtro efetivo decidido pelo serviço, sem conflito include/exclude."""
        work, edition = uuid4(), uuid4()
        self._catalog(monkeypatch, work, edition)
        request = _request("No Dom Casmurro, o que trata o ciúme?", exclude_edition_ids=[edition])
        plan = await PlannerService().plan(_conn(), request)

        effective = plan.effective_filters
        assert effective.exclude_edition_ids == frozenset({edition})
        assert edition not in effective.include_edition_ids
        assert effective.include_work_ids == frozenset({work})
        assert not (effective.include_work_ids & effective.exclude_work_ids)
