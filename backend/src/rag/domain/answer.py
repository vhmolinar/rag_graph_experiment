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
    """Referência citável: edição, seção, páginas (início/fim) e offsets.

    T12-01 (AC-03): uma passagem pode abranger várias páginas físicas. A
    referência transporta início e fim da localização — IDs e índices
    físicos de AMBAS as páginas, com os rótulos impressos e os offsets
    relativos a cada uma (`char_start` sobre `physical_page`, `char_end`
    sobre `page_end`). Para uma passagem de página única, `page_end` é igual
    a `physical_page` (e `printed_end_label` a `printed_label`).
    """

    model_config = ConfigDict(frozen=True)

    passage_id: UUID
    edition_id: UUID
    work_id: UUID
    text: str = Field(min_length=1)
    score: float
    rank: int = Field(ge=0)
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    page_start_id: UUID | None = None
    page_end_id: UUID | None = None
    physical_page: int | None = Field(default=None, ge=0)
    page_end: int | None = Field(default=None, ge=0)
    printed_label: str | None = None
    printed_end_label: str | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _offsets_coherent(self) -> Self:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start e char_end devem ser ambos definidos ou ambos nulos")
        if self.char_start is not None and self.char_end is not None:
            # T12-R2-01: `char_end > char_start` só vale quando ambos os offsets
            # referem à MESMA página. Para uma passagem multipágina, `char_start`
            # é relativo à `physical_page` e `char_end` à `page_end` — podem ser
            # invertidos entre páginas (ex.: início no offset 100 da página A,
            # fim no offset 3 da página B) e ainda ser corretos.
            same_page = (
                self.page_end is None
                or self.physical_page is None
                or self.page_end == self.physical_page
            )
            if same_page and self.char_end <= self.char_start:
                raise ValueError("char_end deve ser > char_start")
        if (
            self.physical_page is not None
            and self.page_end is not None
            and self.page_end < self.physical_page
        ):
            raise ValueError("page_end deve ser >= physical_page")
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
