"""Planejador de consulta contra PostgreSQL real (T10; SPEC §8.2, AC-07, AC-11).

Cobre:
- resolução de filtros naturais com o catálogo real (títulos com acentos);
- menção ambígua NÃO inferida (não aplicada silenciosamente);
- estratégia automática resolvida com explicação estruturada;
- provedor de planejamento chamado só na `expanded` (subperguntas limitadas);
- filtros explícitos prevalecem sobre inferidos (`merge_filters`).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import FakePlannerProvider

from rag.application.planning import PlannerService
from rag.domain.enums import AnswerMode, Intent, SearchStrategy, SourceType
from rag.domain.library import Edition, Work
from rag.domain.planning import merge_filters
from rag.domain.query import EditionFilter, QueryRequest
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.works import WorksRepository


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID


async def _seed(db: Database) -> Corpus:
    async with db.connection() as conn:
        work_a = await WorksRepository(conn).create(Work(canonical_title="Dom Casmurro"))
        work_b = await WorksRepository(conn).create(
            Work(canonical_title="Memórias Póstumas de Brás Cubas")
        )
        edition_a = await EditionsRepository(conn).create(
            Edition(
                work_id=work_a.id,
                title="Dom Casmurro",
                source_type=SourceType.PDF_TEXT,
                source_sha256="a" * 64,
            )
        )
        edition_b = await EditionsRepository(conn).create(
            Edition(
                work_id=work_b.id,
                title="Memórias Póstumas",
                source_type=SourceType.PDF_TEXT,
                source_sha256="b" * 64,
            )
        )
    return Corpus(work_a.id, work_b.id, edition_a.id, edition_b.id)


def _request(question: str) -> QueryRequest:
    return QueryRequest(question=question, answer_mode=AnswerMode.DISSERTATIVE)


async def test_natural_filters_resolved_against_real_catalog(db: Database) -> None:
    corpus = await _seed(db)
    async with db.connection() as conn:
        plan = await PlannerService().plan(
            conn, _request("Exceto Dom Casmurro, o que trata o ciúme?")
        )
    assert corpus.work_a in plan.inferred_filters.exclude_work_ids
    assert not plan.inferred_filters.include_work_ids


async def test_accent_insensitive_title_matching(db: Database) -> None:
    corpus = await _seed(db)
    # Pergunta sem acentos ("Memorias Postumas de Bras Cubas") deve bater o
    # título canônico com acentos via normalização.
    async with db.connection() as conn:
        plan = await PlannerService().plan(
            conn, _request("Só no Memorias Postumas de Bras Cubas, o que trata o spleen?")
        )
    assert corpus.work_b in plan.inferred_filters.include_work_ids


async def test_ambiguous_mention_is_not_silently_applied(db: Database) -> None:
    await _seed(db)
    async with db.connection() as conn:
        plan = await PlannerService().plan(conn, _request("Quem escreveu Dom Casmurro?"))
    assert plan.inferred_filters.is_empty()


async def test_automatic_comparative_resolves_expanded_with_explanation(db: Database) -> None:
    await _seed(db)
    async with db.connection() as conn:
        plan = await PlannerService().plan(
            conn, _request("Compara o spleen em Dom Casmurro e Memórias Póstumas de Brás Cubas.")
        )
    assert plan.intent is Intent.COMPARATIVE
    assert plan.strategy is SearchStrategy.EXPANDED
    assert plan.strategy_explanation.requested is SearchStrategy.AUTOMATIC
    assert plan.strategy_explanation.chosen is SearchStrategy.EXPANDED
    assert plan.needs_diversity is True
    assert plan.needs_hierarchical is True


async def test_provider_called_only_on_expanded(db: Database) -> None:
    await _seed(db)
    provider = FakePlannerProvider()
    async with db.connection() as conn:
        literal_plan = await PlannerService(provider).plan(
            conn, _request("Quem escreveu Dom Casmurro?")
        )
        assert literal_plan.strategy is SearchStrategy.HYBRID
        assert provider.requests == []

        expanded_plan = await PlannerService(provider).plan(
            conn, _request("Qual é a concepção de spleen?")
        )
        assert expanded_plan.strategy is SearchStrategy.EXPANDED
        assert provider.requests, "o provedor deve ser chamado na expanded"
        assert expanded_plan.subquestions
        assert len(expanded_plan.subquestions) <= 5


async def test_explicit_strategy_is_respected(db: Database) -> None:
    await _seed(db)
    async with db.connection() as conn:
        plan = await PlannerService().plan(
            conn,
            QueryRequest(
                question="Qual é a concepção de spleen?",
                answer_mode=AnswerMode.DISSERTATIVE,
                search_strategy=SearchStrategy.LITERAL,
            ),
        )
    assert plan.strategy is SearchStrategy.LITERAL
    assert plan.strategy_explanation.requested is SearchStrategy.LITERAL


async def test_merge_filters_explicit_exclusion_wins(db: Database) -> None:
    """SPEC §8.2: exclusão explícita prevalece sobre inclusão inferida."""
    corpus = await _seed(db)
    explicit = EditionFilter(exclude_work_ids=frozenset({corpus.work_a}))
    inferred = EditionFilter(include_work_ids=frozenset({corpus.work_a}))
    merged = merge_filters(explicit, inferred)
    assert corpus.work_a not in merged.include_work_ids
    assert corpus.work_a in merged.exclude_work_ids
