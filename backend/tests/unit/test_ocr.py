"""Adapter de OCR e gravador de PDF com camada de texto (T05; §10.1.5)."""

import hashlib
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_text_pdf

from rag.adapters.ocr_adapter import _ocr_options, ocr_pdf
from rag.adapters.pdf_writer import OcrLine, OcrPage, write_text_layer_pdf
from rag.domain.errors import IngestionError, StorageError


class StubEngine:
    """Motor stub: reconhecimento determinístico, sem modelo nem rede."""

    name = "stub"

    def __init__(self, text: str = "Texto reconhecido.") -> None:
        self._text = text

    def recognize(self, pdf_path: Path) -> list[OcrPage]:
        return [
            OcrPage(
                width=612.0,
                height=792.0,
                lines=(OcrLine(text=self._text, x=72.0, y=700.0, height=12.0),),
            )
        ]


class TestPdfWriter:
    def test_text_layer_is_extractable_and_searchable(self, tmp_path: Path) -> None:
        out = tmp_path / "derivado.pdf"
        sha = write_text_layer_pdf(
            [
                OcrPage(
                    width=612.0,
                    height=792.0,
                    lines=(
                        OcrLine(text="CAPITULO I", x=72.0, y=720.0, height=18.0),
                        OcrLine(text="Lição de número um.", x=72.0, y=690.0, height=12.0),
                    ),
                ),
                OcrPage(width=612.0, height=792.0, lines=()),
            ],
            out,
        )
        assert sha == hashlib.sha256(out.read_bytes()).hexdigest()
        pdf = pdfium.PdfDocument(bytes(out.read_bytes()))
        assert len(pdf) == 2
        text = pdf[0].get_textpage().get_text_range()
        assert "CAPITULO I" in text
        assert "Lição de número um." in text
        assert pdf[1].get_textpage().get_text_range().strip() == ""

    def test_write_is_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        out = tmp_path / "derivado.pdf"
        write_text_layer_pdf(
            [OcrPage(width=612.0, height=792.0, lines=(OcrLine("x", 0, 0, 12),))], out
        )
        assert [p.name for p in tmp_path.iterdir()] == ["derivado.pdf"]

    def test_empty_pages_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError, match="sem páginas"):
            write_text_layer_pdf([], tmp_path / "x.pdf")

    def test_failure_leaves_no_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        def boom(_src: Path, _dst: Path) -> None:
            raise OSError("disco cheio simulado")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="disco cheio"):
            write_text_layer_pdf(
                [OcrPage(width=612.0, height=792.0, lines=(OcrLine("x", 0, 0, 12),))],
                tmp_path / "derivado.pdf",
            )
        assert list(tmp_path.iterdir()) == []


class TestOcrPdf:
    def test_produces_derived_pdf(self, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        out = tmp_path / "out" / "derivado.pdf"
        report = ocr_pdf(StubEngine(), scan, out)
        assert report.pages == 1
        assert report.lines == 1
        assert report.engine == "stub"
        assert out.is_file()
        assert report.sha256 == hashlib.sha256(out.read_bytes()).hexdigest()

    def test_output_directory_derives_name(self, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        out_dir = tmp_path / "saida"
        out_dir.mkdir()
        report = ocr_pdf(StubEngine(), scan, out_dir)
        assert Path(report.output_path).name == "scan_ocr.pdf"
        assert Path(report.output_path).is_file()

    def test_missing_input_fails(self, tmp_path: Path) -> None:
        with pytest.raises(IngestionError, match="não encontrado"):
            ocr_pdf(StubEngine(), tmp_path / "ausente.pdf", tmp_path / "out.pdf")

    def test_non_pdf_input_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "livro.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(IngestionError, match="somente PDF"):
            ocr_pdf(StubEngine(), f, tmp_path / "out.pdf")

    def test_output_must_not_overwrite_original(self, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_text_pdf([[]]))
        with pytest.raises(IngestionError, match="sobrescrever o original"):
            ocr_pdf(StubEngine(), scan, scan)

    def test_unknown_engine_rejected(self) -> None:
        with pytest.raises(IngestionError, match="desconhecido"):
            _ocr_options("motor-inexistente")
