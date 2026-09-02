"""Pipeline de montagem de contexto e modo quote contra PostgreSQL real (T12).

Seeds works/editions/sections/pages/passages (pais e filhos) e exercita:

- snapshots do modo quote com trechos literais e metadados (AC-03, AC-08);
- nenhuna chamada ao provedor de geração no modo quote (AC-08) — verificação
  estrutural (T12-03);
- abertura da origem reproduz o texto (offsets/páginas recompõem o trecho),
  inclusive para passagens multipágina (T12-01, AC-03);
- o orçamento de contexto nunca é excedido (SPEC §9.1);
- diversidade adaptativa: comparativa seleciona de ambas edições, sem
  preencher com obra menos relevante (SPEC §8.6, AC-11), e diversifica por
  conceito quando os conceitos estão associados (T12-02);
- expansão parental no contexto montado, nunca citável (SPEC §7.3, AC-12);
- a política de contexto fica registrada como `ContextPolicyVersion` (AC-15).
"""

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import ConceptEmbeddingProvider, FakeRerankerProvider

from rag.application.context import ContextService
from rag.application.search import RetrievalService
from rag.domain.answer import QuoteResponse
from rag.domain.context import ContextBudget, ContextPolicy, PackedContext, context_total_chars
from rag.domain.enums import Depth, Intent, RankingStage, SearchStrategy, SourceType
from rag.domain.knowledge import Concept
from rag.domain.library import Edition, Page, Passage, Section, Work
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.retrieval import RetrievalPolicy, RetrievalResult
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import (
    ChunkingVersion,
    ContextPolicyVersion,
    ModelEndpointVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.enrichment import ConceptsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

pytestmark = pytest.mark.integration

_SEMANTIC_QUERY = "liberdade spleen destino"

_A0_TEXT = "spleen sufoca a liberdade de Bentinho."
_A1_TEXT = "O destino e o ciúme e a liberdade misturan o spleen de Capitu."
_B0_TEXT = "O spleen guia a memória dos antigos."
_A0_EXCERPT = "spleen sufoca a"  # _A0_TEXT[0:15]

_PARENT_A0_TEXT = "Capítulo I: " + _A0_TEXT
_PARENT_A1_TEXT = "Seção 1: " + _A1_TEXT
_PARENT_B0_TEXT = "Capítulo Único: " + _B0_TEXT

# Passagem multipágina (T12-01): começa numa página e termina na outra. Os
# offsets são relativos a páginas DISTINTAS e podem ser invertidos entre elas
# (T12-R2-01): char_start=30 na página 0, char_end=13 na página 1 — corretos
# e reproduzíveis, ainda que char_end < char_start.
_MULTI_START_TEXT = "O destino guia a liberdade do spleen de Bentinho."
_MULTI_END_TEXT = "A memória dos antigos pertence ao spleen."
_MULTI_CHAR_START = len("O destino guia a liberdade do ")
_MULTI_CHAR_END = len("A memória dos ")
_MULTI_TEXT = _MULTI_START_TEXT[_MULTI_CHAR_START:] + "\n" + _MULTI_END_TEXT[:_MULTI_CHAR_END]


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID
    a0: UUID
    a1: UUID
    b0: UUID
    page_a0: UUID
    page_a1: UUID
    page_b0: UUID
    parent_a0: UUID
    parent_a1: UUID
    parent_b0: UUID


@dataclass
class MultipageCorpus:
    work: UUID
    edition: UUID
    page_0: UUID
    page_1: UUID
    span: UUID


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

        s_a0 = Section(
            edition_id=edition_a.id,
            level=0,
            ordinal=0,
            path=["Capítulo I"],
            title="Capítulo I",
        )
        s_a1 = Section(
            edition_id=edition_a.id,
            level=1,
            ordinal=1,
            path=["Capítulo I", "Seção 1"],
            title="Seção 1",
            parent_section_id=s_a0.id,
        )
        s_b0 = Section(
            edition_id=edition_b.id,
            level=0,
            ordinal=0,
            path=["Capítulo Único"],
            title="Capítulo Único",
        )
        await SectionsRepository(conn).create_many([s_a0, s_a1, s_b0])

        page_a0 = Page.create(
            edition_id=edition_a.id, physical_index=0, text=_A0_TEXT, printed_label="p. 1"
        )
        page_a1 = Page.create(
            edition_id=edition_a.id, physical_index=1, text=_A1_TEXT, printed_label="p. 2"
        )
        page_b0 = Page.create(
            edition_id=edition_b.id, physical_index=0, text=_B0_TEXT, printed_label="p. 1"
        )
        await PagesRepository(conn).create_many([page_a0, page_a1, page_b0])

        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-ctx", created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        # A versão do embedding tem que ser a MESMA que `_retrieval_service`
        # usa (`ConceptEmbeddingProvider.embedding_version`) — o estágio
        # vetorial filtra por `embedding_version_id` (T9-01); uma versão
        # distinta deixaria o estágio vetorial sem candidatos.
        embedding_version = await versions.get_or_create(provider.embedding_version)
        repo = PassagesRepository(conn)

        parent_a0 = UUID("b0000000-0000-0000-0000-000000000001")
        parent_a1 = UUID("b0000000-0000-0000-0000-000000000002")
        parent_b0 = UUID("b0000000-0000-0000-0000-000000000003")
        a0 = UUID("a0000000-0000-0000-0000-000000000001")
        a1 = UUID("a0000000-0000-0000-0000-000000000002")
        b0 = UUID("a0000000-0000-0000-0000-000000000003")

        # Pais (seção-folha, sem embedding) primeiro: filhos referenciam via FK.
        await repo.create(
            Passage(
                id=parent_a0,
                edition_id=edition_a.id,
                ordinal=0,
                text=_PARENT_A0_TEXT,
                token_count=len(_PARENT_A0_TEXT.split()),
                chunking_version_id=chunking.id,
                section_id=s_a0.id,
                page_start_id=page_a0.id,
                page_end_id=page_a0.id,
            )
        )
        await repo.create(
            Passage(
                id=parent_a1,
                edition_id=edition_a.id,
                ordinal=1,
                text=_PARENT_A1_TEXT,
                token_count=len(_PARENT_A1_TEXT.split()),
                chunking_version_id=chunking.id,
                section_id=s_a1.id,
                page_start_id=page_a1.id,
                page_end_id=page_a1.id,
            )
        )
        await repo.create(
            Passage(
                id=parent_b0,
                edition_id=edition_b.id,
                ordinal=0,
                text=_PARENT_B0_TEXT,
                token_count=len(_PARENT_B0_TEXT.split()),
                chunking_version_id=chunking.id,
                section_id=s_b0.id,
                page_start_id=page_b0.id,
                page_end_id=page_b0.id,
            )
        )

        # Filhos (unidades citáveis, com embedding).
        await repo.create(
            Passage(
                id=a0,
                edition_id=edition_a.id,
                ordinal=2,
                text=_A0_EXCERPT,
                token_count=3,
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=s_a0.id,
                page_start_id=page_a0.id,
                page_end_id=page_a0.id,
                char_start=0,
                char_end=len(_A0_EXCERPT),
                parent_passage_id=parent_a0,
            ),
            embedding=await provider.embed_query(_A0_EXCERPT),
        )
        await repo.create(
            Passage(
                id=a1,
                edition_id=edition_a.id,
                ordinal=3,
                text=_A1_TEXT,
                token_count=len(_A1_TEXT.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=s_a1.id,
                page_start_id=page_a1.id,
                page_end_id=page_a1.id,
                parent_passage_id=parent_a1,
            ),
            embedding=await provider.embed_query(_A1_TEXT),
        )
        await repo.create(
            Passage(
                id=b0,
                edition_id=edition_b.id,
                ordinal=1,
                text=_B0_TEXT,
                token_count=len(_B0_TEXT.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=s_b0.id,
                page_start_id=page_b0.id,
                page_end_id=page_b0.id,
                parent_passage_id=parent_b0,
            ),
            embedding=await provider.embed_query(_B0_TEXT),
        )
        await conn.execute("ANALYZE passages")

    return Corpus(
        work_a=work_a.id,
        work_b=work_b.id,
        edition_a=edition_a.id,
        edition_b=edition_b.id,
        a0=a0,
        a1=a1,
        b0=b0,
        page_a0=page_a0.id,
        page_a1=page_a1.id,
        page_b0=page_b0.id,
        parent_a0=parent_a0,
        parent_a1=parent_a1,
        parent_b0=parent_b0,
    )


async def _seed_multipage(db: Database) -> MultipageCorpus:
    """Corpus mínimo com uma passagem que atravessa duas páginas (T12-01)."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra Multipágina"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Obra Multipágina",
                source_type=SourceType.PDF_TEXT,
                source_sha256="c" * 64,
            )
        )
        page_0 = Page.create(
            edition_id=edition.id,
            physical_index=0,
            text=_MULTI_START_TEXT,
            printed_label="p. 1",
        )
        page_1 = Page.create(
            edition_id=edition.id,
            physical_index=1,
            text=_MULTI_END_TEXT,
            printed_label="p. 2",
        )
        await PagesRepository(conn).create_many([page_0, page_1])

        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-mp", created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        embedding_version = await versions.get_or_create(provider.embedding_version)
        repo = PassagesRepository(conn)

        span = UUID("c0000000-0000-0000-0000-000000000001")
        # `char_start` é relativo ao texto de `page_0`; `char_end` ao texto de
        # `page_1` — mesmo contrato do chunker (NOTES.md §10.6 item 3).
        await repo.create(
            Passage(
                id=span,
                edition_id=edition.id,
                ordinal=0,
                text=_MULTI_TEXT,
                token_count=len(_MULTI_TEXT.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                page_start_id=page_0.id,
                page_end_id=page_1.id,
                char_start=_MULTI_CHAR_START,
                char_end=_MULTI_CHAR_END,
            ),
            embedding=await provider.embed_query(_MULTI_TEXT),
        )
        await conn.execute("ANALYZE passages")

    return MultipageCorpus(
        work=work.id,
        edition=edition.id,
        page_0=page_0.id,
        page_1=page_1.id,
        span=span,
    )


async def _seed_concepts(db: Database, corpus: Corpus) -> None:
    """Associa conceitos às passagens do corpus (T12-02): a0/a1 → "liberdade",
    b0 → "destino" — associação rastreável via `concept_evidence`."""
    async with db.connection() as conn:
        versions = VersionsRepository(conn)
        extractor = await versions.get_or_create(
            ModelEndpointVersion(
                label="concept-extractor",
                endpoint_kind="generator",
                provider="openai-compatible",
                model_name="fake-model",
                created_at=utcnow(),
            )
        )
        concepts = ConceptsRepository(conn)
        liberdade = await concepts.get_or_create(Concept(normalized_label="liberdade"))
        destino = await concepts.get_or_create(Concept(normalized_label="destino"))
        await concepts.add_evidence(liberdade.id, corpus.a0, 1.0, extractor.id)
        await concepts.add_evidence(liberdade.id, corpus.a1, 1.0, extractor.id)
        await concepts.add_evidence(destino.id, corpus.b0, 1.0, extractor.id)


def _retrieval_service() -> RetrievalService:
    return RetrievalService(ConceptEmbeddingProvider(), FakeRerankerProvider())


async def _retrieve(db: Database) -> RetrievalResult:
    async with db.connection() as conn:
        run = await AnswerRunsRepository(conn).create(
            AnswerRun(
                question_original="Consulta de contexto T12.",
                question_anonymized="Consulta de contexto T12.",
                explicit_filters=EditionFilter(),
            )
        )
        return await _retrieval_service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
        )


def _plan(*, needs_diversity: bool, intent: Intent = Intent.COMPARATIVE) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        lexical_query=LexicalQuery(required_terms=("sufoca",)),
        semantic_query=_SEMANTIC_QUERY,
        strategy=SearchStrategy.HYBRID,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.AUTOMATIC,
            chosen=SearchStrategy.HYBRID,
            intent_signals=(f"intenção={intent.value}",),
            rationale="Plano de teste de T12.",
        ),
        needs_diversity=needs_diversity,
        needs_hierarchical=True,
    )


def _context_policy(
    *,
    max_evidences: int = 8,
    max_context_chars: int = 8000,
    parent_expansion_chars: int = 1200,
    per_edition_limit: int | None = 6,
) -> ContextPolicy:
    budgets = tuple(
        (
            depth,
            ContextBudget(
                depth=depth,
                max_evidences=max_evidences,
                max_context_chars=max_context_chars,
                parent_expansion_chars=parent_expansion_chars,
                per_edition_limit=per_edition_limit,
            ),
        )
        for depth in Depth
    )
    return ContextPolicy(budgets=budgets)


async def _assemble(db: Database, *, plan: QueryPlan, policy: ContextPolicy) -> PackedContext:
    retrieval = await _retrieve(db)
    service = ContextService()
    async with db.connection() as conn:
        return await service.assemble(
            conn, plan=plan, retrieval=retrieval, depth=Depth.STANDARD, policy=policy
        )


async def _quote(db: Database, *, plan: QueryPlan, policy: ContextPolicy) -> QuoteResponse:
    retrieval = await _retrieve(db)
    service = ContextService()
    async with db.connection() as conn:
        return await service.quote(
            conn, plan=plan, retrieval=retrieval, depth=Depth.STANDARD, policy=policy
        )


async def test_quote_snapshot_with_text_and_metadata(db: Database, tmp_path: Path) -> None:
    """AC-03/AC-08: o snapshot do modo quote contém trechos literais e
    metadados de origem (obra, edição, seção, página, offsets), sem prosa."""
    corpus = await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    quote = await _quote(db, plan=plan, policy=ContextPolicy.defaults())

    # Ordem = ranking reranked (a1, a0, b0) — ordem corresponde ao ranking.
    assert [ref.text for ref in quote.evidences] == [_A1_TEXT, _A0_EXCERPT, _B0_TEXT]
    for ref in quote.evidences:
        assert ref.text in {_A1_TEXT, _A0_EXCERPT, _B0_TEXT}  # só trechos literais

    a1_ref = quote.evidences[0]
    assert a1_ref.passage_id == corpus.a1
    assert a1_ref.edition_id == corpus.edition_a
    assert a1_ref.work_id == corpus.work_a
    assert a1_ref.section_path == ("Capítulo I", "Seção 1")
    assert a1_ref.physical_page == 1
    assert a1_ref.printed_label == "p. 2"
    assert a1_ref.rank == 0

    a0_ref = quote.evidences[1]
    assert a0_ref.section_path == ("Capítulo I",)
    assert a0_ref.physical_page == 0
    assert a0_ref.printed_label == "p. 1"
    assert a0_ref.char_start == 0
    assert a0_ref.char_end == len(_A0_EXCERPT)
    assert a0_ref.rank == 1


async def test_quote_has_no_generation_path(db: Database) -> None:
    """AC-08 — verificação ESTRUCTURAL (T12-03): o modo `quote` não tem caminho
    de geração. `ContextService` não recebe nenhum provedor de geração em
    nenhum método (nem `quote` nem `assemble`) e a resposta é só trechos
    literais do acervo. NÃO é um spy de chamadas: não existe ainda uma
    orquestração que compone `quote` com geração (pertence a T13), então a
    asserção estrutural é a evidência honesta disponível."""
    await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    quote = await _quote(db, plan=plan, policy=ContextPolicy.defaults())

    assert quote.evidences
    seed_texts = {_A1_TEXT, _A0_EXCERPT, _B0_TEXT}
    assert all(ref.text in seed_texts for ref in quote.evidences)

    # Estrutural: nem `quote` nem `assemble` aceitam provedor de geração.
    service = ContextService()
    for name, method in (("quote", service.quote), ("assemble", service.assemble)):
        provider_params = [
            p for p in inspect.signature(method).parameters if "generator" in p or "provider" in p
        ]
        assert provider_params == [], (
            f"{name} não deve aceitar provedor de geração (T12-03), recebido {provider_params}"
        )


async def test_opening_origin_reproduces_text(db: Database) -> None:
    """AC-03: com edição, página física e offsets, o trecho se reproduz
    exatamente — o que o leitor (T17) usa para abrir e destacar."""
    corpus = await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    quote = await _quote(db, plan=plan, policy=ContextPolicy.defaults())

    async with db.connection() as conn:
        pages_by_edition = {
            edition_id: await PagesRepository(conn).list_by_edition(edition_id)
            for edition_id in {corpus.edition_a, corpus.edition_b}
        }
    for ref in quote.evidences:
        start_page = next(
            p for p in pages_by_edition[ref.edition_id] if p.physical_index == ref.physical_page
        )
        if ref.char_start is None or ref.char_end is None:
            assert start_page.text == ref.text
            continue
        if ref.page_end is None or ref.page_end == ref.physical_page:
            assert start_page.text[ref.char_start : ref.char_end] == ref.text
        else:
            # T12-01: passagem multipágina — `char_start` sobre a página de
            # início, `char_end` sobre a página de fim (NOTES.md §10.6 item 3).
            end_page = next(
                p for p in pages_by_edition[ref.edition_id] if p.physical_index == ref.page_end
            )
            reconstructed = start_page.text[ref.char_start :] + "\n" + end_page.text[: ref.char_end]
            assert reconstructed == ref.text


async def test_quote_multipage_passage_reproduces_text(db: Database) -> None:
    """T12-01/AC-03: uma passagem que atravessa páginas é citável — a
    referência transporta início e fim da localização (IDs, índices físicos,
    rótulos impressos e offsets relativos a cada página) e a abertura da
    origem reproduz o trecho exato, incluindo o destaque por página."""
    corpus = await _seed_multipage(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    retrieval = RetrievalResult(
        reranked=(
            RankedCandidate(
                passage_id=corpus.span,
                stage=RankingStage.RERANKED,
                score=1.0,
                rank=0,
            ),
        )
    )
    service = ContextService()
    async with db.connection() as conn:
        quote = await service.quote(
            conn,
            plan=plan,
            retrieval=retrieval,
            depth=Depth.STANDARD,
            policy=ContextPolicy.defaults(),
        )

    assert len(quote.evidences) == 1
    ref = quote.evidences[0]
    assert ref.passage_id == corpus.span
    assert ref.physical_page == 0
    assert ref.page_end == 1
    assert ref.printed_label == "p. 1"
    assert ref.printed_end_label == "p. 2"
    assert ref.page_start_id == corpus.page_0
    assert ref.page_end_id == corpus.page_1
    assert ref.char_start == _MULTI_CHAR_START
    assert ref.char_end == _MULTI_CHAR_END
    assert ref.text == _MULTI_TEXT

    # Reconstrução (abertura da origem) e destaque: com as duas páginas e os
    # offsets relativos a cada uma, o trecho citado se reproduz exatamente.
    async with db.connection() as conn:
        pages = {
            page.physical_index: page
            for page in await PagesRepository(conn).list_by_edition(corpus.edition)
        }
    start_page = pages[ref.physical_page]
    end_page = pages[ref.page_end]
    reconstructed = start_page.text[ref.char_start :] + "\n" + end_page.text[: ref.char_end]
    assert reconstructed == ref.text
    # Destaque: cada offset cai dentro do texto da sua própria página.
    assert ref.char_start <= len(start_page.text)
    assert ref.char_end <= len(end_page.text)


async def test_concept_diversity_changes_selection_in_pipeline(db: Database) -> None:
    """SPEC §8.6/T12-02: com diversidade, os candidatos que traem um conceito
    novo são preferidos sobre os que repetem conceitos já cobertos — a
    diversificação por conceito altera a seleção no pipeline completo
    (recuperação + montagem)."""
    corpus = await _seed(db)
    await _seed_concepts(db, corpus)
    policy = _context_policy(max_evidences=3, per_edition_limit=10, parent_expansion_chars=0)

    # Sem diversidade: ordem do ranking (a1, a0, b0) prevalece — a0 repete o
    # conceito de a1 mas fica na posição 2 por relevância.
    packed_undiverse = await _assemble(
        db, plan=_plan(needs_diversity=False, intent=Intent.FACTUAL), policy=policy
    )
    assert [item.evidence.passage_id for item in packed_undiverse.evidences] == [
        corpus.a1,
        corpus.a0,
        corpus.b0,
    ]

    # Com diversidade por conceito: b0 (conceito "destino") precede a0
    # (repetição do conceito "liberdade") — a seleção mudou.
    packed_diverse = await _assemble(
        db, plan=_plan(needs_diversity=True, intent=Intent.CONCEPTUAL), policy=policy
    )
    assert [item.evidence.passage_id for item in packed_diverse.evidences] == [
        corpus.a1,
        corpus.b0,
        corpus.a0,
    ]


async def test_context_budget_never_exceeded(db: Database) -> None:
    """SPEC §9.1: o orçamento de contexto não é excedido; evidências que não
    couber no orçamento restante são descartadas."""
    await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    # orçamento estricto: só o primeiro candidato (a1) couber.
    policy = _context_policy(
        max_evidences=8, max_context_chars=len(_A1_TEXT) + 1, parent_expansion_chars=0
    )
    packed = await _assemble(db, plan=plan, policy=policy)

    assert packed.context_budget_chars == len(_A1_TEXT) + 1
    assert packed.total_chars <= packed.context_budget_chars
    assert [item.evidence.text for item in packed.evidences] == [_A1_TEXT]
    assert context_total_chars(packed.evidences) == packed.total_chars


async def test_diversity_preserved_for_comparative(db: Database) -> None:
    """SPEC §8.6/AC-11: pergunta comparativa seleciona de ambas edições com
    limite flexível; nunca preenche com obra menos relevante (ficam abaixo de
    max_evidences)."""
    corpus = await _seed(db)
    plan = _plan(needs_diversity=True, intent=Intent.COMPARATIVE)
    policy = _context_policy(max_evidences=3, per_edition_limit=1, parent_expansion_chars=0)
    packed = await _assemble(db, plan=plan, policy=policy)

    editions = {item.evidence.edition_id for item in packed.evidences}
    assert editions == {corpus.edition_a, corpus.edition_b}
    # a1 (A) e b0 (B); a0 fica fora por limite por edição — nada se preenche.
    assert [item.evidence.passage_id for item in packed.evidences] == [corpus.a1, corpus.b0]
    assert len(packed.evidences) < 3  # max_evidences não é preenchido com menos relevante


async def test_factual_selects_top_relevance_without_per_edition_cap(
    db: Database,
) -> None:
    """SPEC §8.6: factual maximiza relevância — sem limite por edição."""
    corpus = await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    policy = _context_policy(max_evidences=8, per_edition_limit=1)
    packed = await _assemble(db, plan=plan, policy=policy)

    assert [item.evidence.passage_id for item in packed.evidences] == [
        corpus.a1,
        corpus.a0,
        corpus.b0,
    ]


async def test_parent_expansion_in_context_never_citable(db: Database) -> None:
    """SPEC §7.3/AC-12: o texto parental expande o contexto montado, mas nunca
    é citação final."""
    corpus = await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    packed = await _assemble(db, plan=plan, policy=ContextPolicy.defaults())

    # a1 tem pai; o texto parental aparece como contexto, não como evidência.
    item = next(item for item in packed.evidences if item.evidence.passage_id == corpus.a1)
    assert item.parent_text == _PARENT_A1_TEXT
    assert item.parent_passage_id == corpus.parent_a1

    quote = await _quote(db, plan=plan, policy=ContextPolicy.defaults())
    seed_texts = {_A1_TEXT, _A0_EXCERPT, _B0_TEXT}
    # o texto parental NUNCA aparece como citação literal.
    assert all(ref.text in seed_texts for ref in quote.evidences)
    assert _PARENT_A1_TEXT not in {ref.text for ref in quote.evidences}


async def test_context_policy_version_is_registered(db: Database) -> None:
    """AC-15: a política de contexto fica registrada como versão imutável;
    a mesma política devolve a MESMA versão (idempotente)."""
    await _seed(db)
    plan = _plan(needs_diversity=False, intent=Intent.FACTUAL)
    first = await _assemble(db, plan=plan, policy=ContextPolicy.defaults())
    second = await _assemble(db, plan=plan, policy=ContextPolicy.defaults())

    assert first.policy_version_id is not None
    assert second.policy_version_id == first.policy_version_id
    async with db.connection() as conn:
        version = await VersionsRepository(conn).get(ContextPolicyVersion, first.policy_version_id)
    assert version is not None
    assert len(version.params["budgets"]) == 3
