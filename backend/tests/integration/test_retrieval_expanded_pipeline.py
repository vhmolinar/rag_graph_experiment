"""Estratégia `expanded`: aliases e subperguntas alteram candidatos, com
deduplicação, orçamento total, filtros e rastreabilidade (R03; SPEC §8.3,
B02, AC-05/AC-11/AC-15).

Cobre os testes obrigatórios da falha B02:
- uma evidência recuperável apenas por alias aparece em `expanded`, mas não em
  `hybrid`;
- uma evidência recuperável apenas por subpergunta aparece no resultado;
- a mesma passagem recuperada por várias expansões é deduplicada;
- obra excluída não aparece em nenhuma expansão;
- falha de uma expansão segue política explícita (não vira sucesso silencioso);
- o orçamento total não é multiplicado sem limite pelo número de subperguntas;
- as consultas, scores e posições por expansão ficam persistidos em
  `AnswerRun.expansions` e a política em `ExpansionPolicyVersion` (AC-15).
"""

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from psycopg import AsyncConnection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import ConceptEmbeddingProvider, FakeRerankerProvider

from rag.application.search import RetrievalService
from rag.domain.enums import (
    Depth,
    ExpansionKind,
    Intent,
    SearchStrategy,
    SourceType,
)
from rag.domain.errors import ModelTimeoutError
from rag.domain.library import Edition, Passage, Work
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.retrieval import (
    ExpansionBudget,
    ExpansionPolicy,
    RetrievalBudget,
    RetrievalPolicy,
)
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import (
    ChunkingVersion,
    ExpansionPolicyVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_MAIN_QUERY = "O que é o spleen?"
_SUBQUESTION = "O que é o ciúme?"
_ALIAS = "recordação"


@dataclass
class Corpus:
    work_a: UUID
    edition_a: UUID
    a: UUID
    b: UUID
    c: UUID
    d: UUID

    @property
    def texts(self) -> dict[UUID, str]:
        return {
            self.a: "O spleen sufoca a liberdade de Bentinho.",
            self.b: "O destino mora nos antigos.",
            # C: só recuperável via o alias "recordação" (memória).
            self.c: "A recordação lembra a memória.",
            # D: só recuperável via a subpergunta sobre o ciúme (inveja).
            self.d: "O ciúme mora na desconfiança.",
        }


async def _seed(db: Database) -> Corpus:
    async with db.connection() as conn:
        work_a = await WorksRepository(conn).create(Work(canonical_title="Dom Casmurro"))
        edition_a = await EditionsRepository(conn).create(
            Edition(
                work_id=work_a.id,
                title="Dom Casmurro",
                source_type=SourceType.PDF_TEXT,
                source_sha256="a" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-exp", created_at=utcnow())
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
        b = await child(edition_a.id, 1, "O destino mora nos antigos.")
        c = await child(edition_a.id, 2, "A recordação lembra a memória.")
        d = await child(edition_a.id, 3, "O ciúme mora na desconfiança.")
        await conn.execute("ANALYZE passages")

    return Corpus(work_a=work_a.id, edition_a=edition_a.id, a=a, b=b, c=c, d=d)


async def _seed_excluded_work(db: Database) -> tuple[UUID, UUID]:
    """Passagem duma obra que combina spleen e recordação — candidata para o
    alias e a principal, mas que deve ficar FORA de todas as expansões quando
    a obra for excluída."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra Excluída da Expansão"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Edição Excluída",
                source_type=SourceType.PDF_TEXT,
                source_sha256="e" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-excl", created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        embedding_version = await versions.get_or_create(provider.embedding_version)
        passage = Passage(
            edition_id=edition.id,
            ordinal=0,
            text="A recordação também vive no spleen da obra excluída.",
            token_count=8,
            chunking_version_id=chunking.id,
            embedding_version_id=embedding_version.id,
        )
        created = await PassagesRepository(conn).create(
            passage, embedding=await provider.embed_query(passage.text)
        )
        await conn.execute("ANALYZE passages")
        return work.id, created.id


def _expanded_plan() -> QueryPlan:
    return QueryPlan(
        intent=Intent.CONCEPTUAL,
        lexical_query=LexicalQuery(required_terms=("spleen",)),
        semantic_query=_MAIN_QUERY,
        strategy=SearchStrategy.EXPANDED,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.EXPANDED,
            chosen=SearchStrategy.EXPANDED,
            intent_signals=(),
            rationale="Estratégia expanded em teste.",
        ),
        subquestions=(_SUBQUESTION,),
        aliases=(_ALIAS,),
    )


def _hybrid_policy() -> RetrievalPolicy:
    """`rerank_top_n=2`: A e B dominam a fusão híbrida; C e D ficam fora."""
    budgets = []
    for depth in Depth:
        budgets.append(
            (
                depth,
                RetrievalBudget(
                    depth=depth,
                    lexical_top_k=30,
                    vector_top_k=30,
                    rrf_k=60.0,
                    rerank_top_n=2,
                ),
            )
        )
    return RetrievalPolicy(budgets=tuple(budgets))


def _expanded_policy() -> ExpansionPolicy:
    """`rerank_top_n=3`: C (alias) e D (subpergunta) entram nos finais."""
    budgets = []
    for depth in Depth:
        budgets.append(
            (
                depth,
                ExpansionBudget(
                    depth=depth,
                    max_expansions=6,
                    lexical_top_k=30,
                    vector_top_k=30,
                    rrf_k=60.0,
                    fused_top_k=4,
                    rerank_top_n=3,
                ),
            )
        )
    return ExpansionPolicy(budgets=tuple(budgets))


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


async def test_alias_and_subquestion_alter_candidates_in_expanded_but_not_hybrid(
    db: Database,
) -> None:
    """B02/R03: C (só alias) e D (só subpergunta) entram nos finais de
    `expanded`; em `hybrid` ficam fora dos finais."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        hybrid_run = await _create_run(conn)
        hybrid = await RetrievalService(
            ConceptEmbeddingProvider(), FakeRerankerProvider()
        ).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=_hybrid_policy(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.HYBRID,
            run=hybrid_run,
        )
        expanded_run = await _create_run(conn)
        expanded = await RetrievalService(
            ConceptEmbeddingProvider(), FakeRerankerProvider()
        ).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=_expanded_plan(),
            expansion_policy=_expanded_policy(),
            run=expanded_run,
        )

    assert corpus.c not in _ids(hybrid.reranked)
    assert corpus.d not in _ids(hybrid.reranked)
    assert set(_ids(expanded.reranked)) == {corpus.a, corpus.c, corpus.d}

    # Origem de cada candidato preservada (rastreabilidade por expansão).
    kinds = {e.expansion.kind for e in expanded.expansions}
    assert kinds == {
        ExpansionKind.PRIMARY,
        ExpansionKind.SUBQUESTION,
        ExpansionKind.ALIAS,
    }
    alias_result = next(e for e in expanded.expansions if e.expansion.kind is ExpansionKind.ALIAS)
    subq_result = next(
        e for e in expanded.expansions if e.expansion.kind is ExpansionKind.SUBQUESTION
    )
    assert corpus.c in set(_ids(alias_result.lexical)) | set(_ids(alias_result.vector))
    assert corpus.d in set(_ids(subq_result.lexical)) | set(_ids(subq_result.vector))


async def test_passage_recovered_by_multiple_expansions_is_deduplicated(db: Database) -> None:
    """B02/R03: A é recuperada pela principal, pelo alias e pela subpergunta —
    na fusão RRF aparece UMA única vez (contribuições somam)."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await RetrievalService(
            ConceptEmbeddingProvider(), FakeRerankerProvider()
        ).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=_expanded_plan(),
            expansion_policy=_expanded_policy(),
            run=run,
        )

    fused_ids = _ids(result.fused)
    assert len(fused_ids) == len(set(fused_ids)), "a fusão não pode duplicar passagens"
    assert fused_ids.count(corpus.a) == 1
    assert corpus.a in fused_ids


async def test_excluded_work_never_appears_in_any_expansion(db: Database) -> None:
    """AC-07/B02: obra excluída não aparece em NINGUMA expansão, nem na fusão
    nem no reranking."""
    await _seed(db)
    excluded_work, excluded_passage = await _seed_excluded_work(db)
    reranker = FakeRerankerProvider()
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await RetrievalService(ConceptEmbeddingProvider(), reranker).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=EditionFilter(exclude_work_ids=frozenset({excluded_work})),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=_expanded_plan(),
            expansion_policy=_expanded_policy(),
            run=run,
        )

    for exp_result in result.expansions:
        assert excluded_passage not in _ids(exp_result.lexical)
        assert excluded_passage not in _ids(exp_result.vector)

    all_final = set(_ids(result.fused)) | set(_ids(result.reranked))
    assert excluded_passage not in all_final

    # O texto da obra excluída nunca chega ao reranker.
    assert reranker.calls
    _, documents = reranker.calls[-1]
    assert "A recordação também vive no spleen da obra excluída." not in documents


async def test_expansion_failure_is_not_silent_success(db: Database) -> None:
    """B02/R03: falha da expansão do alias propagha fechada — nunca se devolve
    um subconjunto como sucesso."""
    await _seed(db)

    class FailingOnAliasEmbedding(ConceptEmbeddingProvider):
        """Falha no embed_query da 3ª chamada (principal, subpergunta, alias)."""

        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def embed_query(self, text: str) -> list[float]:
            self.calls += 1
            if self.calls == 3:
                raise ModelTimeoutError()
            return await super().embed_query(text)

    async with db.connection() as conn:
        run = await _create_run(conn)
        with pytest.raises(ModelTimeoutError):
            await RetrievalService(FailingOnAliasEmbedding(), FakeRerankerProvider()).retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("spleen",)),
                semantic_query=_MAIN_QUERY,
                filters=None,
                policy=RetrievalPolicy.defaults(),
                depth=Depth.STANDARD,
                strategy=SearchStrategy.EXPANDED,
                plan=_expanded_plan(),
                expansion_policy=_expanded_policy(),
                run=run,
            )


async def test_total_budget_not_multiplied_by_subquestions(db: Database) -> None:
    """B02/R03: o teto total da fusão (`fused_top_k`) limita os finais mesmo
    com MUITAS subperguntas — o orçamento não é multiplicado sem limite."""
    await _seed(db)
    plan = QueryPlan(
        intent=Intent.CONCEPTUAL,
        lexical_query=LexicalQuery(required_terms=("spleen",)),
        semantic_query=_MAIN_QUERY,
        strategy=SearchStrategy.EXPANDED,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.EXPANDED,
            chosen=SearchStrategy.EXPANDED,
            intent_signals=(),
            rationale="Estratégia expanded em teste.",
        ),
        subquestions=(
            _SUBQUESTION,
            "O que é a memória?",
            "O que é a liberdade?",
            "O que é a autonomia?",
            "O que é o destino?",
        ),
        aliases=(_ALIAS, "desconfiança", "inveja"),
    )
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await RetrievalService(
            ConceptEmbeddingProvider(), FakeRerankerProvider()
        ).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=plan,
            expansion_policy=_expanded_policy(),
            run=run,
        )

    budget = _expanded_policy().budget_for(Depth.STANDARD)
    assert len(result.expansions) <= budget.max_expansions
    assert len(result.fused) <= budget.fused_top_k
    assert len(result.reranked) <= budget.rerank_top_n


async def test_degenerate_expansion_never_produces_nan(db: Database) -> None:
    """B02/R03: uma expansão com embedding vazio (sem sinal semântico) nunca
    produz score NaN — o estágio vetorial é pulado, a lexical continua."""
    await _seed(db)
    plan = QueryPlan(
        intent=Intent.CONCEPTUAL,
        lexical_query=LexicalQuery(required_terms=("spleen",)),
        semantic_query=_MAIN_QUERY,
        strategy=SearchStrategy.EXPANDED,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.EXPANDED,
            chosen=SearchStrategy.EXPANDED,
            intent_signals=(),
            rationale="Estratégia expanded em teste.",
        ),
        subquestions=(),
        aliases=("zzz",),  # fora do mapa de conceitos -> embedding vazio
    )
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await RetrievalService(
            ConceptEmbeddingProvider(), FakeRerankerProvider()
        ).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=plan,
            expansion_policy=_expanded_policy(),
            run=run,
        )

    alias_result = next(e for e in result.expansions if e.expansion.kind is ExpansionKind.ALIAS)
    assert alias_result.vector == ()
    for candidate in (*result.fused, *result.reranked):
        assert math.isfinite(candidate.score)


async def test_expansions_and_policy_version_persisted(db: Database) -> None:
    """AC-15: as consultas/scores/posições por expansão ficam persistidas em
    `AnswerRun.expansions`; a política de expansão fica versionada."""
    await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await RetrievalService(
            ConceptEmbeddingProvider(), FakeRerankerProvider()
        ).retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query=_MAIN_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            strategy=SearchStrategy.EXPANDED,
            plan=_expanded_plan(),
            expansion_policy=_expanded_policy(),
            run=run,
        )
        reloaded = await AnswerRunsRepository(conn).get(run.id)
        assert result.policy_version_id is not None
        version = await VersionsRepository(conn).get(
            ExpansionPolicyVersion, result.policy_version_id
        )

    assert reloaded is not None
    assert reloaded.expansions == result.expansions
    assert len(reloaded.expansions) == 3
    assert reloaded.versions.expansion_policy_version_id == result.policy_version_id
    assert reloaded.versions.retrieval_policy_version_id is None
    assert version is not None
    assert len(version.params["budgets"]) == 3
