"""Estágio hierárquico: orçamento, auditoria e falha fechada (R04; SPEC §8.7,
AC-11, AC-12, AC-15).

Cobre, determinístico e sem rede:
- invariantes do orçamento hierárquico (`HierarchicalPolicy`/`HierarchicalBudget`);
- `HierarchicalHit` (nó → passagem) frozen e round-trip JSON;
- `RetrievalResult` preserva candidatos e auditoria do estágio hierárquico;
- persistência append-only de `AnswerRun.hierarchical_hits`;
- falha fechada: `needs_hierarchical` sem política hierárquica NUNCA executa.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.enums import Depth, HierarchicalSourceKind, QueryStatus, RankingStage
from rag.domain.errors import InvalidTransitionError
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.retrieval import (
    HierarchicalBudget,
    HierarchicalHit,
    HierarchicalPolicy,
    HierarchicalResult,
    RetrievalResult,
)
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import utcnow


class TestHierarchicalPolicy:
    def test_defaults_cover_all_depths(self) -> None:
        policy = HierarchicalPolicy.defaults()
        for depth in Depth:
            budget = policy.budget_for(depth)
            assert budget.depth is depth
            assert budget.max_summary_nodes >= 1
            assert budget.max_concept_nodes >= 1
            assert budget.max_passages_per_node >= 1
            assert budget.max_total_passages >= budget.max_passages_per_node

    def test_budgets_grow_with_depth(self) -> None:
        policy = HierarchicalPolicy.defaults()
        brief = policy.budget_for(Depth.BRIEF)
        standard = policy.budget_for(Depth.STANDARD)
        deep = policy.budget_for(Depth.DEEP)
        assert brief.max_summary_nodes < standard.max_summary_nodes < deep.max_summary_nodes
        assert brief.max_concept_nodes < standard.max_concept_nodes < deep.max_concept_nodes
        assert brief.max_total_passages < standard.max_total_passages < deep.max_total_passages

    def test_partial_policy_rejected(self) -> None:
        with pytest.raises(ValidationError, match="três profundidades"):
            HierarchicalPolicy(
                budgets=(
                    (
                        Depth.STANDARD,
                        HierarchicalBudget(
                            depth=Depth.STANDARD,
                            max_summary_nodes=5,
                            max_concept_nodes=5,
                            max_passages_per_node=3,
                            max_total_passages=15,
                        ),
                    ),
                )
            )

    def test_per_node_cannot_exceed_total(self) -> None:
        with pytest.raises(ValidationError, match="max_passages_per_node"):
            HierarchicalBudget(
                depth=Depth.STANDARD,
                max_summary_nodes=5,
                max_concept_nodes=5,
                max_passages_per_node=20,
                max_total_passages=10,
            )

    def test_budget_dumps_to_version_params(self) -> None:
        dumped = HierarchicalPolicy.defaults().model_dump(mode="json")
        assert "budgets" in dumped
        assert len(dumped["budgets"]) == 3


class TestHierarchicalHitAndResult:
    def test_hit_is_frozen(self) -> None:
        hit = HierarchicalHit(
            kind=HierarchicalSourceKind.SUMMARY,
            node_id=uuid4(),
            passage_id=uuid4(),
        )
        with pytest.raises(ValidationError):
            hit.passage_id = uuid4()

    def test_hit_roundtrip_json(self) -> None:
        hit = HierarchicalHit(
            kind=HierarchicalSourceKind.CONCEPT,
            node_id=uuid4(),
            passage_id=uuid4(),
        )
        assert HierarchicalHit.model_validate(hit.model_dump(mode="json")) == hit

    def test_hierarchical_result_roundtrip(self) -> None:
        hit = HierarchicalHit(
            kind=HierarchicalSourceKind.SUMMARY,
            node_id=uuid4(),
            passage_id=uuid4(),
        )
        candidate = RankedCandidate(
            passage_id=hit.passage_id,
            stage=RankingStage.HIERARCHICAL,
            score=0.05,
            rank=0,
        )
        result = HierarchicalResult(candidates=(candidate,), hits=(hit,))
        restored = HierarchicalResult.model_validate(result.model_dump(mode="json"))
        assert restored == result

    def test_retrieval_result_roundtrip_with_hierarchical(self) -> None:
        hit = HierarchicalHit(
            kind=HierarchicalSourceKind.CONCEPT,
            node_id=uuid4(),
            passage_id=uuid4(),
        )
        candidate = RankedCandidate(
            passage_id=hit.passage_id,
            stage=RankingStage.HIERARCHICAL,
            score=0.05,
            rank=0,
        )
        result = RetrievalResult(hierarchical=(candidate,), hierarchical_hits=(hit,))
        restored = RetrievalResult.model_validate(result.model_dump(mode="json"))
        assert restored.hierarchical == result.hierarchical
        assert restored.hierarchical_hits == result.hierarchical_hits

    def test_answer_run_candidates_includes_hierarchical(self) -> None:
        hit = HierarchicalHit(
            kind=HierarchicalSourceKind.SUMMARY,
            node_id=uuid4(),
            passage_id=uuid4(),
        )
        lexical = RankedCandidate(passage_id=uuid4(), stage=RankingStage.LEXICAL, score=1.0, rank=0)
        hier = RankedCandidate(
            passage_id=hit.passage_id,
            stage=RankingStage.HIERARCHICAL,
            score=0.05,
            rank=0,
        )
        result = RetrievalResult(lexical=(lexical,), hierarchical=(hier,))
        stages = {c.stage for c in result.answer_run_candidates()}
        assert RankingStage.HIERARCHICAL in stages


class TestAnswerRunHierarchicalHits:
    def test_hierarchical_hits_append_only(self) -> None:
        hit = HierarchicalHit(
            kind=HierarchicalSourceKind.SUMMARY,
            node_id=uuid4(),
            passage_id=uuid4(),
        )
        first = (hit,)
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
            created_at=utcnow(),
        )
        updated = run.transition(QueryStatus.RUNNING, candidates=(), hierarchical_hits=first)
        assert updated.hierarchical_hits == first
        with pytest.raises(InvalidTransitionError, match="append-only"):
            updated.transition(QueryStatus.RUNNING, hierarchical_hits=())


class TestRetrievalServiceHierarchicalGuards:
    async def test_needs_hierarchical_without_policy_fails_closed(self) -> None:
        from unittest.mock import AsyncMock

        from rag.application.search import RetrievalService
        from rag.domain.enums import Intent, SearchStrategy

        class DummyEmbedding:
            @property
            def embedding_version(self):
                from rag.domain.versions import EmbeddingVersion

                return EmbeddingVersion(
                    label="dummy",
                    model_name="dummy",
                    dimensions=1024,
                    created_at=utcnow(),
                )

            async def embed_query(self, text: str) -> list[float]:
                return []

        class DummyReranker:
            async def rerank(self, query: str, documents: list[str]) -> list[float]:
                return []

        plan = QueryPlan(
            intent=Intent.CONCEPTUAL,
            lexical_query=LexicalQuery(required_terms=("spleen",)),
            semantic_query="O que é spleen?",
            strategy=SearchStrategy.HYBRID,
            strategy_explanation=StrategyExplanation(
                requested=SearchStrategy.HYBRID,
                chosen=SearchStrategy.HYBRID,
                intent_signals=(),
                rationale="Estratégia híbrida em teste.",
            ),
            needs_hierarchical=True,
        )
        service = RetrievalService(DummyEmbedding(), DummyReranker())  # type: ignore[arg-type]
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
            status=QueryStatus.RUNNING,
            created_at=utcnow(),
        )
        with pytest.raises(TypeError, match="política hierárquica"):
            await service.retrieve(
                AsyncMock(),
                lexical_query=LexicalQuery(required_terms=("spleen",)),
                semantic_query="O que é spleen?",
                plan=plan,
                run=run,
            )
