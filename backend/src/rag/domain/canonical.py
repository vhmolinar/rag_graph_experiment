"""Representação canônica de um documento extraído (SPEC §7.2).

Contrato próprio do sistema: adapters (Docling, OCR) produzem
`CanonicalDocument`; todo o restante — persistência, chunking, recuperação —
consome somente este schema, nunca objetos internos de frameworks (regra de
pureza do domínio; AC-03).

Preserva: hierarquia de títulos, ordem de leitura, limites e rótulos de página
quando disponíveis, offsets para destaque, texto normalizado e original, e
warnings de extração.
"""

import hashlib
import json
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import SourceType


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"


class CanonicalPage(BaseModel):
    """Página física do documento (PDF). EPUB não possui páginas."""

    model_config = ConfigDict(frozen=True)

    physical_index: int = Field(ge=0)
    printed_label: str | None = None
    text: str


class CanonicalBlock(BaseModel):
    """Bloco de texto em ordem de leitura com proveniência completa.

    `text` é normalizado (espaços colapsados, hífens de quebra removidos);
    `original_text` preserva a forma extraída para citação fiel. Offsets
    (`char_start`/`char_end`) referem-se ao texto da página `page_index`
    (PDF) — ausentes em EPUB, onde a posição é dada por seção + ordinal.
    """

    model_config = ConfigDict(frozen=True)

    ordinal: int = Field(ge=0)
    kind: BlockKind
    level: int = Field(ge=0)
    text: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    page_index: int | None = Field(default=None, ge=0)
    page_label: str | None = None
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

    @model_validator(mode="after")
    def _heading_carries_path(self) -> Self:
        if self.kind is BlockKind.HEADING and not self.section_path:
            raise ValueError("heading deve carregar section_path")
        return self


class CanonicalDocument(BaseModel):
    """Documento extraído: blocos ordenados, páginas (quando houver) e warnings.

    Invariantes:
    - ordinais dos blocos são 0..n-1 sem lacunas (ordem de leitura);
    - PDF (`pdf_text`) tem páginas e todo bloco referencia página existente;
    - EPUB não tem páginas e nenhum bloco tem `page_index`;
    - `pdf_scan` nunca chega aqui: passa antes por `rag ocr` (decisão §10.1.5).
    """

    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    blocks: tuple[CanonicalBlock, ...] = Field(min_length=1)
    pages: tuple[CanonicalPage, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _source_type_extractable(self) -> Self:
        if self.source_type is SourceType.PDF_SCAN:
            raise ValueError("pdf_scan exige OCR prévio (rag ocr); não é extraível diretamente")
        return self

    def fingerprint(self) -> str:
        """Identidade determinística de toda a representação consumida pelo T06."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def _ordinals_sequential(self) -> Self:
        for expected, block in enumerate(self.blocks):
            if block.ordinal != expected:
                raise ValueError(
                    f"ordinais devem ser sequenciais: esperado {expected}, recebido {block.ordinal}"
                )
        return self

    @model_validator(mode="after")
    def _pages_coherent_with_source(self) -> Self:
        if self.source_type is SourceType.EPUB:
            if self.pages:
                raise ValueError("EPUB não possui páginas físicas")
            if any(b.page_index is not None for b in self.blocks):
                raise ValueError("bloco de EPUB não pode ter page_index")
        else:
            if not self.pages:
                raise ValueError("PDF-texto exige páginas")
            page_indexes = {p.physical_index for p in self.pages}
            if len(page_indexes) != len(self.pages):
                raise ValueError("physical_index duplicado em pages")
            for block in self.blocks:
                if block.page_index is None:
                    raise ValueError("bloco de PDF-texto exige page_index")
                if block.page_index not in page_indexes:
                    raise ValueError(
                        f"bloco {block.ordinal} referencia página inexistente {block.page_index}"
                    )
        return self
