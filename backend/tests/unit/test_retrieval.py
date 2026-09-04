"""Recuperação vetorial/RRF/orçamento: casos determinísticos puros (T09).

Cobre: cálculo RRF validado por casos determinísticos (AC-06), invariantes
do orçamento por profundidade, e persistência das quatro estágios em
`AnswerRun.candidates` (append-only).
"""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from rag.domain.enums import Depth, QueryStatus, RankingStage, SearchStrategy
from rag.domain.errors import InvalidTransitionError
from rag.domain.query import EditionFilter
from rag.domain.retrieval import (
    RetrievalBudget,
    RetrievalPolicy,
    RetrievalResult,
    fuse_rankings,
)
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import utcnow


def _candidate(passage_id: UUID, stage: RankingStage, score: float, rank: int) -> RankedCandidate:
    return RankedCandidate(passage_id=passage_id, stage=stage, score=score, rank=rank)


class TestFuseRankings:
    """RRF determinístico: contribuição 1/(k + rank + 1), rank 0-based."""

    def test_single_list_contribution(self) -> None:
        a, b = uuid4(), uuid4()
        fused = fuse_rankings(
            [
                [
                    _candidate(a, RankingStage.LEXICAL, 12.0, 0),
                    _candidate(b, RankingStage.LEXICAL, 5.0, 1),
                ]
            ],
            k=60.0,
        )
        assert len(fused) == 2
        assert fused[0].passage_id == a
        assert fused[1].passage_id == b
        assert fused[0].score == pytest.approx(1.0 / 61.0)
        assert fused[1].score == pytest.approx(1.0 / 62.0)

    def test_contributions_sum_across_lists(self) -> None:
        """A passagem presente nas duas listas soma as duas contribuições."""
        a = uuid4()
        fused = fuse_rankings(
            [
                [_candidate(a, RankingStage.LEXICAL, 12.0, 0)],
                [_candidate(a, RankingStage.VECTOR, 0.9, 3)],
            ],
            k=10.0,
        )
        assert len(fused) == 1
        assert fused[0].passage_id == a
        assert fused[0].score == pytest.approx(1.0 / 11.0 + 1.0 / 14.0)

    def test_passage_in_only_one_list_keeps_single_contribution(self) -> None:
        a, b = uuid4(), uuid4()
        fused = fuse_rankings(
            [
                [
                    _candidate(a, RankingStage.LEXICAL, 12.0, 0),
                    _candidate(b, RankingStage.LEXICAL, 5.0, 1),
                ],
                [_candidate(a, RankingStage.VECTOR, 0.9, 0)],
            ],
            k=60.0,
        )
        by_id = {c.passage_id: c for c in fused}
        assert by_id[a].score == pytest.approx(1.0 / 61.0 + 1.0 / 61.0)
        assert by_id[b].score == pytest.approx(1.0 / 62.0)
        assert fused[0].passage_id == a  # maior contribuição fica em primeiro

    def test_rank_is_position_after_sorting(self) -> None:
        a, b, c = uuid4(), uuid4(), uuid4()
        fused = fuse_rankings(
            [
                [
                    _candidate(a, RankingStage.LEXICAL, 1.0, 0),
                    _candidate(b, RankingStage.LEXICAL, 1.0, 1),
                    _candidate(c, RankingStage.LEXICAL, 1.0, 2),
                ],
                [
                    _candidate(c, RankingStage.VECTOR, 1.0, 0),
                    _candidate(b, RankingStage.VECTOR, 1.0, 1),
                    _candidate(a, RankingStage.VECTOR, 1.0, 2),
                ],
            ],
            k=1.0,
        )
        assert [c.rank for c in fused] == [0, 1, 2]
        assert {c.stage for c in fused} == {RankingStage.FUSED}

    def test_ties_broken_deterministically_by_passage_id(self) -> None:
        """Duas passagens com a mesma contribuição não dependem da ordem de
        inserção: o desempate é por `passage_id` ascendente."""
        a, b = uuid4(), uuid4()
        if a > b:
            a, b = b, a
        fused = fuse_rankings(
            [
                [
                    _candidate(a, RankingStage.LEXICAL, 1.0, 0),
                    _candidate(b, RankingStage.LEXICAL, 1.0, 1),
                ],
                [
                    _candidate(b, RankingStage.VECTOR, 1.0, 2),
                    _candidate(a, RankingStage.VECTOR, 1.0, 3),
                ],
            ],
            k=60.0,
        )
        assert fused[0].passage_id == a
        assert fused[1].passage_id == b

    def test_empty_lists_return_empty(self) -> None:
        assert fuse_rankings([], k=60.0) == ()
        assert fuse_rankings([[], []], k=60.0) == ()

    def test_negative_k_rejected(self) -> None:
        with pytest.raises(ValueError, match="RRF"):
            fuse_rankings([], k=0.0)

    def test_input_lists_are_not_mutated(self) -> None:
        a = uuid4()
        first = [_candidate(a, RankingStage.LEXICAL, 1.0, 0)]
        fuse_rankings([first], k=60.0)
        assert len(first) == 1


