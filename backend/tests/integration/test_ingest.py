"""Serviço de ingestão ponta a ponta contra PostgreSQL real (T05; AC-01, AC-02).

Extração EPUB usa o conversor real (offline, parse de HTML). Para PDF, o
conversor é injetado com o backend pypdfium (sem modelo de layout), mantendo
os testes determinísticos; o pipeline padrão com modelo é coberto pelo e2e
opcional em test_docling_adapter.py.
"""

import sys
from pathlib import Path

import pytest
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub, make_text_pdf

from rag.application.ingest import IngestionService, IngestReport, load_metadata
from rag.domain.enums import IngestionStatus, SourceType
from rag.domain.errors import ConflictError, IngestionError
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import (
    PagesRepository,
    SectionsRepository,
)
from rag.infrastructure.repositories.editions import EditionsRepository

pytestmark = pytest.mark.integration


def _extractor_pdf_no_model() -> DocumentConverter:
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(backend=PyPdfiumDocumentBackend)}
    )


def _write_metadata(path: Path, *, title: str = "Livro Fixture", extra: str = "") -> Path:
    path.write_text(
        f"title: {title}\nauthors: [Autor Fixture]\nedition_label: 1ª ed.\n{extra}",
        encoding="utf-8",
    )
    return path


def _service(tmp_path: Path, converter: DocumentConverter | None = None) -> IngestionService:
    from rag.adapters.docling_adapter import DoclingExtractor

    return IngestionService(
        ArtifactStore(tmp_path / "artifacts"),
        DoclingExtractor(converter=converter),
    )


async def _ingest_epub(
    db: Database, tmp_path: Path, *, title: str = "Livro Fixture", variant: int = 1
) -> IngestReport:
    epub = tmp_path / f"livro{variant}.epub"
    epub.write_bytes(
        make_epub(
            [
                ("Capítulo I", ["Primeira lição do livro.", "Segunda linha."]),
                ("Capítulo II", [f"Conteúdo da variante {variant}."]),
            ]
        )
    )
    meta = load_metadata(_write_metadata(tmp_path / f"livro{variant}.yaml", title=title))
    service = _service(tmp_path)
    async with db.connection() as conn:
        return await service.ingest(conn, file_path=epub, metadata=meta)


class TestIngestEpub:
    async def test_persists_edition_sections_and_status(self, db: Database, tmp_path: Path) -> None:
        report = await _ingest_epub(db, tmp_path)
        assert report.created
        assert not report.dry_run
        assert report.sections == 2
        assert report.pages == 0  # EPUB não tem páginas físicas
        assert report.blocks == 5  # 2 headings + 3 parágrafos

        async with db.connection() as conn:
            editions = EditionsRepository(conn)
            edition = await editions.get_by_source_hash(report.source_sha256)
            assert edition is not None
            assert edition.ingestion_status is IngestionStatus.EXTRACTED
            assert edition.source_type is SourceType.EPUB
            sections = await SectionsRepository(conn).list_by_edition(edition.id)
            assert [s.title for s in sections] == ["Capítulo I", "Capítulo II"]

    async def test_reingest_is_idempotent(self, db: Database, tmp_path: Path) -> None:
        """AC-01: mesmo arquivo + metadados não duplica a edição."""
        first = await _ingest_epub(db, tmp_path)
        second = await _ingest_epub(db, tmp_path)
        assert first.created
        assert not second.created
        assert first.edition_id == second.edition_id
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM editions")
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 1

    async def test_divergent_metadata_same_file_conflicts(
        self, db: Database, tmp_path: Path
    ) -> None:
        await _ingest_epub(db, tmp_path)
        epub = tmp_path / "livro1.epub"
        meta = load_metadata(_write_metadata(tmp_path / "outro.yaml", title="Título Divergente"))
        service = _service(tmp_path)
        async with db.connection() as conn:
            with pytest.raises(ConflictError, match="divergentes"):
                await service.ingest(conn, file_path=epub, metadata=meta)

    async def test_dry_run_validates_without_persisting(self, db: Database, tmp_path: Path) -> None:
        epub = tmp_path / "livro.epub"
        epub.write_bytes(make_epub([("Capítulo I", ["Texto."])]))
        meta = load_metadata(_write_metadata(tmp_path / "livro.yaml"))
        service = _service(tmp_path)
        async with db.connection() as conn:
            report = await service.ingest(conn, file_path=epub, metadata=meta, dry_run=True)
        assert report.dry_run
        assert not report.created
        assert report.edition_id is None
        assert report.sections == 1
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM editions")
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 0
        assert not (tmp_path / "artifacts" / "objects").exists() or not list(
            (tmp_path / "artifacts" / "objects").rglob("*")
        )

    async def test_two_editions_share_one_work(self, db: Database, tmp_path: Path) -> None:
        """AC-02: duas edições da mesma obra — distinguíveis, mesmo Work."""
        first = await _ingest_epub(db, tmp_path, variant=1)
        second = await _ingest_epub(db, tmp_path, variant=2)
        assert first.edition_id != second.edition_id
        assert first.work_id == second.work_id


