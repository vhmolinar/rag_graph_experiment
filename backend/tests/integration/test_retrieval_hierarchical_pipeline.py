"""Estágio hierárquico integrado contra PostgreSQL real (R04; SPEC §8.7,
B03, AC-11, AC-12, AC-15).

Cobre os testes obrigatórios da falha B03:
- `needs_hierarchical` governa um estágio real: sínteses/conceitos relevantes
  localizam passagens que a busca lexical/vetorial comum não recupera;
- só passagens viram candidatos/evidências — síntese/conceito nunca aparecem
  em `fused`/`reranked` nem em `EvidenceRef` (AC-12);
- obra excluída não aparece em NINGUM estágio do estágio hierárquico (AC-07);
- só suportes da execução de indexação/enriquecimento vigente são resolvidos;
- o estágio coordina com a estratégia `expanded` (R03);
- auditoria: `AnswerRun.hierarchical_hits` e `HierarchicalPolicyVersion`
  persistidos (AC-15).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from psycopg import AsyncConnection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import ConceptEmbeddingProvider, FakeRerankerProvider

from rag.application.context import ContextService
from rag.application.search import RetrievalService
from rag.domain.context import ContextPolicy
from rag.domain.enums import (
    Depth,
    HierarchicalSourceKind,
    Intent,
    QueryStatus,
    SearchStrategy,
    SourceType,
    SummaryScope,
)
from rag.domain.indexing import IndexRun
from rag.domain.knowledge import Concept, Summary
from rag.domain.library import Edition, Passage, Work
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.retrieval import (
    ExpansionPolicy,
    HierarchicalPolicy,
    RetrievalBudget,
    RetrievalPolicy,
)
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ExtractionVersion,
    HierarchicalPolicyVersion,
    ModelEndpointVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.enrichment import (
    ConceptsRepository,
    SummariesRepository,
)
from rag.infrastructure.repositories.index_runs import IndexRunsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_MAIN_QUERY = "destino"

_P1 = "O spleen sufoca a liberdade de Bentinho."
_P2 = "A recordação lembra a memória dos antigos."
_P3 = "O ciúme mora na desconfiança."

_SUMMARY_TEXT = "O capítulo resume o destino dos antigos e as memórias."


@dataclass
class Corpus:
    work_a: UUID
    edition_a: UUID
    p1: UUID
    p2: UUID
    p3: UUID
    summary_id: UUID
    concept_id: UUID

    @property
    def texts(self) -> dict[UUID, str]:
        return {self.p1: _P1, self.p2: _P2, self.p3: _P3}


async def _seeded_edition(
    db: Database, *, source_sha256: str, title: str, work_title: str
) -> tuple[UUID, UUID]:
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title=work_title))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title=title,
                source_type=SourceType.PDF_TEXT,
                source_sha256=source_sha256,
            )
        )
        return work.id, edition.id


async def _active_run(conn: AsyncConnection, edition_id: UUID) -> IndexRun:
    versions = VersionsRepository(conn)
    chunking = await versions.get_or_create(
        ChunkingVersion(label="chunk-hier", created_at=utcnow())
    )
    emb_ver = await versions.get_or_create(
        EmbeddingVersion(
            label="concept-embedding",
            model_name="concept-embedding",
            dimensions=1024,
            created_at=utcnow(),
        )
    )
    ext_ver = await versions.get_or_create(ExtractionVersion(label="ext-hier", created_at=utcnow()))
    model_ver = await versions.get_or_create(
        ModelEndpointVersion(
            label="end-hier",
            endpoint_kind="embedding",
            provider="test",
            model_name="concept-embedding",
            created_at=utcnow(),
        )
    )
    return await IndexRunsRepository(conn).create(
        IndexRun(
            edition_id=edition_id,
            extraction_version_id=ext_ver.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
            model_endpoint_version_id=model_ver.id,
            is_active=True,
            created_at=utcnow(),
        )
    )


async def _child(
    conn: AsyncConnection,
    edition_id: UUID,
    ordinal: int,
    text: str,
    index_run_id: UUID,
    *,
    chunking_version_id: UUID,
    embedding_version_id: UUID,
) -> UUID:
    passage = Passage(
        edition_id=edition_id,
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
        chunking_version_id=chunking_version_id,
        embedding_version_id=embedding_version_id,
        index_run_id=index_run_id,
    )
    provider = ConceptEmbeddingProvider()
    created = await PassagesRepository(conn).create(
        passage, embedding=await provider.embed_query(text)
    )
    return created.id


async def _model_endpoint(conn: AsyncConnection, *, label: str) -> ModelEndpointVersion:
    return await VersionsRepository(conn).get_or_create(
        ModelEndpointVersion(
            label=label,
            endpoint_kind="generator",
            provider="test",
            model_name="fake-model",
            created_at=utcnow(),
        )
    )


async def _seed(db: Database) -> Corpus:
    async with db.connection() as conn:
        work_id, edition_id = await _seeded_edition(
            db, source_sha256="a" * 64, title="Dom Casmurro", work_title="Dom Casmurro"
        )
        run = await _active_run(conn, edition_id)
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-hier-a", created_at=utcnow())
        )
        emb_ver = await versions.get_or_create(
            EmbeddingVersion(
                label="concept-embedding",
                model_name="concept-embedding",
                dimensions=1024,
                created_at=utcnow(),
            )
        )
        p1 = await _child(
            conn,
            edition_id,
            0,
            _P1,
            run.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
        )
        p2 = await _child(
            conn,
            edition_id,
            1,
            _P2,
            run.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
        )
        p3 = await _child(
            conn,
            edition_id,
            2,
            _P3,
            run.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
        )
        await conn.execute("ANALYZE passages")

        gen = await _model_endpoint(conn, label="summarizer-hier")
        summary = Summary(
            edition_id=edition_id,
            scope_type=SummaryScope.EDITION,
            text=_SUMMARY_TEXT,
            generator_version_id=gen.id,
            supporting_passage_ids=(p2, p3),
        )
        await SummariesRepository(conn).create(summary)

        concept = await ConceptsRepository(conn).get_or_create(
            Concept(normalized_label="destino", description="Conceito de fixture.")
        )
        await ConceptsRepository(conn).add_alias(concept.id, "fado", confidence=1.0)
        await ConceptsRepository(conn).add_evidence(
            concept.id, p2, confidence=1.0, extractor_version_id=gen.id
        )

    return Corpus(
        work_a=work_id,
        edition_a=edition_id,
        p1=p1,
        p2=p2,
        p3=p3,
        summary_id=summary.id,
        concept_id=concept.id,
    )


def _hybrid_plan(*, needs_hierarchical: bool) -> QueryPlan:
    return QueryPlan(
        intent=Intent.CONCEPTUAL,
        lexical_query=LexicalQuery(required_terms=("sufoca",)),
        semantic_query=_MAIN_QUERY,
        strategy=SearchStrategy.HYBRID,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.HYBRID,
            chosen=SearchStrategy.HYBRID,
            intent_signals=(),
            rationale="Estratégia híbrida em teste.",
        ),
        needs_hierarchical=needs_hierarchical,
    )


def _expanded_plan() -> QueryPlan:
    return QueryPlan(
        intent=Intent.CONCEPTUAL,
        lexical_query=LexicalQuery(required_terms=("sufoca",)),
        semantic_query=_MAIN_QUERY,
        strategy=SearchStrategy.EXPANDED,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.EXPANDED,
            chosen=SearchStrategy.EXPANDED,
            intent_signals=(),
            rationale="Estratégia expanded em teste.",
        ),
        needs_hierarchical=True,
    )


def _service() -> RetrievalService:
    return RetrievalService(ConceptEmbeddingProvider(), FakeRerankerProvider())


def _strict_policy() -> RetrievalPolicy:
    """`vector_top_k=1` (só p1) e `rerank_top_n=2`: as passagens hierárquicas
    só entram nos finais quando o estágio é executado — sem o estágio, p2/p3
    ficam fora de `fused`/`reranked` (o corpus é pequeno e o estágio vetorial,
    com top_k alto, devolveria todas as passagens, cosseno 0 incluído)."""

    budgets = []
    for depth in Depth:
        budgets.append(
            (
                depth,
                RetrievalBudget(
                    depth=depth,
                    lexical_top_k=30,
                    vector_top_k=1,
                    rrf_k=60.0,
                    rerank_top_n=3,
                ),
            )
        )
    return RetrievalPolicy(budgets=tuple(budgets))


async def _create_run(conn: AsyncConnection) -> AnswerRun:
    return await AnswerRunsRepository(conn).create(
        AnswerRun(
            question_original=_MAIN_QUERY,
            question_anonymized=_MAIN_QUERY,
            explicit_filters=EditionFilter(),
            created_at=utcnow(),
        )
    )


def _ids(candidates: list[RankedCandidate] | tuple[RankedCandidate, ...]) -> list[UUID]:
    return [c.passage_id for c in candidates]


async def test_needs_hierarchical_governs_real_stage_and_localizes_passages(
    db: Database,
) -> None:
    """B03/R04: com `needs_hierarchical=True`, sínteses/conceitos relevantes
    localizam p2/p3 que a busca comum (lexical+vetorial) não recupera; sem o
    flag, o estágio não é executado e essas passagens não entram nos finais."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        without_run = await _create_run(conn)
        without = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=_strict_policy(),
            depth=Depth.STANDARD,
            plan=_hybrid_plan(needs_hierarchical=False),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=without_run,
        )
        assert without.hierarchical == ()
        assert without.hierarchical_hits == ()
        assert corpus.p2 not in _ids(without.fused)
        assert corpus.p3 not in _ids(without.fused)
        assert corpus.p2 not in _ids(without.reranked)
        assert corpus.p3 not in _ids(without.reranked)

        with_run = await _create_run(conn)
        with_hier = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=_strict_policy(),
            depth=Depth.STANDARD,
            plan=_hybrid_plan(needs_hierarchical=True),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=with_run,
        )

    hier_ids = set(_ids(with_hier.hierarchical))
    assert corpus.p2 in hier_ids
    assert corpus.p3 in hier_ids
    assert corpus.p2 in set(_ids(with_hier.fused))
    assert corpus.p3 in set(_ids(with_hier.fused))
    assert corpus.p2 in set(_ids(with_hier.reranked))
    assert corpus.p3 in set(_ids(with_hier.reranked))

    # Auditoria: cada passagem descendente fica ligada ao nó que a localizou.
    hits = with_hier.hierarchical_hits
    by_passage: dict[UUID, set[HierarchicalSourceKind]] = {}
    for hit in hits:
        assert hit.passage_id in hier_ids
        by_passage.setdefault(hit.passage_id, set()).add(hit.kind)
    assert HierarchicalSourceKind.SUMMARY in by_passage[corpus.p2]
    assert HierarchicalSourceKind.CONCEPT in by_passage[corpus.p2]
    assert HierarchicalSourceKind.SUMMARY in by_passage[corpus.p3]


