"""Serviço de indexação ponta a ponta contra PostgreSQL real (T06).

Ingestão real (T05) primeiro produz a edição + artefato no store; depois
`IndexingService` reextrai, chunkeia e indexa com um provedor de embeddings
fake (determinístico, sem rede).
"""

import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub, make_text_pdf

from rag.application.index import IndexingService
from rag.application.ingest import IngestionService, load_metadata
from rag.domain.chunking import ChunkingParams
from rag.domain.enums import IngestionStatus
from rag.domain.errors import EmbeddingDimensionError, NotFoundError
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

pytestmark = pytest.mark.integration


class _FakeEmbeddingProvider:
    """Determinístico, sem rede: um vetor distinto por texto, na ordem.

    Usa a dimensão fixa do schema (T03) — uma `EmbeddingVersion` com outra
    dimensão é rejeitada no cadastro (RR05), antes mesmo de chegar à
    validação própria de T06 (`_WrongDimensionEmbeddingProvider` abaixo
    testa especificamente essa segunda camada).
    """

    def __init__(self, dimensions: int = EMBEDDING_COLUMN_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i + 1)] * self.dimensions for i in range(len(texts))]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dimensions


class _WrongDimensionEmbeddingProvider:
    """Registra a `EmbeddingVersion` com a dimensão correta do schema, mas
    retorna vetores de OUTRA dimensão — exercita a checagem própria de T06
    (`len(vec) != embedding_version.dimensions`), distinta da checagem de
    schema (RR05) que age sobre o valor registrado, não o retornado."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0] for _ in texts]  # sempre dimensão 2, incompatível

    async def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0]


class _CountMismatchEmbeddingProvider:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # sempre 1 vetor, não importa quantos textos — dimensão certa, mas
        # quantidade errada.
        return [[0.0] * EMBEDDING_COLUMN_DIMENSIONS]

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * EMBEDDING_COLUMN_DIMENSIONS


def _write_metadata(path: Path, *, title: str = "Livro Fixture") -> Path:
    path.write_text(
        f"title: {title}\nauthors: [Autor Fixture]\nedition_label: 1ª ed.\n",
        encoding="utf-8",
    )
    return path


def _extractor_pdf_no_model() -> DocumentConverter:
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(backend=PyPdfiumDocumentBackend)}
    )


async def _ingest_epub(
    db: Database, tmp_path: Path, *, title: str = "Livro Fixture", variant: int = 1
) -> UUID:
    from rag.adapters.docling_adapter import DoclingExtractor

    epub = tmp_path / f"livro{variant}.epub"
    epub.write_bytes(
        make_epub(
            [
                ("Capítulo I", ["Primeira frase do capitulo um.", "Segunda frase do capitulo um."]),
                ("Capítulo II", [f"Primeira frase do capitulo dois, variante {variant}."]),
            ]
        )
    )
    meta = load_metadata(_write_metadata(tmp_path / f"livro{variant}.yaml", title=title))
    service = IngestionService(ArtifactStore(tmp_path / "artifacts"), DoclingExtractor())
    async with db.connection() as conn:
        report = await service.ingest(conn, file_path=epub, metadata=meta)
    return UUID(report.edition_id)


async def _ingest_pdf(db: Database, tmp_path: Path, *, title: str = "Livro Fixture") -> UUID:
    from rag.adapters.docling_adapter import DoclingExtractor

    pdf = tmp_path / "livro.pdf"
    pdf.write_bytes(
        make_text_pdf(
            [
                ["CAPITULO I", "Primeira frase do capitulo um.", "Segunda frase do capitulo um."],
                ["CAPITULO II", "Primeira frase do capitulo dois."],
            ]
        )
    )
    meta = load_metadata(_write_metadata(tmp_path / "livro.yaml", title=title))
    service = IngestionService(
        ArtifactStore(tmp_path / "artifacts"), DoclingExtractor(converter=_extractor_pdf_no_model())
    )
    async with db.connection() as conn:
        report = await service.ingest(conn, file_path=pdf, metadata=meta)
    return UUID(report.edition_id)


def _service(tmp_path: Path, embedding_provider: object) -> IndexingService:
    from rag.adapters.docling_adapter import DoclingExtractor

    return IndexingService(
        ArtifactStore(tmp_path / "artifacts"),
        DoclingExtractor(converter=_extractor_pdf_no_model()),
        embedding_provider,  # type: ignore[arg-type]
    )


class TestIndexEdition:
    async def test_indexes_epub_creates_parents_and_children(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            report = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert report.created
        assert report.parents == 2  # duas seções-folha (Capítulo I e II)
        assert report.children >= 2

        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert edition is not None
        assert edition.ingestion_status is IngestionStatus.INDEXED
        assert len(passages) == report.parents + report.children
        children = [p for p in passages if p.embedding_version_id is not None]
        parents = [p for p in passages if p.embedding_version_id is None]
        assert len(children) == report.children
        assert len(parents) == report.parents
        for child in children:
            assert child.parent_passage_id in {p.id for p in parents}
            assert child.section_id is not None
            # EPUB: sem páginas.
            assert child.page_start_id is None
            assert child.char_start is None

    async def test_indexes_pdf_with_page_offsets(self, db: Database, tmp_path: Path) -> None:
        edition_id = await _ingest_pdf(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            pages = await PagesRepository(conn).list_by_edition(edition_id)
        pages_by_id = {p.id: p for p in pages}
        for passage in passages:
            if passage.page_start_id is None:
                continue
            assert passage.char_start is not None
            assert passage.char_end is not None
            start_page = pages_by_id[passage.page_start_id]
            end_page = pages_by_id[passage.page_end_id]  # type: ignore[index]
            if passage.page_start_id == passage.page_end_id:
                assert start_page.text[passage.char_start : passage.char_end] == passage.text
            else:
                reconstructed = (
                    start_page.text[passage.char_start :] + "\n" + end_page.text[: passage.char_end]
                )
                assert reconstructed == passage.text

    async def test_reindex_without_force_is_idempotent(self, db: Database, tmp_path: Path) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            second = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert first.created
        assert not second.created
        assert len(provider.calls) == 1  # segunda chamada nem gerou embeddings

    async def test_force_reindexes_and_replaces_passages(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        provider = _FakeEmbeddingProvider()
        service = _service(tmp_path, provider)
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            forced = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                force=True,
            )
        assert first.created
        assert forced.created
        assert forced.forced
        assert len(provider.calls) == 2
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM passages WHERE edition_id = %s", (edition_id,))
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == forced.parents + forced.children

    async def test_different_chunking_params_create_new_version(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            first = await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        edition_id_2 = await _ingest_epub(db, tmp_path, title="Outro Livro", variant=2)
        async with db.connection() as conn:
            second = await service.index_edition(
                conn,
                edition_id=edition_id_2,
                chunking_params=ChunkingParams(child_target_tokens=999),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
        assert first.chunking_version_id != second.chunking_version_id

    async def test_embedding_dimension_mismatch_fails_before_persisting(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _WrongDimensionEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(EmbeddingDimensionError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            edition = await EditionsRepository(conn).get(edition_id)
        assert passages == []
        assert edition is not None
        assert edition.ingestion_status is not IngestionStatus.INDEXED

    async def test_embedding_count_mismatch_fails_before_persisting(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path)
        service = _service(tmp_path, _CountMismatchEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(EmbeddingDimensionError):
                await service.index_edition(
                    conn,
                    edition_id=edition_id,
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )
        async with db.connection() as conn:
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
        assert passages == []

    async def test_unknown_edition_raises_not_found(self, db: Database, tmp_path: Path) -> None:
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            with pytest.raises(NotFoundError):
                await service.index_edition(
                    conn,
                    edition_id=uuid4(),
                    chunking_params=ChunkingParams(),
                    embedding_model_name="fake-model",
                    embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
                )

    async def test_context_header_includes_work_and_section(
        self, db: Database, tmp_path: Path
    ) -> None:
        edition_id = await _ingest_epub(db, tmp_path, title="Titulo Distintivo")
        service = _service(tmp_path, _FakeEmbeddingProvider())
        async with db.connection() as conn:
            await service.index_edition(
                conn,
                edition_id=edition_id,
                chunking_params=ChunkingParams(),
                embedding_model_name="fake-model",
                embedding_dimensions=EMBEDDING_COLUMN_DIMENSIONS,
            )
            passages = await PassagesRepository(conn).list_by_edition(edition_id)
            sections = await SectionsRepository(conn).list_by_edition(edition_id)
        section_titles = {s.title for s in sections}
        for passage in passages:
            assert "Titulo Distintivo" in passage.context_header
            assert any(title in passage.context_header for title in section_titles if title)
            # o cabeçalho contextual nunca é o texto citável em si.
            assert passage.context_header != passage.text
