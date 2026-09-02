"""Verificação de respostas dissertativas (SPEC §9.4, T13; AC-09, AC-10).

O domínio mantém, independente de framework:

- `VerificationBudget`/`VerificationPolicy`: orçamento de verificação por
  profundidade (iterações de geração/verificação e limiar de cobertura de
  citações), versionado via `VerificationPolicyVersion` (SPEC §6, AC-15);
- `invalid_evidence_ids`: rechazo determinístico de IDs de evidência
  inexistentes (citação fabricada — SPEC §9.4 "rejeita IDs inexistentes");
- `assess_claims`: agrega os veredictos do provedor por afirmação (suporte e
  contradição);
- `mark_unsupported_as_inference`: correção determinística que marca
  afirmações não sustentadas como inferências (AC-09), sem introduzir
  conteúdo novo (SPEC §9.4).

A existência dos IDs e a agregação final são responsabilidade do domínio e do
serviço, NUNCA do provedor de verificação (NOTES.md §10.14 item 2/3).

Nenhum tipo de ORM, FastAPI ou SDK de modelo atravessa esta fronteira
(exercitado em `tests/unit/test_domain_purity.py`).
"""

from collections.abc import Sequence, Set
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.answer import Claim, Contradiction, GeneratedAnswer
from rag.domain.enums import Depth
from rag.domain.providers import ClaimVerdict

_MIN_MAX_ITERATIONS = 0
_MAX_MAX_ITERATIONS = 10


class VerificationBudget(BaseModel):
    """Orçamento de verificação por profundidade (SPEC §9.4, T13).

    Parâmetros calibráveis (NOTES.md §4): `max_iterations` (número de
    regenerações após a geração inicial) e `support_threshold` (limiar de
    cobertura de citações — abaixo dele, abstenção forzada).
    """

    model_config = ConfigDict(frozen=True)

    depth: Depth
    max_iterations: int = Field(ge=_MIN_MAX_ITERATIONS, le=_MAX_MAX_ITERATIONS)
    support_threshold: float = Field(ge=0.0, le=1.0)


