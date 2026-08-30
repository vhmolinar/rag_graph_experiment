"""Montagem de contexto e modo quote (SPEC §9.2, T12).

O domínio mantém, independente de framework:

- `ContextBudget`/`ContextPolicy`: orçamento de montagem por profundidade
  (número de evidências, orçamento total de contexto, expansão parental,
  limite flexível por edição), versionado via `ContextPolicyVersion`
  (SPEC §6, AC-15);
- `CitablePassage`/`ContextCandidate`: metadados citáveis (AC-03) unidos à
  posição do ranking;
- `select_evidences`: seleção pura com deduplicação, diversidade adaptativa
  (SPEC §8.6) e respeito do orçamento de contexto (SPEC §9.1);
- `PackedContext`: contexto montado, com garantia estrutural de que o
  orçamento declarado nunca é excedido.

O modo quote (`QuoteResponse`, T02) é a projeção das evidências seleccionadas
sem prosa: aqui não existe nenhum provedor de geração (AC-08).

Nenhum tipo de ORM, FastAPI ou SDK de modelo atravessa esta fronteira
(exercitado em `tests/unit/test_domain_purity.py`).
"""

from collections.abc import Sequence
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.answer import EvidenceRef
from rag.domain.enums import Depth

_MIN_MAX_EVIDENCES = 1
_MAX_MAX_EVIDENCES = 100
_MIN_CONTEXT_CHARS = 1
_MAX_CONTEXT_CHARS = 200_000


class ContextBudget(BaseModel):
    """Orçamento de montagem de contexto por profundidade (SPEC §9.1, T12).

    Parâmetros calibráveis (NOTES.md §4): `max_evidences` (evidências
    citáveis), `max_context_chars` (orçamento total = evidências + expansão
    parental), `parent_expansion_chars` (máximo de texto parental por
    evidência, contexto NUNCA citável) e `per_edition_limit` (limite
    flexível por edição; `None` = sem limite).
    """

    model_config = ConfigDict(frozen=True)

    depth: Depth
    max_evidences: int = Field(ge=_MIN_MAX_EVIDENCES, le=_MAX_MAX_EVIDENCES)
    max_context_chars: int = Field(ge=_MIN_CONTEXT_CHARS, le=_MAX_CONTEXT_CHARS)
    parent_expansion_chars: int = Field(ge=0, le=_MAX_CONTEXT_CHARS)
    per_edition_limit: int | None = Field(default=None, ge=_MIN_MAX_EVIDENCES)


class ContextPolicy(BaseModel):
    """Conjunto de orçamentos de contexto por profundidade (SPEC §9.1).

    Frozen com a coleção como tuple de pares (imutável, RR02). Exige cobertura
    das três profundidades — uma política parcial não pode existir
    silenciosamente. Versionada via `ContextPolicyVersion`.
    """

    model_config = ConfigDict(frozen=True)

    budgets: tuple[tuple[Depth, ContextBudget], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _covers_all_depths_exactly_once(self) -> Self:
        depths = [depth for depth, _ in self.budgets]
        if len(depths) != len(set(depths)):
            raise ValueError("cada profundidade pode aparecer uma única vez")
        if set(depths) != set(Depth):
            raise ValueError("política de contexto deve cobrir as três profundidades")
        for depth, budget in self.budgets:
            if budget.depth is not depth:
                raise ValueError("budget.depth deve coincidir com a chave da política")
        return self

    def budget_for(self, depth: Depth) -> ContextBudget:
        for d, budget in self.budgets:
            if d is depth:
                return budget
        raise ValueError(f"política de contexto ausente para profundidade {depth!s}")

    @classmethod
    def defaults(cls) -> "ContextPolicy":
        """Valores iniciais conservadores e monotonos por profundidade;
        calibração no benchmark de T19 (NOTES.md §4)."""
        return cls(
            budgets=(
                (
                    Depth.BRIEF,
                    ContextBudget(
                        depth=Depth.BRIEF,
                        max_evidences=4,
                        max_context_chars=4000,
                        parent_expansion_chars=800,
                        per_edition_limit=3,
                    ),
                ),
                (
                    Depth.STANDARD,
                    ContextBudget(
                        depth=Depth.STANDARD,
                        max_evidences=8,
                        max_context_chars=8000,
                        parent_expansion_chars=1200,
                        per_edition_limit=6,
                    ),
                ),
                (
                    Depth.DEEP,
                    ContextBudget(
                        depth=Depth.DEEP,
                        max_evidences=12,
                        max_context_chars=12000,
                        parent_expansion_chars=1600,
                        per_edition_limit=8,
                    ),
                ),
            )
        )


class CitablePassage(BaseModel):
    """Passagem com metadados citáveis (T12; AC-03).

    `PassagesRepository.get_citable` resolve os joins de uma vez: seção
    (path), página física e rótulo impresso (início), obra (`work_id`) e a
    passagem-pai (`parent_text`) para expansão de contexto. `parent_text` é
    contexto adicional e NUNCA é texto citável (SPEC §7.3).
    """

    model_config = ConfigDict(frozen=True)

    passage_id: UUID
    edition_id: UUID
    work_id: UUID
    text: str = Field(min_length=1)
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    physical_page: int | None = Field(default=None, ge=0)
    printed_label: str | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    parent_passage_id: UUID | None = None
    parent_text: str | None = None

    @model_validator(mode="after")
    def _offsets_coherent(self) -> Self:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start e char_end devem ser ambos definidos ou ambos nulos")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end <= self.char_start
        ):
            raise ValueError("char_end deve ser > char_start")
        return self


