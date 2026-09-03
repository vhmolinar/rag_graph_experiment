"""Recuperação vetorial, fusão RRF e orçamento por profundidade (SPEC §8.5, T09).

O domínio mantém, independente de framework:

- `RetrievalBudget`/`RetrievalPolicy`: parâmetros de orçamento calibráveis por
  profundidade, versionados via `RetrievalPolicyVersion` (SPEC §6);
- `fuse_rankings`: fusão Reciprocal Rank Fusion pura e determinística;
- `RetrievalResult`: scores e posições de todos os estágios da recuperação
  (AC-06), preservados para persistência em `AnswerRun`.

Nenhum tipo de ORM, FastAPI ou SDK de modelo atravessa esta fronteira
(exercitado em `tests/unit/test_domain_purity.py`).
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import Depth, RankingStage, SearchStrategy
from rag.domain.runs import RankedCandidate

_MIN_TOP_K = 1
_MAX_TOP_K = 500


class RetrievalBudget(BaseModel):
    """Orçamento de candidatos por profundidade (SPEC §8.5).

    Parâmetros calibráveis (NOTES.md §4): `top_k` de cada estágio, constante
    RRF (`rrf_k`) e quantidade enviada ao reranker (`rerank_top_n`). Valores
    iniciais conservadores; calibração no benchmark de T19.
    """

    model_config = ConfigDict(frozen=True)

    depth: Depth
    lexical_top_k: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)
    vector_top_k: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)
    rrf_k: float = Field(ge=1.0, le=1000.0)
    rerank_top_n: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)


class RetrievalPolicy(BaseModel):
    """Conjunto de orçamentos por profundidade (SPEC §8.5).

    Frozen com a coleção como tuple de pares (imutável, RR02). Exige cobertura
    das três profundidades — uma política parcial não pode existir
    silenciosamente. Versionada via `RetrievalPolicyVersion`.
    """

    model_config = ConfigDict(frozen=True)

    budgets: tuple[tuple[Depth, RetrievalBudget], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _covers_all_depths_exactly_once(self) -> Self:
        depths = [depth for depth, _ in self.budgets]
        if len(depths) != len(set(depths)):
            raise ValueError("cada profundidade pode aparecer uma única vez")
        if set(depths) != set(Depth):
            raise ValueError("política de orçamento deve cobrir as três profundidades")
        for depth, budget in self.budgets:
            if budget.depth is not depth:
                raise ValueError("budget.depth deve coincidir com a chave da política")
        return self

    def budget_for(self, depth: Depth) -> RetrievalBudget:
        for d, budget in self.budgets:
            if d is depth:
                return budget
        raise ValueError(f"política de orçamento ausente para profundidade {depth!s}")

    @classmethod
    def defaults(cls) -> "RetrievalPolicy":
        """Valores iniciais conservadores e monotonos por profundidade;
        calibração no benchmark de T19 (NOTES.md §4)."""
        return cls(
            budgets=(
                (
                    Depth.BRIEF,
                    RetrievalBudget(
                        depth=Depth.BRIEF,
                        lexical_top_k=15,
                        vector_top_k=15,
                        rrf_k=60.0,
                        rerank_top_n=10,
                    ),
                ),
                (
                    Depth.STANDARD,
                    RetrievalBudget(
                        depth=Depth.STANDARD,
                        lexical_top_k=30,
                        vector_top_k=30,
                        rrf_k=60.0,
                        rerank_top_n=20,
                    ),
                ),
                (
                    Depth.DEEP,
                    RetrievalBudget(
                        depth=Depth.DEEP,
                        lexical_top_k=50,
                        vector_top_k=50,
                        rrf_k=60.0,
                        rerank_top_n=30,
                    ),
                ),
            )
        )


def fuse_rankings(
    ranked_lists: Sequence[Sequence[RankedCandidate]],
    *,
    k: float,
) -> tuple[RankedCandidate, ...]:
    """Reciprocal Rank Fusion (SPEC §8.5, AC-06).

    Contribuição de cada lista `l` para uma passagem: `1/(k + rank + 1)`, com
    `rank` 0-based (posição na lista). A constante `k` pertence à política de
    profundidade (calibrable). Ordenação determinística: score RRF
    descendente, desempate por `passage_id` ascendente — nunca depende de
    ordem de iteración do dicionário.

    A função não toca o texto nem o provider de reranking: é pura sobre as
    listas ranqueadas.
    """
    if k <= 0:
        raise ValueError("constante RRF deve ser > 0")
    scores: dict[UUID, float] = defaultdict(float)
    for candidates in ranked_lists:
        for candidate in candidates:
            scores[candidate.passage_id] += 1.0 / (k + candidate.rank + 1)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        RankedCandidate(
            passage_id=passage_id,
            stage=RankingStage.FUSED,
            score=score,
            rank=rank,
        )
        for rank, (passage_id, score) in enumerate(ordered)
    )


class RetrievalResult(BaseModel):
    """Scores e posições de todos os estágios da recuperação (AC-06).

    Mantém as listas lexical e vetorial independentes (SPEC §8.5), além da
    fundida por RRF e da reranked, para que a execução registre o ranking de
    cada estágio sem reconstrução. `strategy` regista a estratégia RESOLVIDA
    que gerou a execução: `literal` NUNCA popula `vector`/`fused`/`reranked`
    (SPEC §8.3, B01), enquanto `hybrid`/`expanded` executam os quatro estágios.
    Frozen (RR02).
    """

    model_config = ConfigDict(frozen=True)

    lexical: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    vector: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    fused: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    reranked: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    policy_version_id: UUID | None = None
    embedding_version_id: UUID | None = None
    run_id: UUID | None = None
    strategy: SearchStrategy = SearchStrategy.HYBRID

    def final_candidates(self) -> tuple[RankedCandidate, ...]:
        """Candidatos finais para a montagem de contexto (SPEC §8.5, AC-06).

        `literal` não executa fusão nem reranking: a lista lexical É o ranking
        final. `hybrid`/`expanded` consumem a lista reranked.
        """
        if self.strategy is SearchStrategy.LITERAL:
            return self.lexical
        return self.reranked

    def answer_run_candidates(self) -> tuple[RankedCandidate, ...]:
        """Candidatos de todos os estágios para persistir em
        `AnswerRun.candidates` (append-only; AC-06).

        Coincide com a estratégia executada: num caso `literal` só o estágio
        lexical está populado, entón apenas ele é persistido.
        """
        return (*self.lexical, *self.vector, *self.fused, *self.reranked)
