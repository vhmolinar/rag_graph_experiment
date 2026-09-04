"""Estratégia `expanded`: orçamento total, deduplicação e rastreabilidade
(R03; SPEC §8.3, B02, AC-05/AC-11/AC-15).

Cobre, determinístico e sem rede:
- invariantes do orçamento de expansão (`ExpansionPolicy`/`ExpansionBudget`);
- `ExpansionExecutor.build_expansions`: valida e deduplica consulta principal,
  subperguntas e aliases, aplicando o teto total (`max_expansions`);
- persistência das expansões em `AnswerRun` (append-only) e em
  `RetrievalResult.expansions`;
- falha fechada: `expanded` sem plano ou sem política NUNCA executa.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.application.expansion import ExpansionExecutor
from rag.domain.enums import Depth, ExpansionKind, Intent, QueryStatus, RankingStage, SearchStrategy
from rag.domain.errors import InvalidTransitionError
from rag.domain.query import EditionFilter, LexicalQuery, QueryPlan, StrategyExplanation
from rag.domain.retrieval import (
    ExpansionBudget,
    ExpansionPolicy,
    ExpansionResult,
    RetrievalResult,
    fuse_rankings,
)
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import utcnow

_STANDARD_BUDGET = ExpansionPolicy.defaults().budget_for(Depth.STANDARD)


def _plan(
    *,
    semantic_query: str = "O que é spleen?",
    subquestions: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> QueryPlan:
    return QueryPlan(
        intent=Intent.CONCEPTUAL,
        lexical_query=LexicalQuery(required_terms=("spleen",)),
        semantic_query=semantic_query,
        strategy=SearchStrategy.EXPANDED,
        strategy_explanation=StrategyExplanation(
            requested=SearchStrategy.EXPANDED,
            chosen=SearchStrategy.EXPANDED,
            intent_signals=(),
            rationale="Estratégia expanded em teste.",
        ),
        subquestions=subquestions,
        aliases=aliases,
    )


class TestExpansionPolicy:
    def test_defaults_cover_all_depths(self) -> None:
        policy = ExpansionPolicy.defaults()
        for depth in Depth:
            budget = policy.budget_for(depth)
            assert budget.depth is depth
            assert budget.max_expansions >= 1
            assert budget.lexical_top_k >= 1
            assert budget.vector_top_k >= 1
            assert budget.rrf_k >= 1.0
            assert budget.fused_top_k >= budget.rerank_top_n

    def test_budgets_grow_with_depth(self) -> None:
        policy = ExpansionPolicy.defaults()
        brief = policy.budget_for(Depth.BRIEF)
        standard = policy.budget_for(Depth.STANDARD)
        deep = policy.budget_for(Depth.DEEP)
        assert brief.max_expansions < standard.max_expansions < deep.max_expansions
        assert brief.fused_top_k < standard.fused_top_k < deep.fused_top_k

    def test_partial_policy_rejected(self) -> None:
        with pytest.raises(ValidationError, match="três profundidades"):
            ExpansionPolicy(
                budgets=(
                    (
                        Depth.STANDARD,
                        ExpansionBudget(
                            depth=Depth.STANDARD,
                            max_expansions=4,
                            lexical_top_k=10,
                            vector_top_k=10,
                            rrf_k=60.0,
                            fused_top_k=20,
                            rerank_top_n=10,
                        ),
                    ),
                )
            )

    def test_rerank_top_n_cannot_exceed_fused_total(self) -> None:
        with pytest.raises(ValidationError, match="rerank_top_n"):
            ExpansionPolicy(
                budgets=(
                    (
                        Depth.BRIEF,
                        ExpansionBudget(
                            depth=Depth.BRIEF,
                            max_expansions=4,
                            lexical_top_k=10,
                            vector_top_k=10,
                            rrf_k=60.0,
                            fused_top_k=5,
                            rerank_top_n=10,
                        ),
                    ),
                    (
                        Depth.STANDARD,
                        ExpansionBudget(
                            depth=Depth.STANDARD,
                            max_expansions=4,
                            lexical_top_k=10,
                            vector_top_k=10,
                            rrf_k=60.0,
                            fused_top_k=20,
                            rerank_top_n=10,
                        ),
                    ),
                    (
                        Depth.DEEP,
                        ExpansionBudget(
                            depth=Depth.DEEP,
                            max_expansions=4,
                            lexical_top_k=10,
                            vector_top_k=10,
                            rrf_k=60.0,
                            fused_top_k=20,
                            rerank_top_n=10,
                        ),
                    ),
                )
            )

    def test_budget_dumps_to_version_params(self) -> None:
        dumped = ExpansionPolicy.defaults().model_dump(mode="json")
        assert "budgets" in dumped
        assert len(dumped["budgets"]) == 3


class TestBuildExpansions:
    def test_primary_subquestions_and_aliases_all_present(self) -> None:
        """B02/R03: subperguntas e aliases alteram os candidatos — cada uma vira
        uma expansão com origem preservada."""
        plan = _plan(
            subquestions=("Qual é a memória?", "O que é o ciúme?"),
            aliases=("spleen", "baço"),
        )
        expansions = ExpansionExecutor.build_expansions(plan, _STANDARD_BUDGET)

        assert [e.kind for e in expansions] == [
            ExpansionKind.PRIMARY,
            ExpansionKind.SUBQUESTION,
            ExpansionKind.SUBQUESTION,
            ExpansionKind.ALIAS,
            ExpansionKind.ALIAS,
        ]
        assert expansions[0].semantic_query == "O que é spleen?"
        assert expansions[0].lexical_query.required_terms == ("spleen",)
        assert expansions[1].semantic_query == "Qual é a memória?"
        assert expansions[3].semantic_query == "spleen"

    def test_duplicates_dropped_by_normalized_text(self) -> None:
        """A mesma consulta em varias origens nunca é executada duas vezes."""
        plan = _plan(
            semantic_query="O que é spleen?",
            subquestions=("o que é spleen?", "qual é a memória?"),
            aliases=("spleen", "SPLEEN", "baço"),
        )
        expansions = ExpansionExecutor.build_expansions(plan, _STANDARD_BUDGET)
        texts = [e.semantic_query for e in expansions]
        assert len(texts) == len(set(texts))
        assert [e.kind for e in expansions] == [
            ExpansionKind.PRIMARY,
            ExpansionKind.SUBQUESTION,
            ExpansionKind.ALIAS,
            ExpansionKind.ALIAS,
        ]

    def test_blank_expansions_skipped(self) -> None:
        plan = _plan(subquestions=("   ", ""), aliases=("", "spleen"))
        expansions = ExpansionExecutor.build_expansions(plan, _STANDARD_BUDGET)
        assert [e.kind for e in expansions] == [
            ExpansionKind.PRIMARY,
            ExpansionKind.ALIAS,
        ]

    def test_max_expansions_budget_caps_total(self) -> None:
        """O orçamento total limita o número de expansões — NUNCA é
        multiplicado sem limite pelo número de subperguntas/aliases."""
        budget = ExpansionPolicy.defaults().budget_for(Depth.BRIEF)  # max_expansions=4
        plan = _plan(
            subquestions=("s1", "s2", "s3", "s4"),
            aliases=("a1", "a2", "a3"),
        )
        expansions = ExpansionExecutor.build_expansions(plan, budget)
        assert len(expansions) == budget.max_expansions == 4
        assert expansions[0].kind is ExpansionKind.PRIMARY

    def test_alias_lexical_query_uses_alias_terms(self) -> None:
        """B02/R03: os aliases alteram a consulta lexical — o termo do alias
        entra nos required_terms da expansão."""
        plan = _plan(aliases=("baço",))
        expansions = ExpansionExecutor.build_expansions(plan, _STANDARD_BUDGET)
        assert expansions[1].kind is ExpansionKind.ALIAS
        assert "baço" in expansions[1].lexical_query.required_terms


class TestExpansionResultAndRetrievalResult:
    def test_expansion_result_is_frozen(self) -> None:
        result = ExpansionResult(
            expansion=ExpansionExecutor.build_expansions(_plan(), _STANDARD_BUDGET)[0],
            lexical=(
                RankedCandidate(
                    passage_id=uuid4(),
                    stage=RankingStage.LEXICAL,
                    score=1.0,
                    rank=0,
                ),
            ),
        )
        with pytest.raises(ValidationError):
            result.lexical = ()

    def test_retrieval_result_expansions_roundtrip(self) -> None:
        expansion = ExpansionExecutor.build_expansions(_plan(), _STANDARD_BUDGET)[0]
        per_expansion = (ExpansionResult(expansion=expansion, lexical=(), vector=()),)
        result = RetrievalResult(expansions=per_expansion)
        restored = RetrievalResult.model_validate(result.model_dump(mode="json"))
        assert restored.expansions == per_expansion

    def test_fuse_rankings_dedups_across_expansions(self) -> None:
        """B02/R03: a mesma passagem recuperada por várias expansões é
        deduplicada pela fusão RRF — a contribuição de cada expansão soma."""
        passage = uuid4()
        fused = fuse_rankings(
            [
                [
                    RankedCandidate(
                        passage_id=passage, stage=RankingStage.LEXICAL, score=1.0, rank=0
                    )
                ],
                [RankedCandidate(passage_id=passage, stage=RankingStage.VECTOR, score=0.9, rank=0)],
            ],
            k=10.0,
        )
        assert len(fused) == 1
        assert fused[0].passage_id == passage


class TestAnswerRunExpansions:
    def test_expansions_append_only(self) -> None:
        expansion = ExpansionExecutor.build_expansions(_plan(), _STANDARD_BUDGET)[0]
        first = (ExpansionResult(expansion=expansion, lexical=(), vector=()),)
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
            created_at=utcnow(),
        )
        updated = run.transition(QueryStatus.RUNNING, candidates=(), expansions=first)
        assert updated.expansions == first
        with pytest.raises(InvalidTransitionError, match="append-only"):
            updated.transition(QueryStatus.RUNNING, expansions=())


class TestRetrievalServiceExpandedGuards:
    async def test_expanded_without_plan_fails_closed(self) -> None:
        from unittest.mock import AsyncMock

        from rag.application.search import RetrievalService

        class DummyEmbedding:
            async def embed_query(self, text: str) -> list[float]:
                return []

        class DummyReranker:
            async def rerank(self, query: str, documents: list[str]) -> list[float]:
                return []

        service = RetrievalService(DummyEmbedding(), DummyReranker())  # type: ignore[arg-type]
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
            status=QueryStatus.RUNNING,
            created_at=utcnow(),
        )
        with pytest.raises(TypeError, match="plano"):
            await service.retrieve(
                AsyncMock(),
                lexical_query=LexicalQuery(required_terms=("spleen",)),
                semantic_query="O que é spleen?",
                strategy=SearchStrategy.EXPANDED,
                run=run,
            )

    async def test_expanded_without_expansion_policy_fails_closed(self) -> None:
        from unittest.mock import AsyncMock

        from rag.application.search import RetrievalService

        class DummyEmbedding:
            async def embed_query(self, text: str) -> list[float]:
                return []

        class DummyReranker:
            async def rerank(self, query: str, documents: list[str]) -> list[float]:
                return []

        service = RetrievalService(DummyEmbedding(), DummyReranker())  # type: ignore[arg-type]
        run = AnswerRun(
            question_original="q",
            question_anonymized="q",
            explicit_filters=EditionFilter(),
            status=QueryStatus.RUNNING,
            created_at=utcnow(),
        )
        with pytest.raises(TypeError, match="política de expansão"):
            await service.retrieve(
                AsyncMock(),
                lexical_query=LexicalQuery(required_terms=("spleen",)),
                semantic_query="O que é spleen?",
                strategy=SearchStrategy.EXPANDED,
                plan=_plan(),
                run=run,
            )