class VerificationPolicy(BaseModel):
    """Conjunto de orçamentos de verificação por profundidade (SPEC §9.4).

    Frozen com a coleção como tuple de pares (imutável, RR02). Exige cobertura
    das três profundidades — uma política parcial não pode existir
    silenciosamente. Versionada via `VerificationPolicyVersion`.
    """

    model_config = ConfigDict(frozen=True)

    budgets: tuple[tuple[Depth, VerificationBudget], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _covers_all_depths_exactly_once(self) -> Self:
        depths = [depth for depth, _ in self.budgets]
        if len(depths) != len(set(depths)):
            raise ValueError("cada profundidade pode aparecer uma única vez")
        if set(depths) != set(Depth):
            raise ValueError("política de verificação deve cobrir as três profundidades")
        for depth, budget in self.budgets:
            if budget.depth is not depth:
                raise ValueError("budget.depth deve coincidir com a chave da política")
        return self

    def budget_for(self, depth: Depth) -> VerificationBudget:
        for d, budget in self.budgets:
            if d is depth:
                return budget
        raise ValueError(f"política de verificação ausente para profundidade {depth!s}")

    @classmethod
    def defaults(cls) -> "VerificationPolicy":
        """Valores iniciais conservadores e monotonos por profundidade;
        calibração no benchmark de T19 (NOTES.md §4)."""
        return cls(
            budgets=(
                (
                    Depth.BRIEF,
                    VerificationBudget(
                        depth=Depth.BRIEF,
                        max_iterations=1,
                        support_threshold=0.5,
                    ),
                ),
                (
                    Depth.STANDARD,
                    VerificationBudget(
                        depth=Depth.STANDARD,
                        max_iterations=2,
                        support_threshold=0.7,
                    ),
                ),
                (
                    Depth.DEEP,
                    VerificationBudget(
                        depth=Depth.DEEP,
                        max_iterations=3,
                        support_threshold=0.8,
                    ),
                ),
            )
        )


def invalid_evidence_ids(
    claims: Sequence[Claim],
    allowed_ids: Set[UUID],
) -> tuple[UUID, ...]:
    """IDs de evidência citados por afirmações e ausentes do contexto montado.

    SPEC §9.4 "rejeita IDs inexistentes": uma citação fabricada nunca pode ser
    liberada. Determinístico (ordenada lexicograficamente) — não depende do
    provedor (NOTES.md §10.14 item 2).
    """
    invalid: set[UUID] = set()
    for claim in claims:
        for evidence_id in claim.evidence_ids:
            if evidence_id not in allowed_ids:
                invalid.add(evidence_id)
    return tuple(sorted(invalid, key=str))


class ClaimAssessment(BaseModel):
    """Veredicto agregado de uma afirmação (SPEC §9.4, T13)."""

    model_config = ConfigDict(frozen=True)

    claim_id: str = Field(min_length=1)
    supported: bool
    contradictions: tuple[Contradiction, ...] = Field(default_factory=tuple)


def assess_claims(
    claims: Sequence[Claim],
    verdicts: Sequence[ClaimVerdict],
) -> tuple[ClaimAssessment, ...]:
    """Agrega os veredictos do provedor por afirmação (SPEC §9.4).

    Uma afirmação é sustentada SÓ quando todas as suas evidências citadas
    estão cobertas por um veredicto 'supported' SEM contradição. Pairs
    ausentes do veredicto são tratados como não sustentados (conservador,
    falha fechada — o provedor não pode omitir silenciosamente um juízo);
    veredictos para pares não informados (afirmação/evidência inexistentes)
    são ignorados defensivamente (NOTES.md §10.14 item 3).

    T13-02: uma contradição torna o par/afirmação NÃO sustentado
    INDEPENDENTEMENTE do valor de `supported` — um veredicto
    `supported=true, contradiction=true` (combinação permitida pelo schema)
    não pode manter a afirmação factual (AC-09).
    """
    by_pair: dict[tuple[str, UUID], ClaimVerdict] = {
        (verdict.claim_id, verdict.evidence_id): verdict for verdict in verdicts
    }
    assessments: list[ClaimAssessment] = []
    for claim in claims:
        contradictions: list[Contradiction] = []
        supported = True
        for evidence_id in claim.evidence_ids:
            verdict = by_pair.get((claim.id, evidence_id))
            if verdict is None or not verdict.supported or verdict.contradiction:
                supported = False
            if verdict is not None and verdict.contradiction:
                contradictions.append(
                    Contradiction(
                        claim_id=claim.id,
                        evidence_id=evidence_id,
                        detail=verdict.detail or "A fonte contradice a afirmação.",
                    )
                )
        assessments.append(
            ClaimAssessment(
                claim_id=claim.id,
                supported=supported,
                contradictions=tuple(contradictions),
            )
        )
    return tuple(assessments)


def mark_unsupported_as_inference(
    answer: GeneratedAnswer,
    unsupported_claim_ids: Set[str],
) -> GeneratedAnswer:
    """Marca afirmações não sustentadas como inferências (SPEC §9.4, AC-09).

    Correção determinística após o limite de regenerações (opção "marca"): a
    afirmação permanece mas fica marcada explicitamente como inferência — AC-09
    exige "marcação explícita de inferência" para afirmações sem evidência
    válida. Nunca introduz conteúdo novo — só ajusta `Claim.inference`. O
    resultado é revalidado integralmente (R05).
    """
    data = answer.model_dump(mode="python")
    claims: list[dict[str, object]] = []
    for claim in answer.claims:
        dumped = claim.model_dump(mode="python")
        if claim.id in unsupported_claim_ids and not claim.inference:
            dumped["inference"] = True
        claims.append(dumped)
    data["claims"] = claims
    return GeneratedAnswer.model_validate(data)