async def test_only_passages_become_candidates_and_evidence(db: Database) -> None:
    """AC-12: só passagens viram candidatos/evidências — sínteses e conceitos
    NUNCA aparecem em `fused`/`reranked` nem em `EvidenceRef` do contexto."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            plan=_hybrid_plan(needs_hierarchical=True),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=run,
        )
        packed = await ContextService().assemble(
            conn,
            plan=_hybrid_plan(needs_hierarchical=True),
            retrieval=result,
            depth=Depth.STANDARD,
            policy=ContextPolicy.defaults(),
        )

    known_passage_ids = {corpus.p1, corpus.p2, corpus.p3}
    for candidate in (*result.fused, *result.reranked):
        assert candidate.passage_id in known_passage_ids
    assert packed.evidences, "o contexto deve ter evidências"
    for item in packed.evidences:
        assert item.evidence.passage_id in known_passage_ids
        assert item.evidence.text in (_P1, _P2, _P3)
        assert item.evidence.text != _SUMMARY_TEXT


async def test_excluded_work_never_appears_in_hierarchical_stage(db: Database) -> None:
    """AC-07: obra excluída não aparece em NINGUM estágio do estágio
    hierárquico — nem nos candidatos, nem na auditoria, nem nos finais.

    O conceito global "destino" tem evidência nas DUAS obras: a seleção de nós
    aplica os filtros ANTES (via as evidências) e a recuperação descendente
    DEPOIS (só devolve as passagens permitidas da execução vigente)."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        work_b, edition_b = await _seeded_edition(
            db, source_sha256="b" * 64, title="Obra Excluída", work_title="Obra Excluída"
        )
        run_b = await _active_run(conn, edition_b)
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-hier-b", created_at=utcnow())
        )
        emb_ver = await versions.get_or_create(
            EmbeddingVersion(
                label="concept-embedding",
                model_name="concept-embedding",
                dimensions=1024,
                created_at=utcnow(),
            )
        )
        p4 = await _child(
            conn,
            edition_b,
            0,
            "A recordação e o destino da obra excluída moram na memória.",
            run_b.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
        )
        await conn.execute("ANALYZE passages")
        gen = await _model_endpoint(conn, label="summarizer-excl")
        excluded_summary = Summary(
            edition_id=edition_b,
            scope_type=SummaryScope.EDITION,
            text="O destino da obra excluída.",
            generator_version_id=gen.id,
            supporting_passage_ids=(p4,),
        )
        await SummariesRepository(conn).create(excluded_summary)
        # Conceito global com evidência nas duas obras: a exclusão vale ANTES
        # e DEPOIS da seleção de nós (B03/R04).
        concepts_repo = ConceptsRepository(conn)
        shared_concept = await concepts_repo.get_or_create(
            Concept(normalized_label="destino", description="Conceito de fixture.")
        )
        assert shared_concept.id == corpus.concept_id
        await concepts_repo.add_evidence(
            shared_concept.id, p4, confidence=1.0, extractor_version_id=gen.id
        )

        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=EditionFilter(exclude_work_ids=frozenset({work_b})),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            plan=_hybrid_plan(needs_hierarchical=True),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=run,
        )

    # O estágio continua ativo (o conceito é selecionado via a edição permitida).
    assert corpus.concept_id in {h.node_id for h in result.hierarchical_hits}
    assert corpus.p2 in set(_ids(result.hierarchical))

    all_ids = set(_ids(result.hierarchical))
    all_ids |= set(_ids(result.fused)) | set(_ids(result.reranked))
    assert p4 not in all_ids
    assert all(hit.passage_id != p4 for hit in result.hierarchical_hits)
    assert all(hit.node_id != excluded_summary.id for hit in result.hierarchical_hits)


