"""Entidades do acervo: Work, Edition, Section, Page, Passage (SPEC §5.1).

Toda edição é uma fonte distinta, mesmo dentro da mesma obra. Artefatos derivados
(ex.: PDF com camada OCR) são versionados e apontam para o artefato de origem;
`source_sha256` da edição identifica sempre o original imutável.
"""

from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import (
    ArtifactKind,
    ContributorRole,
    IngestionStatus,
    LicenseStatus,
    SourceType,
)
from rag.domain.identifiers import Sha256, sha256_of_text
from rag.domain.versions import utcnow

_MAX_TEXT = 5_000_000


class Contributor(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=300)
    role: ContributorRole = ContributorRole.AUTHOR
    ordinal: int = Field(default=0, ge=0)


class Work(BaseModel):
    canonical_title: str = Field(min_length=1, max_length=500)
    original_title: str | None = Field(default=None, max_length=500)
    authors: list[Contributor] = Field(default_factory=list)
    language: Literal["pt"] = "pt"
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)
    updated_at: AwareDatetime = Field(default_factory=utcnow)

    model_config = ConfigDict(str_strip_whitespace=True)


class DerivedArtifactRef(BaseModel):
    """Artefato derivado versionado (ex.: PDF com camada de texto OCR).

    `derived_from` referencia o hash do artefato de origem; o original permanece
    imutável e identificado por `Edition.source_sha256`.
    """

    model_config = ConfigDict(frozen=True)

    sha256: Sha256
    kind: ArtifactKind
    derived_from: Sha256
    generator: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime = Field(default_factory=utcnow)


class Edition(BaseModel):
    work_id: UUID
    title: str = Field(min_length=1, max_length=500)
    source_type: SourceType
    source_sha256: Sha256
    publisher: str | None = Field(default=None, max_length=300)
    publication_year: int | None = Field(default=None, ge=0, le=2100)
    isbn: str | None = Field(default=None, max_length=20)
    edition_label: str | None = Field(default=None, max_length=200)
    license_status: LicenseStatus = LicenseStatus.UNKNOWN
    ingestion_status: IngestionStatus = IngestionStatus.PENDING
    derived_artifacts: list[DerivedArtifactRef] = Field(default_factory=list)
    extraction_warnings: tuple[str, ...] = Field(default_factory=tuple)
    canonical_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="after")
    def _derived_must_point_to_original(self) -> Self:
        for artifact in self.derived_artifacts:
            if artifact.derived_from != self.source_sha256:
                raise ValueError(
                    "artefato derivado deve referenciar o hash do artefato original da edição"
                )
        return self


class Section(BaseModel):
    edition_id: UUID
    level: int = Field(ge=0, le=12)
    ordinal: int = Field(ge=0)
    path: list[str] = Field(min_length=1)
    parent_section_id: UUID | None = None
    title: str | None = Field(default=None, max_length=500)
    start_page: int | None = Field(default=None, ge=0)
    end_page: int | None = Field(default=None, ge=0)
    id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def _coherence(self) -> Self:
        if (
            self.end_page is not None
            and self.start_page is not None
            and self.end_page < self.start_page
        ):
            raise ValueError("end_page deve ser >= start_page")
        if len(self.path) != self.level + 1:
            raise ValueError("path deve ter exatamente level + 1 elementos")
        if self.level == 0 and self.parent_section_id is not None:
            raise ValueError("seção raiz (level 0) não pode ter parent_section_id")
        return self


class Page(BaseModel):
    edition_id: UUID
    physical_index: int = Field(ge=0)
    text: str = Field(max_length=_MAX_TEXT)
    text_sha256: Sha256
    printed_label: str | None = Field(default=None, max_length=50)
    id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def _hash_matches_text(self) -> Self:
        if sha256_of_text(self.text) != self.text_sha256:
            raise ValueError("text_sha256 não corresponde ao conteúdo de text")
        return self

    @classmethod
    def create(
        cls,
        *,
        edition_id: UUID,
        physical_index: int,
        text: str,
        printed_label: str | None = None,
    ) -> "Page":
        return cls(
            edition_id=edition_id,
            physical_index=physical_index,
            text=text,
            text_sha256=sha256_of_text(text),
            printed_label=printed_label,
        )


class Passage(BaseModel):
    """Unidade citável. `context_header` é metadado de recuperação e NUNCA faz
    parte do texto citável (AC-08/AC-12).

    `original_text`, quando presente, preserva o texto exato dos blocos
    canônicos de origem (T6-05 — antes da normalização do chunker) e é o
    campo que alimenta a citação literal (`citable_text`); `text`
    (normalizado) continua sendo a base de busca lexical/embeddings.
    `index_run_id` associa a passagem à execução de indexação que a
    produziu (T6-01) — `None` apenas para linhas fora do fluxo de `rag
    index` (ex.: fixtures de teste de repository).
    """

    edition_id: UUID
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    token_count: int = Field(gt=0)
    chunking_version_id: UUID
    section_id: UUID | None = None
    page_start_id: UUID | None = None
    page_end_id: UUID | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    context_header: str = Field(default="", max_length=1000)
    parent_passage_id: UUID | None = None
    embedding_version_id: UUID | None = None
    original_text: str | None = Field(default=None, min_length=1, max_length=_MAX_TEXT)
    index_run_id: UUID | None = None
    id: UUID = Field(default_factory=uuid4)

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

    @property
    def citable_text(self) -> str:
        """Único texto que pode aparecer como citação literal (T6-05: o
        original preservado do bloco canônico, quando disponível)."""
        return self.original_text if self.original_text is not None else self.text