class ContextCandidate(BaseModel):
    """Candidato à evidência: metadados citáveis + posição do ranking (T12).

    É o input de `select_evidences`: une o resultado do reranking (T09,
    score/rank) aos metadados de origem (AC-03) e à expansão parental.
    """

    model_config = ConfigDict(frozen=True)

    passage: CitablePassage
    score: float
    rank: int = Field(ge=0)


class ContextEvidence(BaseModel):
    """Evidência citável + expansão parental (contexto, NUNCA citável)."""

    model_config = ConfigDict(frozen=True)

    evidence: EvidenceRef
    parent_passage_id: UUID | None = None
    parent_text: str | None = None


class PackedContext(BaseModel):
    """Contexto montado por profundidade (T12).

    Frozen. `total_chars` contabiliza as evidências + expansão parental; a
    validação impõe estruturalmente que o orçamento declarado não seja
    excedido — nunca se devolve contexto acima do orçamento (falha fechada).
    `policy_version_id` permite reproduzir a política usada (AC-15).
    """

    model_config = ConfigDict(frozen=True)

    evidences: tuple[ContextEvidence, ...] = Field(default_factory=tuple)
    total_chars: int = Field(ge=0)
    context_budget_chars: int = Field(ge=_MIN_CONTEXT_CHARS, le=_MAX_CONTEXT_CHARS)
    policy_version_id: UUID | None = None

    @model_validator(mode="after")
    def _budget_not_exceeded(self) -> Self:
        if self.total_chars > self.context_budget_chars:
            raise ValueError("orçamento de contexto excedido (T12)")
        return self


def _evidence_ref(candidate: ContextCandidate) -> EvidenceRef:
    """Projeta um candidato à `EvidenceRef` citável (AC-03/AC-08)."""
    passage = candidate.passage
    return EvidenceRef(
        passage_id=passage.passage_id,
        edition_id=passage.edition_id,
        work_id=passage.work_id,
        text=passage.text,
        score=candidate.score,
        rank=candidate.rank,
        section_path=passage.section_path,
        physical_page=passage.physical_page,
        printed_label=passage.printed_label,
        char_start=passage.char_start,
        char_end=passage.char_end,
    )


def _parent_expansion(candidate: ContextCandidate, limit_chars: int) -> str | None:
    """Texto parental como contexto adicional, truncado ao limite.

    NUNCA citável (SPEC §7.3): serve só a dar contexto à geração (T13). Um
    limite <= 0 desativa a expansão para este candidato.
    """
    if not candidate.passage.parent_text or limit_chars <= 0:
        return None
    return candidate.passage.parent_text[:limit_chars]


def select_evidences(
    candidates: Sequence[ContextCandidate],
    *,
    budget: ContextBudget,
    needs_diversity: bool,
) -> tuple[ContextEvidence, ...]:
    """Seleciona evidências com deduplicação, diversidade adaptativa e
    orçamento de contexto (SPEC §8.6, SPEC §9.1, T12).

    - Deduplicação por `passage_id`, preservando a ordem do ranking;
    - `max_evidences` limita o número de evidências;
    - o orçamento de contexto é respeitado DURANTE a seleção: uma evidência
      que não couber no orçamento restante é descartada — nunca se estoura;
    - diversidade adaptativa (SPEC §8.6): SÓ quando `needs_diversity`, cada
      edição contribui ao máximo `per_edition_limit` evidências — limite
      flexível: não é preenchido com obras menos relevantes e a seleção
      pode ficar abaixo de `max_evidences` (NOTES.md §10.13 item 4).

    Função pura sobre candidatos ranqueados; não toca provedores nem o banco.
    """
    selected: list[ContextEvidence] = []
    per_edition: dict[UUID, int] = {}
    seen: set[UUID] = set()
    remaining = budget.max_context_chars
    for candidate in candidates:
        if len(selected) >= budget.max_evidences:
            break
        if candidate.passage.passage_id in seen:
            continue
        parent = _parent_expansion(candidate, budget.parent_expansion_chars)
        cost = len(candidate.passage.text) + (len(parent) if parent else 0)
        if cost > remaining:
            continue
        if (
            needs_diversity
            and budget.per_edition_limit is not None
            and per_edition.get(candidate.passage.edition_id, 0) >= budget.per_edition_limit
        ):
            continue
        seen.add(candidate.passage.passage_id)
        selected.append(
            ContextEvidence(
                evidence=_evidence_ref(candidate),
                parent_passage_id=candidate.passage.parent_passage_id,
                parent_text=parent,
            )
        )
        per_edition[candidate.passage.edition_id] = (
            per_edition.get(candidate.passage.edition_id, 0) + 1
        )
        remaining -= cost
    return tuple(selected)


def context_total_chars(evidences: tuple[ContextEvidence, ...]) -> int:
    """Total de caracteres de contexto: evidências + expansão parental."""
    return sum(
        len(item.evidence.text) + (len(item.parent_text) if item.parent_text else 0)
        for item in evidences
    )
