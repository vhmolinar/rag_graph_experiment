"""Serviço de ingestão ponta a ponta contra PostgreSQL real (T05; AC-01, AC-02).

Extração EPUB usa o conversor real (offline, parse de HTML). Para PDF, o
conversor é injetado com o backend pypdfium (sem modelo de layout), mantendo
os testes determinísticos; o pipeline padrão com modelo é coberto pelo e2e
opcional em test_docling_adapter.py.
"""

import io
import os
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub, make_scanned_pdf, make_scanned_pdf_with_text, make_text_pdf

from rag.adapters.ocr_adapter import (
    _PROVENANCE_ATTACHMENT_NAME,
    DoclingOcrEngine,
    load_provenance,
    ocr_pdf,
)
from rag.adapters.pdf_writer import OcrLine, OcrPage, write_text_layer_pdf
from rag.application.ingest import IngestionService, IngestReport, load_metadata
from rag.domain.enums import IngestionStatus, SourceType
from rag.domain.errors import ConflictError, IngestionError
from rag.domain.identifiers import sha256_of_file
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import (
    PagesRepository,
    SectionsRepository,
)
from rag.infrastructure.repositories.editions import EditionsRepository

pytestmark = pytest.mark.integration


def _tamper_provenance_pages(pdf_path: Path, new_pages: int) -> None:
    """Substitui a proveniência embutida por uma com `pages` divergente,
    mantendo o restante do PDF intacto — usado para testar rejeição por
    contagem de páginas divergente (T5-04, redesenhado em R6-01: a
    proveniência agora vive DENTRO do PDF, não em um sidecar separado)."""
    tampered = load_provenance(pdf_path).model_copy(update={"pages": new_pages})
    document = pdfium.PdfDocument(str(pdf_path))
    for index in range(document.count_attachments()):
        attachment = document.get_attachment(index)
        if attachment.get_name() == _PROVENANCE_ATTACHMENT_NAME:
            attachment.set_data(tampered.model_dump_json(indent=2).encode("utf-8"))
            break
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    pdf_path.write_bytes(buffer.getvalue())


class _StubOcrEngine:
    """Motor stub para testes de proveniência do OCR (T5-04)."""

    name = "stub"
    version = "stub-0"

    def __init__(self, text: str = "Texto reconhecido por OCR.") -> None:
        self._text = text

    def recognize(self, pdf_path: Path) -> list[OcrPage]:
        page_count = len(pdfium.PdfDocument(str(pdf_path)))
        return [
            OcrPage(
                physical_index=i,
                width=612.0,
                height=792.0,
                lines=(OcrLine(text=self._text, x=72.0, y=700.0, height=12.0),) if i == 0 else (),
            )
            for i in range(page_count)
        ]


