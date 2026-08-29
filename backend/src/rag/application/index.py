"""Serviço de indexação: chunking + embeddings em lote (T06; SPEC §7.3).

Reextrai o documento (T05 só persiste `Section`/`Page`, não o detalhe de
bloco — ver NOTES.md §10.6 item 1), gera os chunks pai/filho
(`rag.domain.chunking`), casa cada um com `Section`/`Page` já persistidos,
gera embeddings em lote para os filhos e persiste tudo numa única transação.
Nada é publicado parcialmente: dimensão de embedding inesperada falha antes
de qualquer INSERT.
"""

import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from rag.adapters.docling_adapter import DoclingExtractor
from rag.domain.canonical import CanonicalDocument
from rag.domain.chunking import ChunkingParams, chunk_document, default_section_header
from rag.domain.enums import ArtifactKind, IngestionStatus, SourceType
from rag.domain.errors import EmbeddingDimensionError, IngestionError, NotFoundError
from rag.domain.library import Edition, Passage, Work
from rag.domain.providers import EmbeddingProvider
from rag.domain.versions import ChunkingVersion, EmbeddingVersion, utcnow
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_EXTRACTABLE_BY_SOURCE_TYPE = {
    SourceType.PDF_TEXT: SourceType.PDF_TEXT,
    SourceType.EPUB: SourceType.EPUB,
    # PDF escaneado é reextraído do derivado OCR, que tem camada de texto
    # (mesma regra de `_extraction_type` em application/ingest.py).
    SourceType.PDF_SCAN: SourceType.PDF_TEXT,
}


class IndexReport(BaseModel):
    """Resultado da indexação. Nunca contém texto do livro."""

    model_config = ConfigDict(frozen=True)

    edition_id: str
    created: bool
    forced: bool
    parents: int
    children: int
    chunking_version_id: str
    embedding_version_id: str


