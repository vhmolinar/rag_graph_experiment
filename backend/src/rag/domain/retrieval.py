"""Recuperação vetorial, fusão RRF, orçamento e expansão (SPEC §8.5, T09/R03).

O domínio mantém, independente de framework:

- `RetrievalBudget`/`RetrievalPolicy`: parâmetros de orçamento calibráveis por
  profundidade, versionados via `RetrievalPolicyVersion` (SPEC §6);
- `Expansion`/`ExpansionBudget`/`ExpansionPolicy`: consultas de expansão da
  estratégia `expanded` (SPEC §8.3, B02/R03) com orçamento TOTAL por
  profundidade — o orçamento nunca é multiplicado sem limite pelo número de
  subperguntas/aliases —, versionados via `ExpansionPolicyVersion` (AC-15);
- `fuse_rankings`: fusão Reciprocal Rank Fusion pura e determinística;
- `ExpansionResult`/`RetrievalResult`: scores e posições de todos os estágios
  da recuperação (AC-06), com origem de cada candidato por expansão,
  preservados para persistência em `AnswerRun`.

Nenhum tipo de ORM, FastAPI ou SDK de modelo atravessa esta fronteira
(exercitado em `tests/unit/test_domain_purity.py`).
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import Depth, ExpansionKind, RankingStage, SearchStrategy
from rag.domain.query import LexicalQuery

_MIN_TOP_K = 1
_MAX_TOP_K = 500


class RankedCandidate(BaseModel):
    """Posição de uma passagem em um estágio específico do ranking (AC-06).

    Definida aqui (núcleo da recuperação) e re-exportada por `domain/runs.py`
    para romper a dependência circular `retrieval <-> runs` (R03): `runs.py`
    consome `ExpansionResult`, que contém candidatos desta classe.
    """

    model_config = ConfigDict(frozen=True)

    passage_id: UUID
    stage: RankingStage
    score: float
    rank: int = Field(ge=0)


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


class Expansion(BaseModel):
    """Consulta de uma expansão da estratégia `expanded` (SPEC §8.3, B02/R03).

    Cada expansão é um par (consulta lexical, consulta semântica) com uma
    origem (`ExpansionKind`): a consulta principal, uma subpergunta ou um
    alias do provedor de planejamento. Preservar a origem de cada candidato
    exige recuperar e ranquear cada expansão em separado, para que o ranking
    por expansão fique rastreável (AC-06/AC-15).
    """

    model_config = ConfigDict(frozen=True)

    kind: ExpansionKind
    semantic_query: str = Field(min_length=1, max_length=4000)
    lexical_query: LexicalQuery


class ExpansionBudget(BaseModel):
    """Orçamento TOTAL de expansão para uma profundidade (SPEC §8.3, B02/R03).

    `max_expansions` limita o número de expansões executadas (consulta
    principal + subperguntas + aliases); `lexical_top_k`/`vector_top_k` são o
    limite de candidatos recuperados POR expansão; `fused_top_k` é o teto
    TOTAL da fusão — a quantidade de candidatos finais NUNCA é multiplicada
    sem limite pelo número de subperguntas/aliases. `rerank_top_n` seleciona
    do fundido o subconjunto enviado ao reranker.
    """

    model_config = ConfigDict(frozen=True)

    depth: Depth
    max_expansions: int = Field(ge=1, le=50)
    lexical_top_k: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)
    vector_top_k: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)
    rrf_k: float = Field(ge=1.0, le=1000.0)
    fused_top_k: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)
    rerank_top_n: int = Field(ge=_MIN_TOP_K, le=_MAX_TOP_K)


class ExpansionPolicy(BaseModel):
    """Conjunto de orçamentos de expansão por profundidade (SPEC §8.3).

    Frozen, com a coleção como tuple de pares (imutável, RR02). Exige
    cobertura das três profundidades — uma política parcial não pode existir
    silenciosamente. Versionada via `ExpansionPolicyVersion` (AC-15).
    """

    model_config = ConfigDict(frozen=True)

    budgets: tuple[tuple[Depth, ExpansionBudget], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _covers_all_depths_exactly_once(self) -> Self:
        depths = [depth for depth, _ in self.budgets]
        if len(depths) != len(set(depths)):
            raise ValueError("cada profundidade pode aparecer uma única vez")
        if set(depths) != set(Depth):
            raise ValueError("política de expansão deve cobrir as três profundidades")
        for depth, budget in self.budgets:
            if budget.depth is not depth:
                raise ValueError("budget.depth deve coincidir com a chave da política")
            if budget.rerank_top_n > budget.fused_top_k:
                raise ValueError("rerank_top_n não pode exceder o teto total fused_top_k")
        return self

    def budget_for(self, depth: Depth) -> ExpansionBudget:
        for d, budget in self.budgets:
            if d is depth:
                return budget
        raise ValueError(f"política de expansão ausente para profundidade {depth!s}")

    @classmethod
    def defaults(cls) -> "ExpansionPolicy":
        """Valores iniciais conservadores por profundidade; calibração no
        benchmark de T19 (NOTES.md §4)."""
        return cls(
            budgets=(
                (
                    Depth.BRIEF,
                    ExpansionBudget(
                        depth=Depth.BRIEF,
                        max_expansions=4,
                        lexical_top_k=10,
                        vector_top_k=10,
                        rrf_k=60.0,
                        fused_top_k=20,
                        rerank_top_n=10,
                    ),
                ),
                (
                    Depth.STANDARD,
                    ExpansionBudget(
                        depth=Depth.STANDARD,
                        max_expansions=6,
                        lexical_top_k=15,
                        vector_top_k=15,
                        rrf_k=60.0,
                        fused_top_k=40,
                        rerank_top_n=20,
                    ),
                ),
                (
                    Depth.DEEP,
                    ExpansionBudget(
                        depth=Depth.DEEP,
                        max_expansions=8,
                        lexical_top_k=20,
                        vector_top_k=20,
                        rrf_k=60.0,
                        fused_top_k=60,
                        rerank_top_n=30,
                    ),
                ),
            )
        )


class ExpansionResult(BaseModel):
    """Resultado de recuperação de UMA expansão (B02/R03).

    Preserva a consulta executada (`expansion`) e as listas lexical/vetorial
    com scores e posições, para rastreabilidade do ranking POR expansão —
    a consulta que produzou cada candidato fica registrada (AC-15).
    """

    model_config = ConfigDict(frozen=True)

    expansion: Expansion
    lexical: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    vector: tuple[RankedCandidate, ...] = Field(default_factory=tuple)


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
    Na estratégia `expanded`, `lexical`/`vector` são a concatenação de TODAS
    as expansões e `expansions` preserva a consulta e o ranking de cada uma —
    a origem de cada candidato fica rastreável (B02/R03, AC-15). Frozen (RR02).
    """

    model_config = ConfigDict(frozen=True)

    lexical: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    vector: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    fused: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    reranked: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    expansions: tuple[ExpansionResult, ...] = Field(default_factory=tuple)
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