class TestRetrievalBudgetAndPolicy:
    def test_defaults_cover_all_depths_and_are_conservative(self) -> None:
        policy = RetrievalPolicy.defaults()
        for depth in Depth:
            budget = policy.budget_for(depth)
            assert budget.depth is depth
            assert budget.lexical_top_k >= 1
            assert budget.vector_top_k >= 1
            assert budget.rrf_k >= 1.0
            assert budget.rerank_top_n >= 1

    def test_budgets_grow_with_depth(self) -> None:
        policy = RetrievalPolicy.defaults()
        brief = policy.budget_for(Depth.BRIEF)
        standard = policy.budget_for(Depth.STANDARD)
        deep = policy.budget_for(Depth.DEEP)
        assert brief.lexical_top_k < standard.lexical_top_k < deep.lexical_top_k
        assert brief.vector_top_k < standard.vector_top_k < deep.vector_top_k
        assert brief.rerank_top_n < standard.rerank_top_n < deep.rerank_top_n

    def test_policy_requires_all_depths_exactly_once(self) -> None:
        with pytest.raises(ValidationError, match="três profundidades"):
            RetrievalPolicy(
                budgets=(
                    (
                        Depth.BRIEF,
                        RetrievalBudget(
                            depth=Depth.BRIEF,
                            lexical_top_k=10,
                            vector_top_k=10,
                            rrf_k=60.0,
                            rerank_top_n=5,
                        ),
                    ),
                )
            )
        with pytest.raises(ValidationError, match="uma única vez"):
            RetrievalPolicy(
                budgets=(
                    (
                        Depth.BRIEF,
                        RetrievalBudget(
                            depth=Depth.BRIEF,
                            lexical_top_k=10,
                            vector_top_k=10,
                            rrf_k=60.0,
                            rerank_top_n=5,
                        ),
                    ),
                    (
                        Depth.BRIEF,
                        RetrievalBudget(
                            depth=Depth.BRIEF,
                            lexical_top_k=11,
                            vector_top_k=11,
                            rrf_k=60.0,
                            rerank_top_n=6,
                        ),
                    ),
                    (
                        Depth.STANDARD,
                        RetrievalBudget(
                            depth=Depth.STANDARD,
                            lexical_top_k=20,
                            vector_top_k=20,
                            rrf_k=60.0,
                            rerank_top_n=10,
                        ),
                    ),
                    (
                        Depth.DEEP,
                        RetrievalBudget(
                            depth=Depth.DEEP,
                            lexical_top_k=30,
                            vector_top_k=30,
                            rrf_k=60.0,
                            rerank_top_n=15,
                        ),
                    ),
                )
            )

    def test_budget_for_unknown_depth_raises(self) -> None:
        """Rama defensiva: uma política malformada nunca devolve um orçamento
        errado silenciosamente. Contorna deliberadamente os validators
        (`model_construct`), que são o que garante a cobertura em produção."""
        partial = RetrievalPolicy.model_construct(
            budgets=(
                (
                    Depth.BRIEF,
                    RetrievalBudget(
                        depth=Depth.BRIEF,
                        lexical_top_k=10,
                        vector_top_k=10,
                        rrf_k=60.0,
                        rerank_top_n=5,
                    ),
                ),
            )
        )
        with pytest.raises(ValueError, match="ausente"):
            partial.budget_for(Depth.DEEP)

    def test_budget_invalid_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RetrievalBudget(
                depth=Depth.BRIEF,
                lexical_top_k=0,
                vector_top_k=5,
                rrf_k=60.0,
                rerank_top_n=3,
            )
        with pytest.raises(ValidationError):
            RetrievalBudget(
                depth=Depth.BRIEF,
                lexical_top_k=5,
                vector_top_k=5,
                rrf_k=0.5,
                rerank_top_n=3,
            )

    def test_policy_dumps_to_json_compatible_params(self) -> None:
        """`model_dump(mode="json")` da política é armazenável como params de
        `RetrievalPolicyVersion` (SPEC §6; AC-15)."""
        dumped = RetrievalPolicy.defaults().model_dump(mode="json")
        assert "budgets" in dumped
        assert len(dumped["budgets"]) == 3

    def test_frozen(self) -> None:
        policy = RetrievalPolicy.defaults()
        with pytest.raises(ValidationError):
            policy.budgets = ()