class TestAtomicity:
    async def test_partial_failure_publishes_nothing(
        self, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Erro no meio da transação não publica edição (SPEC §7.1)."""
        from rag.infrastructure.repositories import content

        async def boom(_self: object, _pages: list[object]) -> None:
            raise RuntimeError("falha simulada na gravação de páginas")

        monkeypatch.setattr(content.PagesRepository, "create_many", boom)

        epub = tmp_path / "livro.epub"
        epub.write_bytes(make_epub([("Capítulo I", ["Texto."])]))
        meta = load_metadata(_write_metadata(tmp_path / "livro.yaml"))
        service = _service(tmp_path)
        async with db.connection() as conn:
            with pytest.raises(RuntimeError, match="falha simulada"):
                await service.ingest(conn, file_path=epub, metadata=meta)

        async with db.connection() as conn, conn.cursor() as cur:
            for table in ("editions", "sections", "pages", "works"):
                assert table in ("editions", "sections", "pages", "works")
                await cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
                row = await cur.fetchone()
                assert row is not None, table
                assert row[0] == 0, table


class TestPdfScan:
    async def test_scan_without_ocr_artifact_fails(self, db: Database, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        meta = load_metadata(
            _write_metadata(tmp_path / "scan.yaml", extra="source_type: pdf_scan\n")
        )
        service = _service(tmp_path)
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="rag ocr"):
                await service.ingest(conn, file_path=scan, metadata=meta)

    async def test_scan_ingest_preserves_original_identity(
        self, db: Database, tmp_path: Path
    ) -> None:
        """Contrato OCR (§10.1.5): edição identifica a varredura original; o
        PDF com texto é artefato derivado que referencia o original."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))  # varredura sem camada de texto
        ocr = tmp_path / "scan_ocr.pdf"
        ocr.write_bytes(make_text_pdf([["CAPITULO I", "Texto reconhecido por OCR."]]))
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            report = await service.ingest(conn, file_path=scan, metadata=meta)

        from rag.application.ingest import sha256_of_file

        assert report.created
        assert report.source_type is SourceType.PDF_SCAN
        assert report.source_sha256 == sha256_of_file(scan)  # hash do ORIGINAL

        async with db.connection() as conn:
            edition = await EditionsRepository(conn).get_by_source_hash(sha256_of_file(scan))
            assert edition is not None
            assert len(edition.derived_artifacts) == 1
            derived = edition.derived_artifacts[0]
            assert derived.sha256 == sha256_of_file(ocr)
            assert derived.derived_from == sha256_of_file(scan)
            pages = await PagesRepository(conn).list_by_edition(edition.id)
            assert len(pages) == 1  # texto veio do derivado OCR
            assert "OCR" in pages[0].text