async def test_hierarchical_resolves_only_active_index_run_supports(db: Database) -> None:
    """B03/R04: a recuperação descendente só resolve suportes da execução de
    indexação/enriquecimento vigente — passagens de execuções inativas nunca
    são localizadas por síntese/conceito."""
    async with db.connection() as conn:
        _, edition_c = await _seeded_edition(
            db, source_sha256="c" * 64, title="Edição Reindexada", work_title="Obra Reindexada"
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-hier-c", created_at=utcnow())
        )
        emb_ver = await versions.get_or_create(
            EmbeddingVersion(
                label="concept-embedding",
                model_name="concept-embedding",
                dimensions=1024,
                created_at=utcnow(),
            )
        )
        ext_ver = await versions.get_or_create(
            ExtractionVersion(label="ext-hier-c", created_at=utcnow())
        )
        model_ver = await versions.get_or_create(
            ModelEndpointVersion(
                label="end-hier-c",
                endpoint_kind="embedding",
                provider="test",
                model_name="concept-embedding",
                created_at=utcnow(),
            )
        )
        runs_repo = IndexRunsRepository(conn)

        old_run = await runs_repo.create(
            IndexRun(
                edition_id=edition_c,
                extraction_version_id=ext_ver.id,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                model_endpoint_version_id=model_ver.id,
                is_active=False,
                created_at=utcnow(),
            )
        )
        p_old = await _child(
            conn,
            edition_c,
            0,
            "O destino obsoleto da execução inativa.",
            old_run.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
        )
        new_run = await runs_repo.create(
            IndexRun(
                edition_id=edition_c,
                extraction_version_id=ext_ver.id,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                model_endpoint_version_id=model_ver.id,
                is_active=True,
                created_at=utcnow(),
            )
        )
        p_new = await _child(
            conn,
            edition_c,
            0,
            "O destino ativo da execução vigente.",
            new_run.id,
            chunking_version_id=chunking.id,
            embedding_version_id=emb_ver.id,
        )
        await conn.execute("ANALYZE passages")

        gen = await _model_endpoint(conn, label="summarizer-reindex")
        summary = Summary(
            edition_id=edition_c,
            scope_type=SummaryScope.EDITION,
            text="O resumo fala do destino nas duas execuções.",
            generator_version_id=gen.id,
            supporting_passage_ids=(p_old, p_new),
        )
        await SummariesRepository(conn).create(summary)

        run = await _create_run(conn)
        plan = QueryPlan(
            intent=Intent.CONCEPTUAL,
            lexical_query=LexicalQuery(required_terms=("inexistente",)),
            semantic_query="destino",
            strategy=SearchStrategy.HYBRID,
            strategy_explanation=StrategyExplanation(
                requested=SearchStrategy.HYBRID,
                chosen=SearchStrategy.HYBRID,
                intent_signals=(),
                rationale="Estratégia híbrida em teste.",
            ),
            needs_hierarchical=True,
        )
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("inexistente",)),
            semantic_query="destino",
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            plan=plan,
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=run,
        )

    hier_ids = set(_ids(result.hierarchical))
    assert p_new in hier_ids
    assert p_old not in hier_ids
    assert p_old not in set(_ids(result.fused)) | set(_ids(result.reranked))