class TestRetrievalResult:
    def test_answer_run_candidates_preserves_all_stages(self) -> None:
        """AC-06: a tuple a persistir mantém lexical, vetorial, RRF e
        reranking distintos e rastreáveis."""
        a, b = uuid4(), uuid4()
        result = RetrievalResult(
            lexical=(_candidate(a, RankingStage.LEXICAL, 12.0, 0),),
            vector=(_candidate(a, RankingStage.VECTOR, 0.91, 0),),
            fused=(_candidate(a, RankingStage.FUSED, 0.033, 0),),
            reranked=(_candidate(b, RankingStage.RERANKED, 0.87, 0),),
            policy_version_id=uuid4(),
        )
        candidates = result.answer_run_candidates()
        assert {c.stage for c in candidates} == set(RankingStage)
        assert len(candidates) == 4

    def test_candidates_persist_into_answer_run_append_only(self) -> None:
        """AC-06: as transições de `AnswerRun.candidates` aceitam o resultado
        da recuperação e continuam a aplicar a regra append-only."""
        a, b = uuid4(), uuid4()
        result = RetrievalResult(
            lexical=(_candidate(a, RankingStage.LEXICAL, 1.0, 0),),
            vector=(_candidate(b, RankingStage.VECTOR, 0.9, 0),),
            fused=(_candidate(a, RankingStage.FUSED, 0.03, 0),),
            reranked=(_candidate(b, RankingStage.RERANKED, 0.8, 0),),
        )
        run = AnswerRun(
            question_original="pergunta",
            question_anonymized="pergunta",
            explicit_filters=EditionFilter(),
            created_at=utcnow(),
        ).transition(QueryStatus.RUNNING, candidates=result.answer_run_candidates())
        assert len(run.candidates) == 4
        assert {c.stage for c in run.candidates} == set(RankingStage)
        with pytest.raises(InvalidTransitionError, match="append-only"):
            run.transition(QueryStatus.RUNNING, candidates=())  # append-only

    def test_empty_result(self) -> None:
        result = RetrievalResult()
        assert result.answer_run_candidates() == ()
        assert result.policy_version_id is None
        assert result.embedding_version_id is None
        assert result.run_id is None

    def test_result_retains_more_fused_than_reranked(self) -> None:
        """T9-04: todos os itens de `fused` são preservados em `answer_run_candidates`,
        mesmo quando apenas um subconjunto foi enviado ao reranker."""
        ids = [uuid4() for _ in range(5)]
        fused = tuple(
            _candidate(i, RankingStage.FUSED, 0.05 - idx * 0.01, idx) for idx, i in enumerate(ids)
        )
        reranked = tuple(
            _candidate(i, RankingStage.RERANKED, 0.9 - idx * 0.1, idx)
            for idx, i in enumerate(ids[:2])
        )
        result = RetrievalResult(
            fused=fused,
            reranked=reranked,
            policy_version_id=uuid4(),
            embedding_version_id=uuid4(),
        )
        assert len(result.fused) == 5
        assert len(result.reranked) == 2
        candidates = result.answer_run_candidates()
        assert len(candidates) == 7
        assert sum(1 for c in candidates if c.stage == RankingStage.FUSED) == 5
        assert sum(1 for c in candidates if c.stage == RankingStage.RERANKED) == 2

    def test_literal_final_candidates_uses_lexical_not_reranked(self) -> None:
        """R02 (B01): num caso `literal` o ranking final para a montagem de
        contexto é a lista lexical — não existe fusão nem reranking."""
        a, b = uuid4(), uuid4()
        lexical = (_candidate(a, RankingStage.LEXICAL, 12.0, 0),)
        result = RetrievalResult(
            lexical=lexical,
            vector=(),
            fused=(),
            reranked=(_candidate(b, RankingStage.RERANKED, 0.9, 0),),
            strategy=SearchStrategy.LITERAL,
        )
        assert result.final_candidates() == lexical

    def test_hybrid_final_candidates_uses_reranked(self) -> None:
        """R02: `hybrid`/`expanded` consumem o reranking como ranking final."""
        a, b = uuid4(), uuid4()
        reranked = (_candidate(b, RankingStage.RERANKED, 0.9, 0),)
        result = RetrievalResult(
            lexical=(_candidate(a, RankingStage.LEXICAL, 12.0, 0),),
            reranked=reranked,
            strategy=SearchStrategy.HYBRID,
        )
        assert result.final_candidates() == reranked

    def test_literal_answer_run_candidates_only_lexical_stage(self) -> None:
        """R02 (B01): os estágios persistidos correspondem à estratégia —
        `literal` grava somente o estágio lexical no `AnswerRun`."""
        a = uuid4()
        lexical = (_candidate(a, RankingStage.LEXICAL, 12.0, 0),)
        result = RetrievalResult(
            lexical=lexical,
            vector=(),
            fused=(),
            reranked=(),
            strategy=SearchStrategy.LITERAL,
        )
        candidates = result.answer_run_candidates()
        assert candidates == lexical
        assert {c.stage for c in candidates} == {RankingStage.LEXICAL}


