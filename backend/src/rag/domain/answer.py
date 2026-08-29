"""Contratos de resposta: quote, dissertative e verificação (SPEC §9).

Garantias estruturais:
- `QuoteResponse` não possui nenhum campo de prosa — só trechos literais e
  metadados (AC-08). Síntese é impossível por construção de tipo.
- `Claim` factual exige ao menos uma evidência; inferências são marcadas (AC-09).
- Abstenção exige razão e não carrega afirmações (AC-10).
"""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import VerificationAction


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=20)
    inference: bool = False

    @model_validator(mode="after")
    def _factual_claim_requires_evidence(self) -> Self:
        if not self.inference and not self.evidence_ids:
            raise ValueError("afirmação factual exige ao menos uma evidência (AC-09)")
        return self


class GeneratedAnswer(BaseModel):
    """Resposta dissertativa. Frozen com coleções imutáveis (RR02)."""

    model_config = ConfigDict(frozen=True)

    answer_markdown: str
    claims: tuple[Claim, ...] = Field(default_factory=tuple, max_length=200)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    abstained: bool
    abstention_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _abstention_contract(self) -> Self:
        if self.abstained:
            if not self.abstention_reason or not self.abstention_reason.strip():
                raise ValueError("abstenção exige abstention_reason (AC-10)")
            if self.claims:
                raise ValueError("resposta abstida não pode conter afirmações")
        elif self.abstention_reason is not None:
            raise ValueError("abstention_reason só é permitido quando abstained=true")
        ids = [c.id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de afirmações devem ser únicos")
        return self


class EvidenceRef(BaseModel):
    """Referência citável: edição, seção, página física/impressa e offsets."""

    model_config = ConfigDict(frozen=True)

    passage_id: UUID
    edition_id: UUID
    work_id: UUID
    text: str = Field(min_length=1)
    score: float
    rank: int = Field(ge=0)
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    physical_page: int | None = Field(default=None, ge=0)
    printed_label: str | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

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


class QuoteResponse(BaseModel):
    """Modo quote: somente trechos literais ranqueados. Sem prosa gerada."""

    model_config = ConfigDict(frozen=True)

    evidences: tuple[EvidenceRef, ...] = Field(default_factory=tuple, max_length=100)


class Contradiction(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str = Field(min_length=1)
    evidence_id: UUID
    detail: str = Field(min_length=1, max_length=2000)


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_claims: int = Field(ge=0)
    supported_claims: int = Field(ge=0)
    citation_coverage: float = Field(ge=0.0, le=1.0)
    action: VerificationAction
    invalid_evidence_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    unsupported_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    contradictions: tuple[Contradiction, ...] = Field(default_factory=tuple)
    iterations: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _counts_coherent(self) -> Self:
        if self.supported_claims > self.total_claims:
            raise ValueError("supported_claims não pode exceder total_claims")
        return self