async def test_hierarchical_coordinates_with_expanded_strategy(db: Database) -> None:
    """R04 coordenado com R03: na estratégia `expanded`, os candidatos do
    estágio hierárquico entram na fusão/reranking junto com as expansões."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=_expanded_plan(),
            expansion_policy=ExpansionPolicy.defaults(),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=run,
        )

    hier_ids = set(_ids(result.hierarchical))
    assert corpus.p2 in hier_ids
    assert corpus.p2 in set(_ids(result.fused))
    assert corpus.p2 in set(_ids(result.reranked))
    assert result.strategy is SearchStrategy.EXPANDED


async def test_hierarchical_hits_and_policy_version_persisted(db: Database) -> None:
    """AC-15: a auditoria do estágio hierárquico fica persistida em
    `AnswerRun.hierarchical_hits`; a política fica versionada em
    `HierarchicalPolicyVersion` (idempotente)."""
    await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            plan=_hybrid_plan(needs_hierarchical=True),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=run,
        )
        reloaded = await AnswerRunsRepository(conn).get(run.id)
        assert reloaded is not None
        hier_version_id = reloaded.versions.hierarchical_policy_version_id
        assert hier_version_id is not None
        version = await VersionsRepository(conn).get(HierarchicalPolicyVersion, hier_version_id)

    assert reloaded.hierarchical_hits == result.hierarchical_hits
    assert len(reloaded.hierarchical_hits) >= 2
    assert version is not None
    assert len(version.params["budgets"]) == 3
    assert reloaded.status is QueryStatus.RUNNING


async def test_empty_corpus_hierarchical_returns_empty_and_retrieval_continues(
    db: Database,
) -> None:
    """Sem sínteses/conceitos no acervo, o estágio hierárquico devolve vazio e
    a recuperação comum continua sem falha."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        # Remove all summaries/concepts: o acervo fica sem índice hierárquico.
        await conn.execute(
            "TRUNCATE summary_supports, summaries, concept_evidence, "
            "concept_aliases, concepts CASCADE"
        )
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            plan=_hybrid_plan(needs_hierarchical=True),
            hierarchical_policy=HierarchicalPolicy.defaults(),
            run=run,
        )

    assert result.hierarchical == ()
    assert result.hierarchical_hits == ()
    assert corpus.p1 in set(_ids(result.reranked))