class TestRetrievalServiceValidation:
    """R2-T9-01 e R2-T9-02: validações obrigatórias de run e embedding_version."""

    async def test_retrieval_service_rejects_missing_or_invalid_run(self) -> None:
        from unittest.mock import AsyncMock

        from rag.application.search import RetrievalService
        from rag.domain.query import LexicalQuery
        from rag.domain.versions import EmbeddingVersion

        class DummyEmbedding:
            @property
            def embedding_version(self) -> EmbeddingVersion:
                return EmbeddingVersion(
                    label="d", model_name="d", dimensions=1024, created_at=utcnow()
                )

            async def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return []

            async def embed_query(self, text: str) -> list[float]:
                return []

        class DummyReranker:
            async def rerank(self, query: str, documents: list[str]) -> list[float]:
                return []

        service = RetrievalService(DummyEmbedding(), DummyReranker())
        conn = AsyncMock()

        with pytest.raises(TypeError, match="AnswerRun"):
            await service.retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("termo",)),
                semantic_query="consulta",
                run=None,  # type: ignore[arg-type]
            )

        with pytest.raises(TypeError, match="AnswerRun"):
            await service.retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("termo",)),
                semantic_query="consulta",
                run="not-a-run",  # type: ignore[arg-type]
            )

    async def test_retrieval_service_rejects_provider_without_embedding_version(self) -> None:
        from unittest.mock import AsyncMock

        from rag.application.search import RetrievalService
        from rag.domain.query import LexicalQuery

        class NoVersionProvider:
            async def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return []

            async def embed_query(self, text: str) -> list[float]:
                return []

        class DummyReranker:
            async def rerank(self, query: str, documents: list[str]) -> list[float]:
                return []

        service = RetrievalService(NoVersionProvider(), DummyReranker())  # type: ignore[arg-type]
        conn = AsyncMock()
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
            created_at=utcnow(),
        )

        with pytest.raises(TypeError, match="embedding_version"):
            await service.retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("termo",)),
                semantic_query="consulta",
                run=run,
            )