class IndexingService:
    def __init__(
        self,
        store: ArtifactStore,
        extractor: DoclingExtractor,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._store = store
        self._extractor = extractor
        self._embedding_provider = embedding_provider

    async def index_edition(
        self,
        conn: AsyncConnection,
        *,
        edition_id: UUID,
        chunking_params: ChunkingParams,
        embedding_model_name: str,
        embedding_dimensions: int,
        force: bool = False,
    ) -> IndexReport:
        editions = EditionsRepository(conn)
        edition = await editions.get(edition_id)
        if edition is None:
            raise NotFoundError("Edição não encontrada.", context={"edition_id": str(edition_id)})
        work = await WorksRepository(conn).get(edition.work_id)
        if work is None:  # pragma: no cover - integridade referencial garante isto
            raise NotFoundError("Obra da edição não encontrada.")

        passages_repo = PassagesRepository(conn)
        existing = await passages_repo.list_by_edition(edition.id)
        if existing and not force:
            return self._idempotent_report(edition, existing)

        canonical = self._reextract(edition)
        nodes = chunk_document(canonical, chunking_params)
        if not nodes:
            raise IngestionError(
                "Nenhum chunk gerado: documento sem parágrafos indexáveis.",
                context={"edition_id": str(edition.id)},
            )

        versions = VersionsRepository(conn)
        chunking_version = await versions.get_or_create(
            ChunkingVersion(
                label="structural-chunker", params=chunking_params.model_dump(), created_at=utcnow()
            )
        )
        embedding_version = await versions.get_or_create(
            EmbeddingVersion(
                label=embedding_model_name,
                model_name=embedding_model_name,
                dimensions=embedding_dimensions,
                created_at=utcnow(),
            )
        )

        child_nodes = [n for n in nodes if n.parent_index is not None]
        embeddings = await self._embedding_provider.embed_documents([n.text for n in child_nodes])
        if len(embeddings) != len(child_nodes):
            raise EmbeddingDimensionError(
                "Quantidade de embeddings retornada não corresponde à quantidade de chunks-filho.",
                context={"esperado": str(len(child_nodes)), "recebido": str(len(embeddings))},
            )
        for vec in embeddings:
            if len(vec) != embedding_version.dimensions:
                raise EmbeddingDimensionError(
                    "Dimensão do embedding retornado diverge da versão registrada.",
                    context={
                        "esperado": str(embedding_version.dimensions),
                        "recebido": str(len(vec)),
                    },
                )
        embedding_by_child_index = dict(
            zip((n.index for n in child_nodes), embeddings, strict=True)
        )

        sections = await SectionsRepository(conn).list_by_edition(edition.id)
        pages = await PagesRepository(conn).list_by_edition(edition.id)
        section_by_path = {tuple(s.path): s for s in sections}
        page_by_physical_index = {p.physical_index: p for p in pages}

        parents_created = 0
        children_created = 0
        async with conn.transaction():
            if force and existing:
                await passages_repo.delete_by_edition(edition.id)
            id_by_node_index: dict[int, UUID] = {}
            for node in nodes:
                section = section_by_path.get(node.section_path)
                page_start = (
                    page_by_physical_index.get(node.page_start_index)
                    if node.page_start_index is not None
                    else None
                )
                page_end = (
                    page_by_physical_index.get(node.page_end_index)
                    if node.page_end_index is not None
                    else None
                )
                is_child = node.parent_index is not None
                passage = Passage(
                    edition_id=edition.id,
                    ordinal=node.index,
                    text=node.text,
                    token_count=node.token_count,
                    chunking_version_id=chunking_version.id,
                    section_id=section.id if section is not None else None,
                    page_start_id=page_start.id if page_start is not None else None,
                    page_end_id=page_end.id if page_end is not None else None,
                    char_start=node.char_start,
                    char_end=node.char_end,
                    context_header=self._context_header(work, edition, node.section_path),
                    parent_passage_id=(
                        id_by_node_index[node.parent_index]
                        if node.parent_index is not None
                        else None
                    ),
                    embedding_version_id=embedding_version.id if is_child else None,
                )
                embedding = embedding_by_child_index[node.index] if is_child else None
                created = await passages_repo.create(passage, embedding)
                id_by_node_index[node.index] = created.id
                if is_child:
                    children_created += 1
                else:
                    parents_created += 1
            await editions.update_ingestion_status(edition.id, IngestionStatus.INDEXED)

        return IndexReport(
            edition_id=str(edition.id),
            created=True,
            forced=force and bool(existing),
            parents=parents_created,
            children=children_created,
            chunking_version_id=str(chunking_version.id),
            embedding_version_id=str(embedding_version.id),
        )

    def _reextract(self, edition: Edition) -> CanonicalDocument:
        extraction_type = _EXTRACTABLE_BY_SOURCE_TYPE[edition.source_type]
        sha256 = edition.source_sha256
        if edition.source_type is SourceType.PDF_SCAN:
            ocr_ref = next(
                (d for d in edition.derived_artifacts if d.kind is ArtifactKind.OCR_TEXT_LAYER),
                None,
            )
            if ocr_ref is None:
                raise IngestionError(
                    "Edição pdf_scan sem derivado OCR registrado.",
                    context={"edition_id": str(edition.id)},
                )
            sha256 = ocr_ref.sha256
        suffix = ".epub" if edition.source_type is SourceType.EPUB else ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            with self._store.open_stream(sha256) as stream:
                shutil.copyfileobj(stream, tmp)
            tmp.flush()
            return self._extractor.extract(Path(tmp.name), extraction_type)

    @staticmethod
    def _context_header(work: Work, edition: Edition, section_path: tuple[str, ...]) -> str:
        parts = [work.canonical_title]
        if edition.edition_label:
            parts.append(edition.edition_label)
        header = " — ".join(parts)
        section_part = default_section_header(section_path)
        if section_part:
            header = f"{header}. {section_part}"
        return header[:1000]

    @staticmethod
    def _idempotent_report(edition: Edition, existing: list[Passage]) -> IndexReport:
        embedding_version_id = next(
            (p.embedding_version_id for p in existing if p.embedding_version_id is not None), None
        )
        return IndexReport(
            edition_id=str(edition.id),
            created=False,
            forced=False,
            parents=sum(1 for p in existing if p.embedding_version_id is None),
            children=sum(1 for p in existing if p.embedding_version_id is not None),
            chunking_version_id=str(existing[0].chunking_version_id),
            embedding_version_id=str(embedding_version_id) if embedding_version_id else "",
        )
