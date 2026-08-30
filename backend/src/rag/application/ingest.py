"""Serviço de ingestão (T05; SPEC §7.1-7.2; AC-01, AC-02).

Orquestra: validação de arquivo e metadados → hash → deduplicação →
extração canônica → persistência atômica (obra, edição, seções, páginas,
artefatos). Nada é publicado parcialmente: a gravação relacional ocorre em
uma única transação; o artifact store é content-addressed e idempotente, e
objetos órfãos de uma transação abortada são detectáveis por `audit()`.

`--dry-run` executa validação, hash, deduplicação e extração sem persistir.
"""

from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, field_validator

from rag.adapters.docling_adapter import DoclingExtractor
from rag.adapters.ocr_adapter import OcrProvenance, load_provenance
from rag.domain.canonical import BlockKind, CanonicalDocument
from rag.domain.enums import ArtifactKind, IngestionStatus, LicenseStatus, SourceType
from rag.domain.errors import ConflictError, IngestionError
from rag.domain.identifiers import Sha256, sha256_of_file
from rag.domain.library import (
    Contributor,
    DerivedArtifactRef,
    Edition,
    Page,
    Section,
    Work,
)
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.works import WorksRepository

__all__ = ["sha256_of_file"]  # re-exportado: callers históricos importam daqui

# Matriz fechada de combinações válidas entre extensão e source_type (T5-07):
# nenhuma outra combinação é aceita, em nenhuma direção.
_VALID_SOURCE_TYPES_BY_SUFFIX: dict[str, frozenset[SourceType]] = {
    ".pdf": frozenset({SourceType.PDF_TEXT, SourceType.PDF_SCAN}),
    ".epub": frozenset({SourceType.EPUB}),
}


