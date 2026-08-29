"""Adapter Docling → schema canônico (T05; SPEC §7.2; AC-03).

O núcleo do mapeamento (`_to_canonical`) é testado com DoclingDocument
construído programaticamente — determinístico, sem modelo de layout nem rede.
A conversão EPUB real é offline (parse de HTML) e exercita o caminho completo.
A conversão PDF real usa o backend pypdfium (sem modelo) para o caso de PDF
sem texto; o pipeline padrão com modelo de layout é coberto por teste e2e
opcional (RAG_DOCLING_E2E=1), pois exige download de modelo na primeira
execução.
"""

import os
import sys
from pathlib import Path

import pytest
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.base import BoundingBox
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableData
from docling_core.types.doc.labels import DocItemLabel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_epub, make_text_pdf

from rag.adapters.docling_adapter import DoclingExtractor, _to_canonical
from rag.domain.canonical import BlockKind
from rag.domain.enums import SourceType
from rag.domain.errors import IngestionError


def _prov(page_no: int, start: int = 0, end: int = 10) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=0, t=0, r=100, b=10),
        charspan=(start, end),
    )


def _book_doc() -> DoclingDocument:
    """Documento sintético: 2 capítulos, subseção, mobília e tabela."""
    doc = DoclingDocument(name="livro")
    doc.add_heading("Capítulo I", level=1, prov=_prov(1))
    doc.add_text(DocItemLabel.TEXT, "Primeira  lição\n do livro.", prov=_prov(1))
    doc.add_heading("Seção 1.1", level=2, prov=_prov(1))
    doc.add_text(DocItemLabel.TEXT, "Detalhe da seção.", prov=_prov(1))
    doc.add_text(DocItemLabel.PAGE_FOOTER, "rodapé da página", prov=_prov(1))
    doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[]), prov=_prov(1))
    doc.add_heading("Capítulo II", level=1, prov=_prov(2))
    doc.add_text(DocItemLabel.LIST_ITEM, "Item de lista.", prov=_prov(2))
    return doc


class TestMapping:
    def test_hierarchy_order_and_paths(self) -> None:
        doc = _to_canonical(_book_doc(), SourceType.PDF_TEXT)
        headings = [b for b in doc.blocks if b.kind is BlockKind.HEADING]
        assert [(b.level, b.text) for b in headings] == [
            (0, "Capítulo I"),
            (1, "Seção 1.1"),
            (0, "Capítulo II"),
        ]
        detail = next(b for b in doc.blocks if b.text == "Detalhe da seção.")
        assert detail.section_path == ("Capítulo I", "Seção 1.1")
        assert detail.level == 1
        item = next(b for b in doc.blocks if b.text == "Item de lista.")
        assert item.section_path == ("Capítulo II",)

    def test_pages_and_offsets_recompose_excerpt(self) -> None:
        doc = _to_canonical(_book_doc(), SourceType.PDF_TEXT)
        assert [p.physical_index for p in doc.pages] == [0, 1]
        assert doc.pages[0].printed_label == "1"
        for block in doc.blocks:
            assert block.page_index is not None
            page = doc.pages[block.page_index]
            assert page.text[block.char_start : block.char_end] == block.text

    def test_normalized_and_original_text_preserved(self) -> None:
        doc = _to_canonical(_book_doc(), SourceType.PDF_TEXT)
        lesson = next(b for b in doc.blocks if "lição" in b.text)
        assert lesson.text == "Primeira lição do livro."
        assert lesson.original_text == "Primeira  lição\n do livro."

    def test_furniture_skipped_and_table_warned(self) -> None:
        doc = _to_canonical(_book_doc(), SourceType.PDF_TEXT)
        assert all("rodapé" not in b.text for b in doc.blocks)
        assert any("tabela" in w for w in doc.warnings)

    def test_empty_extraction_fails_closed_with_ocr_guidance(self) -> None:
        with pytest.raises(IngestionError, match="rag ocr"):
            _to_canonical(DoclingDocument(name="vazio"), SourceType.PDF_TEXT)

    def test_unexpected_document_type_fails(self) -> None:
        with pytest.raises(IngestionError, match="inesperado"):
            _to_canonical(object(), SourceType.PDF_TEXT)


class TestRealConversions:
    def test_epub_end_to_end(self, tmp_path: Path) -> None:
        epub = tmp_path / "livro.epub"
        epub.write_bytes(
            make_epub(
                [
                    ("Capítulo I", ["Primeira lição do livro.", "Segunda linha."]),
                    ("Capítulo II", ["Outro capítulo aqui."]),
                ]
            )
        )
        doc = DoclingExtractor().extract(epub, SourceType.EPUB)
        assert doc.source_type is SourceType.EPUB
        assert doc.pages == ()
        headings = [b for b in doc.blocks if b.kind is BlockKind.HEADING]
        assert [(b.level, b.text) for b in headings] == [
            (0, "Capítulo I"),
            (0, "Capítulo II"),
        ]
        lesson = next(b for b in doc.blocks if "lição" in b.text)
        assert lesson.section_path == ("Capítulo I",)
        assert lesson.page_index is None
        assert lesson.char_start is None

    def test_scanned_pdf_fails_closed_without_models(self, tmp_path: Path) -> None:
        """PDF sem camada de texto (escaneado) → erro orientando OCR."""
        scanned = tmp_path / "escaneado.pdf"
        scanned.write_bytes(make_text_pdf([[]]))  # página sem texto
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(backend=PyPdfiumDocumentBackend)}
        )
        with pytest.raises(IngestionError, match="rag ocr"):
            DoclingExtractor(converter=converter).extract(scanned, SourceType.PDF_TEXT)

    def test_pdf_scan_source_type_rejected_before_conversion(self, tmp_path: Path) -> None:
        with pytest.raises(IngestionError, match="não extraível"):
            DoclingExtractor().extract(tmp_path / "x.pdf", SourceType.PDF_SCAN)

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.environ.get("RAG_DOCLING_E2E") != "1",
        reason="pipeline padrão baixa modelo de layout na 1ª execução; "
        "habilitar com RAG_DOCLING_E2E=1",
    )
    def test_pdf_text_with_layout_model(self, tmp_path: Path) -> None:
        """Integração real configurável: pipeline padrão (modelo de layout)."""
        pdf = tmp_path / "livro.pdf"
        pdf.write_bytes(
            make_text_pdf(
                [
                    ["CAPITULO I", "Primeira lição do livro.", "Segunda linha."],
                    ["CAPITULO II", "Outro capítulo aqui."],
                ]
            )
        )
        doc = DoclingExtractor().extract(pdf, SourceType.PDF_TEXT)
        assert doc.source_type is SourceType.PDF_TEXT
        assert len(doc.pages) >= 1
        assert any(b.kind is BlockKind.HEADING for b in doc.blocks)
        for block in doc.blocks:
            page = doc.pages[block.page_index] if block.page_index is not None else None
            if page is not None:
                assert page.text[block.char_start : block.char_end] == block.text