def _extractor_pdf_no_model() -> DocumentConverter:
    # do_ocr=False: determinístico e sem RapidOCR (evita alucinação de OCR sobre
    # figuras sintéticas nos testes de proveniência do PDF escaneado).
    opts = PdfPipelineOptions(do_ocr=False)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts, backend=PyPdfiumDocumentBackend)
        }
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

    @pytest.mark.parametrize(
        ("field", "extra"),
        [
            ("authors", "authors: [Outro Autor]\n"),
            ("publisher", "publisher: Outra Editora\n"),
            ("publication_year", "publication_year: 1999\n"),
            ("license_status", "license_status: public_domain\n"),
            ("original_title", "original_title: Outro Título Original\n"),
        ],
        ids=["authors", "publisher", "publication_year", "license_status", "original_title"],
    )
    async def test_divergent_metadata_field_conflicts(
        self, db: Database, tmp_path: Path, field: str, extra: str
    ) -> None:
        """T5-03: coerência cobre toda a identidade, não só title/edition/isbn."""
        await _ingest_epub(db, tmp_path)
        epub = tmp_path / "livro1.epub"
        meta = load_metadata(_write_metadata(tmp_path / "outro.yaml", extra=extra))
        service = _service(tmp_path)
        async with db.connection() as conn:
            with pytest.raises(ConflictError, match="divergentes") as exc_info:
                await service.ingest(conn, file_path=epub, metadata=meta)
        assert field in exc_info.value.context["fields"]

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
        scan.write_bytes(make_scanned_pdf(1))
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
        PDF com texto é artefato derivado que referencia o original. O
        derivado vem do pipeline real `rag ocr` (com sidecar de proveniência),
        não de um PDF montado à mão (T5-05)."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))  # varredura sem camada de texto
        ocr = tmp_path / "scan_ocr.pdf"
        ocr_pdf(_StubOcrEngine("Texto reconhecido por OCR."), scan, ocr)
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            report = await service.ingest(conn, file_path=scan, metadata=meta)

        from rag.domain.identifiers import sha256_of_file

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
            assert derived.generator == "stub:stub-0"  # engine/versão real (T5-04)
            pages = await PagesRepository(conn).list_by_edition(edition.id)
            assert len(pages) == 1  # texto veio do derivado OCR
            assert "OCR" in pages[0].text

    async def test_ocr_artifact_without_provenance_rejected(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T5-04: derivado gravado sem passar por `rag ocr` (sem proveniência
        embutida) falha fechado."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        ocr = tmp_path / "scan_ocr.pdf"
        write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=612.0,
                    height=792.0,
                    lines=(OcrLine("Texto.", 72.0, 700.0, 12.0),),
                )
            ],
            ocr,
            scan,
        )
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="proveniência"):
                await service.ingest(conn, file_path=scan, metadata=meta)

    async def test_ocr_artifact_from_different_original_rejected(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T5-04: um artefato de outro livro não pode ser associado à edição."""
        scan_a = tmp_path / "a.pdf"
        scan_a.write_bytes(make_scanned_pdf(1))
        scan_b = tmp_path / "b.pdf"
        scan_b.write_bytes(make_scanned_pdf(1))
        ocr = tmp_path / "a_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), scan_a, ocr)  # proveniência aponta para scan_a
        meta = load_metadata(
            _write_metadata(
                tmp_path / "b.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="outro livro"):
                await service.ingest(conn, file_path=scan_b, metadata=meta)

    async def test_ocr_artifact_page_count_mismatch_rejected(
        self, db: Database, tmp_path: Path
    ) -> None:
        """T5-04: contagem de páginas divergente da proveniência é rejeitada."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(2))
        ocr = tmp_path / "scan_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), scan, ocr)
        _tamper_provenance_pages(ocr, 99)
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="Contagem de páginas"):
                await service.ingest(conn, file_path=scan, metadata=meta)

    async def test_source_type_divergence_rejected(self, db: Database, tmp_path: Path) -> None:
        """T5-03: reingestão do mesmo arquivo declarando outro source_type conflita."""
        pdf = tmp_path / "livro.pdf"
        pdf.write_bytes(make_text_pdf([["CAPITULO I", "Texto normal."]]))
        meta = load_metadata(_write_metadata(tmp_path / "livro.yaml"))
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            first = await service.ingest(conn, file_path=pdf, metadata=meta)
        assert first.created

        ocr = tmp_path / "livro_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), pdf, ocr)  # input_sha256 = hash do MESMO pdf
        meta2 = load_metadata(
            _write_metadata(
                tmp_path / "livro2.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        async with db.connection() as conn:
            with pytest.raises(ConflictError, match="divergentes") as exc_info:
                await service.ingest(conn, file_path=pdf, metadata=meta2)
        assert "source_type" in exc_info.value.context["fields"]

    async def test_reingest_with_same_derivative_is_idempotent(
        self, db: Database, tmp_path: Path
    ) -> None:
        """R5-03: reingestão com o MESMO derivado (mesmo sha256) permanece
        idempotente — a exigência de equivalência não deve quebrar o
        caminho legítimo de repetição (AC-01)."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        ocr = tmp_path / "scan_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), scan, ocr)
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            first = await service.ingest(conn, file_path=scan, metadata=meta)
            second = await service.ingest(conn, file_path=scan, metadata=meta)
        assert first.created
        assert not second.created
        assert first.edition_id == second.edition_id

    async def test_reingest_with_different_derivative_rejected(
        self, db: Database, tmp_path: Path
    ) -> None:
        """R5-03: reingestão trocando o derivado por outro (mesmo original,
        proveniência íntegra) ainda assim conflita — a edição já registrou UM
        derivado específico, e substituí-lo silenciosamente não é idempotência."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        ocr1 = tmp_path / "scan_ocr1.pdf"
        ocr_pdf(_StubOcrEngine("primeira versão"), scan, ocr1)
        meta1 = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr1) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            first = await service.ingest(conn, file_path=scan, metadata=meta1)
        assert first.created

        ocr2 = tmp_path / "scan_ocr2.pdf"
        ocr_pdf(_StubOcrEngine("segunda versão, outro reconhecimento"), scan, ocr2)
        meta2 = load_metadata(
            _write_metadata(
                tmp_path / "scan2.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr2) + "\n",
            )
        )
        async with db.connection() as conn:
            with pytest.raises(ConflictError, match="divergentes") as exc_info:
                await service.ingest(conn, file_path=scan, metadata=meta2)
        assert "ocr_artifact" in exc_info.value.context["fields"]


class TestPdfScanDryRun:
    """R5-02: `--dry-run` valida a proveniência do OCR, não só o resto do
    contrato — sem isso, um derivado inválido "passava" no dry-run."""

    async def _assert_nothing_persisted(self, db: Database) -> None:
        async with db.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM editions")
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 0

    async def test_dry_run_rejects_missing_provenance(self, db: Database, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        ocr = tmp_path / "scan_ocr.pdf"
        write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=612.0,
                    height=792.0,
                    lines=(OcrLine("Texto.", 72.0, 700.0, 12.0),),
                )
            ],
            ocr,
            scan,
        )
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="proveniência"):
                await service.ingest(conn, file_path=scan, metadata=meta, dry_run=True)
        await self._assert_nothing_persisted(db)

    async def test_dry_run_rejects_different_original(self, db: Database, tmp_path: Path) -> None:
        scan_a = tmp_path / "a.pdf"
        scan_a.write_bytes(make_scanned_pdf(1))
        scan_b = tmp_path / "b.pdf"
        scan_b.write_bytes(make_scanned_pdf(1))
        ocr = tmp_path / "a_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), scan_a, ocr)
        meta = load_metadata(
            _write_metadata(
                tmp_path / "b.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="outro livro"):
                await service.ingest(conn, file_path=scan_b, metadata=meta, dry_run=True)
        await self._assert_nothing_persisted(db)

    async def test_dry_run_rejects_page_count_mismatch(self, db: Database, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(2))
        ocr = tmp_path / "scan_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), scan, ocr)
        _tamper_provenance_pages(ocr, 99)
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="Contagem de páginas"):
                await service.ingest(conn, file_path=scan, metadata=meta, dry_run=True)
        await self._assert_nothing_persisted(db)

    async def test_dry_run_accepts_valid_provenance_without_persisting(
        self, db: Database, tmp_path: Path
    ) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        ocr = tmp_path / "scan_ocr.pdf"
        ocr_pdf(_StubOcrEngine(), scan, ocr)
        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            report = await service.ingest(conn, file_path=scan, metadata=meta, dry_run=True)
        assert report.dry_run
        assert not report.created
        await self._assert_nothing_persisted(db)


@pytest.mark.skipif(
    os.environ.get("RAG_OCR_E2E") != "1",
    reason="motor OCR real (rapidocr/torch) carrega modelo na 1ª execução; "
    "habilitar com RAG_OCR_E2E=1",
)
class TestOcrRealEngineToIngestE2E:
    """E2E opcional completo (correção R5-05): imagem raster com frase
    legível → `rag ocr` com motor real → `rag ingest` → texto e proveniência
    persistidos corretamente. Nenhum stub em nenhuma etapa."""

    async def test_scan_to_ingest_persists_recognized_text(
        self, db: Database, tmp_path: Path
    ) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf_with_text("Capitulo Um licao"))
        ocr = tmp_path / "scan_ocr.pdf"
        report = ocr_pdf(DoclingOcrEngine(engine="rapidocr"), scan, ocr)
        assert report.lines > 0

        meta = load_metadata(
            _write_metadata(
                tmp_path / "scan.yaml",
                extra="source_type: pdf_scan\nocr_artifact: " + str(ocr) + "\n",
            )
        )
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            ingest_report = await service.ingest(conn, file_path=scan, metadata=meta)
        assert ingest_report.created
        assert ingest_report.source_type is SourceType.PDF_SCAN

        async with db.connection() as conn:
            edition = await EditionsRepository(conn).get_by_source_hash(sha256_of_file(scan))
            assert edition is not None
            assert len(edition.derived_artifacts) == 1
            assert edition.derived_artifacts[0].sha256 == sha256_of_file(ocr)
            pages = await PagesRepository(conn).list_by_edition(edition.id)
        assert len(pages) == 1
        recognized_words = set(pages[0].text.split())
        for expected in ("Capitulo", "Um", "licao"):
            assert expected in recognized_words, pages[0].text


class TestExtensionSourceTypeMatrix:
    """T5-07: combinações inválidas entre extensão e source_type falham fechado."""

    async def test_epub_declared_as_pdf_text_rejected(self, db: Database, tmp_path: Path) -> None:
        epub = tmp_path / "livro.epub"
        epub.write_bytes(make_epub([("Capítulo I", ["Texto."])]))
        meta = load_metadata(
            _write_metadata(tmp_path / "livro.yaml", extra="source_type: pdf_text\n")
        )
        service = _service(tmp_path)
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="Combinação inválida"):
                await service.ingest(conn, file_path=epub, metadata=meta)

    async def test_pdf_declared_as_epub_rejected(self, db: Database, tmp_path: Path) -> None:
        pdf = tmp_path / "livro.pdf"
        pdf.write_bytes(make_text_pdf([["Texto."]]))
        meta = load_metadata(_write_metadata(tmp_path / "livro.yaml", extra="source_type: epub\n"))
        service = _service(tmp_path, converter=_extractor_pdf_no_model())
        async with db.connection() as conn:
            with pytest.raises(IngestionError, match="Combinação inválida"):
                await service.ingest(conn, file_path=pdf, metadata=meta)