class IngestMetadata(BaseModel):
    """Contrato do arquivo YAML de metadados de ingestão (SPEC §7.1).

    Fase 1 é monolíngue: `language` deve ser "pt" (o schema físico impõe
    `CHECK (language = 'pt')`).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    authors: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    original_title: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=300)
    publication_year: int | None = Field(default=None, ge=0, le=2100)
    isbn: str | None = Field(default=None, max_length=20)
    edition_label: str | None = Field(default=None, max_length=200)
    license_status: LicenseStatus = LicenseStatus.UNKNOWN
    language: Literal["pt"] = "pt"
    source_type: SourceType | None = None
    ocr_artifact: Path | None = None

    @field_validator("language", mode="before")
    @classmethod
    def _language_supported(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() != "pt":
            raise ValueError("idioma não suportado na fase 1: somente 'pt' (português)")
        return value


class IngestReport(BaseModel):
    """Resultado da ingestão (ou do dry-run). Nunca contém texto do livro."""

    model_config = ConfigDict(frozen=True)

    edition_id: str | None
    work_id: str | None
    source_sha256: Sha256
    source_type: SourceType
    created: bool
    dry_run: bool
    sections: int
    pages: int
    blocks: int
    warnings: tuple[str, ...]


def load_metadata(path: Path) -> IngestMetadata:
    """Carrega e valida o YAML de metadados; erros são sanitizados."""
    if not path.is_file():
        raise IngestionError("Arquivo de metadados não encontrado.", context={"name": path.name})
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise IngestionError("YAML de metadados inválido.", cause=exc) from exc
    if not isinstance(raw, dict):
        raise IngestionError("Metadados devem ser um objeto YAML (chave: valor).")
    try:
        return IngestMetadata.model_validate(raw)
    except ValueError as exc:
        raise IngestionError(
            "Metadados inválidos: verifique campos obrigatórios e formatos.",
            cause=exc,
        ) from exc


def derive_sections(edition_id: UUID, doc: CanonicalDocument) -> list[Section]:
    """Deriva seções a partir dos headings, com páginas de início/fim e pais.

    `end_page` é a maior página física com bloco no subtree da seção.
    """
    sections: list[Section] = []
    by_path: dict[tuple[str, ...], Section] = {}
    for block in doc.blocks:
        if block.kind is not BlockKind.HEADING:
            continue
        path = block.section_path
        parent = by_path.get(path[:-1])
        section = Section(
            edition_id=edition_id,
            level=block.level,
            ordinal=len(sections),
            path=list(path),
            parent_section_id=parent.id if parent else None,
            title=block.text,
            start_page=block.page_index,
            end_page=block.page_index,
        )
        sections.append(section)
        by_path[path] = section

    # end_page: maior página dentre os blocos do subtree de cada seção.
    for block in doc.blocks:
        if block.page_index is None:
            continue
        for i in range(len(block.section_path), 0, -1):
            ancestor = by_path.get(block.section_path[:i])
            if ancestor is not None and (
                ancestor.end_page is None or block.page_index > ancestor.end_page
            ):
                ancestor.end_page = block.page_index
    return sections


class IngestionService:
    def __init__(self, store: ArtifactStore, extractor: DoclingExtractor) -> None:
        self._store = store
        self._extractor = extractor

    async def ingest(
        self,
        conn: AsyncConnection,
        *,
        file_path: Path,
        metadata: IngestMetadata,
        dry_run: bool = False,
    ) -> IngestReport:
        source_type, extraction_path = self._resolve_source(file_path, metadata)
        sha256 = sha256_of_file(file_path)

        # Correções R5-02/R5-03: a proveniência do OCR é verificada
        # incondicionalmente aqui — antes de qualquer branch de retorno
        # (edição existente OU dry-run). Nem a deduplicação nem `--dry-run`
        # podem aceitar um `ocr_artifact` sem proveniência embutida,
        # adulterado ou de outro livro.
        ocr_provenance: OcrProvenance | None = None
        ocr_sha256: Sha256 | None = None
        if source_type is SourceType.PDF_SCAN:
            ocr_sha256, ocr_provenance = self._verify_ocr_hashes(metadata, sha256)

        editions = EditionsRepository(conn)
        works = WorksRepository(conn)
        existing = await editions.get_by_source_hash(sha256)
        if existing is not None:
            existing_work = await works.get(existing.work_id)
            self._assert_coherent(existing, existing_work, metadata, source_type, ocr_sha256)
            return await self._report(
                conn, existing, sha256, source_type, created=False, dry_run=dry_run
            )

        canonical = self._extractor.extract(extraction_path, self._extraction_type(source_type))

        if ocr_provenance is not None and ocr_provenance.pages != len(canonical.pages):
            raise IngestionError(
                "Contagem de páginas do derivado OCR diverge da proveniência registrada.",
                context={
                    "proveniencia_paginas": str(ocr_provenance.pages),
                    "extraido_paginas": str(len(canonical.pages)),
                },
            )

        if dry_run:
            return IngestReport(
                edition_id=None,
                work_id=None,
                source_sha256=sha256,
                source_type=source_type,
                created=False,
                dry_run=True,
                sections=sum(1 for b in canonical.blocks if b.kind is BlockKind.HEADING),
                pages=len(canonical.pages),
                blocks=len(canonical.blocks),
                warnings=canonical.warnings,
            )

        with file_path.open("rb") as fh:
            self._store.put(fh, sha256, original_filename=file_path.name)
        ocr_ref: DerivedArtifactRef | None = None
        if source_type is SourceType.PDF_SCAN:
            if ocr_provenance is None or ocr_sha256 is None:  # verificado acima; nunca ocorre
                raise IngestionError("Proveniência do OCR ausente para pdf_scan.")
            ocr_ref = self._finalize_ocr_derivative(metadata, sha256, ocr_sha256, ocr_provenance)

        async with conn.transaction():
            work = await works.find_by_identity(metadata.title, list(metadata.authors))
            if work is None:
                work = await works.create(
                    Work(
                        canonical_title=metadata.title,
                        original_title=metadata.original_title,
                        authors=[
                            Contributor(name=name, ordinal=i)
                            for i, name in enumerate(metadata.authors)
                        ],
                        language=metadata.language,
                    )
                )
            edition = await editions.create(
                Edition(
                    work_id=work.id,
                    title=metadata.title,
                    source_type=source_type,
                    source_sha256=sha256,
                    publisher=metadata.publisher,
                    publication_year=metadata.publication_year,
                    isbn=metadata.isbn,
                    edition_label=metadata.edition_label,
                    license_status=metadata.license_status,
                    derived_artifacts=[ocr_ref] if ocr_ref else [],
                    extraction_warnings=canonical.warnings,
                    canonical_fingerprint=canonical.fingerprint(),
                )
            )
            sections = derive_sections(edition.id, canonical)
            await SectionsRepository(conn).create_many(sections)
            await PagesRepository(conn).create_many(
                [
                    Page.create(
                        edition_id=edition.id,
                        physical_index=page.physical_index,
                        text=page.text,
                        printed_label=page.printed_label,
                    )
                    for page in canonical.pages
                ]
            )
            await editions.update_ingestion_status(edition.id, IngestionStatus.EXTRACTED)

        return IngestReport(
            edition_id=str(edition.id),
            work_id=str(work.id),
            source_sha256=sha256,
            source_type=source_type,
            created=True,
            dry_run=False,
            sections=len(sections),
            pages=len(canonical.pages),
            blocks=len(canonical.blocks),
            warnings=canonical.warnings,
        )

    def _resolve_source(self, file_path: Path, metadata: IngestMetadata) -> tuple[SourceType, Path]:
        if not file_path.is_file():
            raise IngestionError(
                "Arquivo de origem não encontrado.", context={"name": file_path.name}
            )
        suffix = file_path.suffix.lower()
        allowed = _VALID_SOURCE_TYPES_BY_SUFFIX.get(suffix)
        if allowed is None:
            raise IngestionError(
                "Extensão não suportada: use .pdf ou .epub.",
                context={"suffix": file_path.suffix},
            )
        source_type = metadata.source_type
        if source_type is None:
            source_type = SourceType.EPUB if suffix == ".epub" else SourceType.PDF_TEXT
        if source_type not in allowed:
            raise IngestionError(
                "Combinação inválida entre extensão do arquivo e source_type.",
                context={
                    "suffix": file_path.suffix,
                    "source_type": str(source_type),
                    "permitido": ",".join(str(s) for s in sorted(allowed, key=str)),
                },
            )
        if source_type is SourceType.PDF_SCAN:
            if metadata.ocr_artifact is None:
                raise IngestionError(
                    "PDF escaneado exige 'ocr_artifact' nos metadados: execute "
                    "'rag ocr' antes de ingerir."
                )
            if not metadata.ocr_artifact.is_file():
                raise IngestionError(
                    "Artefato OCR não encontrado.",
                    context={"name": metadata.ocr_artifact.name},
                )
            return source_type, metadata.ocr_artifact
        if metadata.ocr_artifact is not None:
            raise IngestionError("'ocr_artifact' só é válido com source_type=pdf_scan.")
        return source_type, file_path

    @staticmethod
    def _extraction_type(source_type: SourceType) -> SourceType:
        # PDF escaneado é extraído do derivado OCR, que tem camada de texto.
        return SourceType.PDF_TEXT if source_type is SourceType.PDF_SCAN else source_type

    @staticmethod
    def _verify_ocr_hashes(
        metadata: IngestMetadata, original_sha256: Sha256
    ) -> tuple[Sha256, OcrProvenance]:
        """Valida a proveniência embutida no derivado OCR (T5-04; redesenhado
        em R6-01 — proveniência dentro do PDF, não em sidecar separado).

        Roda incondicionalmente para `source_type=pdf_scan`, antes de
        qualquer branch de retorno (correções R5-02/R5-03): o artefato só é
        aceito se sua proveniência confirma que foi produzido a partir do
        MESMO arquivo original sendo ingerido agora (`input_sha256`) —
        impedindo que um derivado de outro livro seja associado a esta
        edição. Não valida contagem de páginas aqui: isso exige extração,
        que não roda no caminho de edição já existente. Retorna também o
        sha256 do próprio artefato (identidade no content store).
        """
        if metadata.ocr_artifact is None:  # garantido por _resolve_source
            raise IngestionError("Artefato OCR ausente para pdf_scan.")
        ocr_path = metadata.ocr_artifact
        provenance = load_provenance(ocr_path)
        if provenance.input_sha256 != original_sha256:
            raise IngestionError(
                "O artefato OCR não foi produzido a partir deste arquivo original "
                "(proveniência aponta para outro livro).",
                context={"name": ocr_path.name},
            )
        return sha256_of_file(ocr_path), provenance

    def _finalize_ocr_derivative(
        self,
        metadata: IngestMetadata,
        original_sha256: Sha256,
        ocr_sha256: Sha256,
        provenance: OcrProvenance,
    ) -> DerivedArtifactRef:
        """Publica o derivado já validado por `_verify_ocr_hashes` no artifact
        store e monta a referência persistida."""
        if metadata.ocr_artifact is None:  # garantido por _resolve_source
            raise IngestionError("Artefato OCR ausente para pdf_scan.")
        ocr_path = metadata.ocr_artifact
        with ocr_path.open("rb") as fh:
            self._store.put(fh, ocr_sha256, original_filename=ocr_path.name)
        return DerivedArtifactRef(
            sha256=ocr_sha256,
            kind=ArtifactKind.OCR_TEXT_LAYER,
            derived_from=original_sha256,
            generator=f"{provenance.engine}:{provenance.adapter_version}",
        )

    @staticmethod
    def _assert_coherent(
        existing: Edition,
        existing_work: Work | None,
        metadata: IngestMetadata,
        source_type: SourceType,
        ocr_sha256: Sha256 | None,
    ) -> None:
        """Reexecução com mesmo hash mas metadados divergentes falha fechado.

        Compara toda a identidade imutável da ingestão: obra/autores, dados
        bibliográficos, licença, idioma e o contrato de source_type — não
        apenas título/edição/isbn (T5-03). Para `pdf_scan`, exige também que
        o `ocr_artifact` informado seja EXATAMENTE (mesmo sha256) um dos
        derivados já registrados na edição — reingestão não pode trocar
        silenciosamente o derivado por outro, proveniência ausente/adulterada
        ou artefato de outro livro (R5-03; `_verify_ocr_hashes` já garantiu
        que a proveniência aponta para este original antes de chegar aqui).
        Divergência em qualquer campo impede que a repetição seja tratada
        como equivalente.
        """
        divergent = []
        if existing.title != metadata.title:
            divergent.append("title")
        if existing.source_type != source_type:
            divergent.append("source_type")
        if (existing.publisher or None) != (metadata.publisher or None):
            divergent.append("publisher")
        if existing.publication_year != metadata.publication_year:
            divergent.append("publication_year")
        if (existing.edition_label or None) != (metadata.edition_label or None):
            divergent.append("edition_label")
        if (existing.isbn or None) != (metadata.isbn or None):
            divergent.append("isbn")
        if existing.license_status != metadata.license_status:
            divergent.append("license_status")
        if existing_work is None:
            divergent.append("work")
        else:
            if (existing_work.original_title or None) != (metadata.original_title or None):
                divergent.append("original_title")
            if existing_work.language != metadata.language:
                divergent.append("language")
            existing_authors = tuple(
                c.name for c in sorted(existing_work.authors, key=lambda c: c.ordinal)
            )
            if existing_authors != tuple(metadata.authors):
                divergent.append("authors")
        if source_type is SourceType.PDF_SCAN:
            known_hashes = {d.sha256 for d in existing.derived_artifacts}
            if ocr_sha256 is None or ocr_sha256 not in known_hashes:
                divergent.append("ocr_artifact")
        if divergent:
            raise ConflictError(
                "Arquivo já ingerido com metadados divergentes.",
                context={"fields": ",".join(divergent)},
            )

    @staticmethod
    async def _report(
        conn: AsyncConnection,
        existing: Edition,
        sha256: Sha256,
        source_type: SourceType,
        *,
        created: bool,
        dry_run: bool,
    ) -> IngestReport:
        sections = await SectionsRepository(conn).list_by_edition(existing.id)
        pages = await PagesRepository(conn).list_by_edition(existing.id)
        return IngestReport(
            edition_id=str(existing.id),
            work_id=str(existing.work_id),
            source_sha256=sha256,
            source_type=source_type,
            created=created,
            dry_run=dry_run,
            sections=len(sections),
            pages=len(pages),
            blocks=0,
            warnings=existing.extraction_warnings,
        )
