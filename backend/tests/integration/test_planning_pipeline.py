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
from model_doubles import (
    ConceptEmbeddingProvider,
    FakePlannerProvider,
    FakeRerankerProvider,
)

from rag.application.planning import PlannerService
from rag.application.search import RetrievalService
from rag.domain.enums import AnswerMode, Depth, Intent, SearchStrategy, SourceType
from rag.domain.library import Edition, Passage, Work
from rag.domain.planning import merge_filters
from rag.domain.query import EditionFilter, LexicalQuery, QueryRequest
from rag.domain.retrieval import RetrievalPolicy
from rag.domain.runs import AnswerRun
from rag.domain.versions import ChunkingVersion, utcnow
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID
    a: UUID | None = None
    b: UUID | None = None
    c: UUID | None = None


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


async def _seed_with_passages(db: Database) -> Corpus:
    """Seed de obras, edições e passagens para provar o filtro efetivo no fluxo
    planejador → recuperação (AC-07). Mesmo padrão de `test_retrieval_pipeline`."""
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
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-plan", created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        embedding_version = await versions.get_or_create(provider.embedding_version)
        repo = PassagesRepository(conn)

        async def child(edition_id: UUID, ordinal: int, text: str) -> UUID:
            passage = Passage(
                edition_id=edition_id,
                ordinal=ordinal,
                text=text,
                token_count=len(text.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            created = await repo.create(passage, embedding=await provider.embed_query(text))
            return created.id

        a = await child(edition_a.id, 0, "O spleen sufoca a liberdade de Bentinho.")
        b = await child(
            edition_a.id, 1, "O destino e o ciúme e a liberdade misturan o spleen de Capitu."
        )
        c = await child(edition_b.id, 0, "O spleen guia a memória dos antigos.")
        await conn.execute("ANALYZE passages")

    return Corpus(work_a.id, work_b.id, edition_a.id, edition_b.id, a, b, c)


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
        plan = await PlannerService(FakePlannerProvider()).plan(
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


async def test_no_na_em_infer_inclusion_against_real_catalog(db: Database) -> None:
    """T10-03: "no"/"na"/"em" imediatamente antes da menção inferem inclusão,
    inclusive título sem acento e com catálogo real."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        no_plan = await PlannerService().plan(
            conn, _request("No Dom Casmurro, o que trata o ciúme?")
        )
        em_plan = await PlannerService().plan(
            conn, _request("Em Dom Casmurro, o que trata o ciúme?")
        )
        na_plan = await PlannerService().plan(
            conn, _request("Na semana de Dom Casmurro, o que trata o ciúme?")
        )
        no_accent_plan = await PlannerService().plan(
            conn, _request("No Memorias Postumas de Bras Cubas, o que trata o spleen?")
        )
    assert corpus.work_a in no_plan.inferred_filters.include_work_ids
    assert corpus.work_a in em_plan.inferred_filters.include_work_ids
    assert na_plan.inferred_filters.is_empty()
    assert corpus.work_b in no_accent_plan.inferred_filters.include_work_ids


async def test_plan_carries_effective_filters_against_real_catalog(db: Database) -> None:
    """T10-01: o plano produzido pelo serviço expõe o filtro efetivo fundido
    (explicit + inferred), pronto para a recuperação."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        plan = await PlannerService().plan(
            conn,
            QueryRequest(
                question="No Dom Casmurro, o que trata o ciúme?",
                answer_mode=AnswerMode.DISSERTATIVE,
                exclude_edition_ids=[corpus.edition_a],
            ),
        )
    assert corpus.work_a in plan.inferred_filters.include_work_ids
    assert plan.effective_filters == merge_filters(
        EditionFilter(exclude_edition_ids=frozenset({corpus.edition_a})), plan.inferred_filters
    )
    assert corpus.edition_a in plan.effective_filters.exclude_edition_ids
    assert corpus.edition_a not in plan.effective_filters.include_edition_ids
    assert corpus.work_a in plan.effective_filters.include_work_ids


async def test_effective_filters_flow_into_retrieval_stages(db: Database) -> None:
    """T10-01/AC-07: o filtro efetivo do plano é consumido pela recuperação —
    a obra excluída não aparece no SQL (lexical/vetorial), fusão nem reranker."""
    corpus = await _seed_with_passages(db)
    reranker = FakeRerankerProvider()
    async with db.connection() as conn:
        plan = await PlannerService().plan(
            conn, _request("Exceto Dom Casmurro, o que trata o ciúme?")
        )
        assert corpus.work_a in plan.inferred_filters.exclude_work_ids
        assert corpus.work_a in plan.effective_filters.exclude_work_ids

        run = await AnswerRunsRepository(conn).create(
            AnswerRun(
                question_original="Exceto Dom Casmurro, o que trata o ciúme?",
                question_anonymized="Exceto Dom Casmurro, o que trata o ciúme?",
                explicit_filters=EditionFilter(),
                created_at=utcnow(),
            )
        )
        result = await RetrievalService(ConceptEmbeddingProvider(), reranker).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query="liberdade spleen destino",
            filters=plan.effective_filters,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
        )

    all_ids = set(c.passage_id for c in result.lexical)
    all_ids |= set(c.passage_id for c in result.vector)
    all_ids |= set(c.passage_id for c in result.fused)
    all_ids |= set(c.passage_id for c in result.reranked)

    # As passagens de Dom Casmurro (a, b) não aparecem em NINGUM estágio;
    # Memórias Póstumas (c) permanece candidata.
    assert corpus.a is not None
    assert corpus.b is not None
    assert corpus.c is not None
    assert corpus.a not in all_ids
    assert corpus.b not in all_ids
    assert corpus.c in all_ids

    assert reranker.calls, "o reranker deve ter sido chamado"
    _, documents = reranker.calls[-1]
    assert "O spleen sufoca a liberdade de Bentinho." not in documents
    assert "O destino e o ciúme e a liberdade misturan o spleen de Capitu." not in documents
    assert "O spleen guia a memória dos antigos." in documents
