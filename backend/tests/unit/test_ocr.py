"""Adapter de OCR e gravador de PDF com camada de texto (T05; §10.1.5)."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import ImageChops
from PIL.Image import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_scanned_pdf, make_scanned_pdf_with_text

from rag.adapters.ocr_adapter import DoclingOcrEngine, _ocr_options, load_provenance, ocr_pdf
from rag.adapters.pdf_writer import OcrLine, OcrPage, write_text_layer_pdf
from rag.domain.errors import IngestionError, StorageError
from rag.domain.identifiers import sha256_of_file


class StubEngine:
    """Motor stub: reconhecimento determinístico, sem modelo nem rede."""

    name = "stub"
    version = "stub-0"

    def __init__(self, text: str = "Texto reconhecido.") -> None:
        self._text = text

    def recognize(self, pdf_path: Path) -> list[OcrPage]:
        page_count = len(pdfium.PdfDocument(str(pdf_path)))
        return [
            OcrPage(
                physical_index=page,
                width=612.0,
                height=792.0,
                lines=(OcrLine(text=self._text, x=72.0, y=700.0, height=12.0),)
                if page == 0
                else (),
            )
            for page in range(page_count)
        ]


def _render_pil(doc: pdfium.PdfDocument, index: int) -> Image:  # type: ignore[no-any-unimported]
    return doc[index].render(scale=1).to_pil().convert("RGB")  # type: ignore[no-any-return]


def _renders_are_pixel_identical(  # type: ignore[no-any-unimported]
    doc_a: pdfium.PdfDocument, doc_b: pdfium.PdfDocument, index: int
) -> bool:
    """Compara os renders pixel a pixel (correção R5-06 — extrema não basta:
    duas imagens diferentes podem compartilhar mínimo/máximo)."""
    diff = ImageChops.difference(_render_pil(doc_a, index), _render_pil(doc_b, index))
    return diff.getbbox() is None


class TestPdfWriter:
    def test_text_layer_preserves_visual_content_and_is_searchable(self, tmp_path: Path) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(2))
        out = tmp_path / "derivado.pdf"
        sha = write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=612.0,
                    height=792.0,
                    lines=(
                        OcrLine(text="CAPITULO I", x=72.0, y=720.0, height=18.0),
                        OcrLine(text="Lição de número um.", x=72.0, y=690.0, height=12.0),
                    ),
                ),
                OcrPage(physical_index=1, width=612.0, height=792.0, lines=()),
            ],
            out,
            source,
        )
        assert sha == hashlib.sha256(out.read_bytes()).hexdigest()

        original = pdfium.PdfDocument(str(source))
        derived = pdfium.PdfDocument(bytes(out.read_bytes()))
        assert len(derived) == len(original) == 2
        for index in range(2):
            assert _renders_are_pixel_identical(original, derived, index)
            # a página tem conteúdo visual (não é uma imagem em branco).
            assert _render_pil(derived, index).getextrema() != ((255, 255), (255, 255), (255, 255))

        text = derived[0].get_textpage().get_text_range()
        assert "CAPITULO I" in text
        assert "Lição de número um." in text
        assert derived[1].get_textpage().get_text_range().strip() == ""

    def test_rotated_page_preserves_rotation_and_pixels(self, tmp_path: Path) -> None:
        """R5-06: página rotacionada mantém `/Rotate` e conteúdo visual.

        `pdfium.get_width/height` (usado pela checagem de geometria R6-04) e
        o próprio Docling reportam as dimensões EFETIVAS (já com a rotação
        aplicada) para uma página rotacionada 90°/270° — 612x792 vira
        792x612. `OcrPage.width/height` precisa refletir isso, não o
        mediabox bruto.
        """
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1, rotation=90))
        out = tmp_path / "derivado.pdf"
        write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=792.0,
                    height=612.0,
                    lines=(OcrLine(text="Texto rotacionado.", x=72.0, y=700.0, height=12.0),),
                )
            ],
            out,
            source,
        )
        original = pdfium.PdfDocument(str(source))
        derived = pdfium.PdfDocument(str(out))
        assert derived[0].get_rotation() == 90 == original[0].get_rotation()
        assert _renders_are_pixel_identical(original, derived, 0)
        assert "Texto rotacionado." in derived[0].get_textpage().get_text_range()

    def test_line_width_fits_bounding_box(self, tmp_path: Path) -> None:
        """R5-07: quando `width` é informado, o texto invisível é ajustado
        para ocupar exatamente a largura detectada (alinhamento p/ destaque)."""
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "derivado.pdf"
        write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=612.0,
                    height=792.0,
                    lines=(OcrLine(text="teste", x=50.0, y=50.0, height=12.0, width=300.0),),
                )
            ],
            out,
            source,
        )
        page = pdfium.PdfDocument(str(out))[0]
        text_objs = [o for o in page.get_objects() if type(o).__name__ == "PdfTextObj"]
        assert len(text_objs) == 1
        left, _bottom, right, _top = text_objs[0].get_bounds()
        assert right - left == pytest.approx(300.0, abs=0.5)

    def test_geometry_mismatch_rejected(self, tmp_path: Path) -> None:
        """R6-04: dimensões declaradas pelo motor são validadas contra a
        página real (com tolerância) — não eram usadas antes desta correção."""
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))  # 612x792 pt
        with pytest.raises(StorageError, match="Dimensões reportadas"):
            write_text_layer_pdf(
                [OcrPage(physical_index=0, width=200.0, height=200.0, lines=())],
                tmp_path / "out.pdf",
                source,
            )

    def test_geometry_within_tolerance_accepted(self, tmp_path: Path) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))  # 612x792 pt
        write_text_layer_pdf(
            [OcrPage(physical_index=0, width=612.4, height=791.7, lines=())],
            tmp_path / "out.pdf",
            source,
        )  # não deve levantar

    def test_unicode_text_round_trips(self, tmp_path: Path) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "derivado.pdf"
        sample = "Acentuação: ção, ã, é — travessão, “aspas curvas”, café."
        write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=612.0,
                    height=792.0,
                    lines=(OcrLine(sample, 10, 10, 12),),
                )
            ],
            out,
            source,
        )
        text = pdfium.PdfDocument(str(out))[0].get_textpage().get_text_range()
        assert sample in text

    def test_write_is_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "derivado.pdf"
        write_text_layer_pdf(
            [OcrPage(physical_index=0, width=612.0, height=792.0, lines=(OcrLine("x", 0, 0, 12),))],
            out,
            source,
        )
        assert sorted(p.name for p in tmp_path.iterdir()) == ["derivado.pdf", "scan.pdf"]

    def test_empty_pages_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError, match="sem páginas"):
            write_text_layer_pdf([], tmp_path / "x.pdf", tmp_path / "scan.pdf")

    def test_page_count_mismatch_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))
        with pytest.raises(StorageError, match="não corresponde ao original"):
            write_text_layer_pdf(
                [
                    OcrPage(physical_index=0, width=612.0, height=792.0, lines=()),
                    OcrPage(physical_index=1, width=612.0, height=792.0, lines=()),
                ],
                tmp_path / "out.pdf",
                source,
            )

    def test_physical_index_out_of_order_rejected(self, tmp_path: Path) -> None:
        """R5-07: páginas não são associadas apenas pela posição na lista —
        um `physical_index` fora de ordem falha fechado."""
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(2))
        with pytest.raises(StorageError, match="fora de ordem"):
            write_text_layer_pdf(
                [
                    OcrPage(physical_index=1, width=612.0, height=792.0, lines=()),
                    OcrPage(physical_index=0, width=612.0, height=792.0, lines=()),
                ],
                tmp_path / "out.pdf",
                source,
            )

    def test_failure_leaves_no_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))

        def boom(_src: Path, _dst: Path) -> None:
            raise OSError("disco cheio simulado")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="disco cheio"):
            write_text_layer_pdf(
                [
                    OcrPage(
                        physical_index=0, width=612.0, height=792.0, lines=(OcrLine("x", 0, 0, 12),)
                    )
                ],
                tmp_path / "derivado.pdf",
                source,
            )
        assert sorted(p.name for p in tmp_path.iterdir()) == ["scan.pdf"]

    def test_rename_failure_does_not_destroy_previous_valid_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R6-01: com proveniência embutida em um ÚNICO arquivo, não há mais
        um segundo `os.replace` para dessincronizar — uma falha no (único)
        rename nunca publica um estado incoerente nem destrói o anterior."""
        source = tmp_path / "scan.pdf"
        source.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "derivado.pdf"
        first_sha = write_text_layer_pdf(
            [
                OcrPage(
                    physical_index=0,
                    width=612.0,
                    height=792.0,
                    lines=(OcrLine("versao 1", 0, 0, 12),),
                )
            ],
            out,
            source,
        )
        first_bytes = out.read_bytes()

        def boom(_src: Path, _dst: Path) -> None:
            raise OSError("disco cheio simulado no rename")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="disco cheio simulado no rename"):
            write_text_layer_pdf(
                [
                    OcrPage(
                        physical_index=0,
                        width=612.0,
                        height=792.0,
                        lines=(OcrLine("versao 2", 0, 0, 12),),
                    )
                ],
                out,
                source,
            )
        monkeypatch.undo()

        # o arquivo anterior (válido) não foi tocado nem deixado incoerente.
        assert out.read_bytes() == first_bytes
        assert hashlib.sha256(out.read_bytes()).hexdigest() == first_sha
        assert sorted(p.name for p in tmp_path.iterdir()) == ["derivado.pdf", "scan.pdf"]


class TestOcrPdf:
    def test_produces_derived_pdf_with_verifiable_provenance(self, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "out" / "derivado.pdf"
        report = ocr_pdf(StubEngine(), scan, out)
        assert report.pages == 1
        assert report.lines == 1
        assert report.engine == "stub"
        assert out.is_file()
        assert report.sha256 == hashlib.sha256(out.read_bytes()).hexdigest()

        provenance = load_provenance(out)
        assert provenance.input_sha256 == sha256_of_file(scan)
        assert provenance.engine == "stub"
        assert provenance.adapter_version == "stub-0"
        assert provenance.pages == 1

    def test_output_directory_derives_name(self, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        out_dir = tmp_path / "saida"
        out_dir.mkdir()
        report = ocr_pdf(StubEngine(), scan, out_dir)
        assert Path(report.output_path).name == "scan_ocr.pdf"
        assert Path(report.output_path).is_file()
        assert load_provenance(Path(report.output_path)).pages == 1

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
        scan.write_bytes(make_scanned_pdf(1))
        with pytest.raises(IngestionError, match="sobrescrever o original"):
            ocr_pdf(StubEngine(), scan, scan)

    def test_unknown_engine_rejected(self) -> None:
        with pytest.raises(IngestionError, match="desconhecido"):
            _ocr_options("motor-inexistente")

    def test_provenance_missing_fails(self, tmp_path: Path) -> None:
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "derivado.pdf"
        write_text_layer_pdf(
            [OcrPage(physical_index=0, width=612.0, height=792.0, lines=(OcrLine("x", 0, 0, 12),))],
            out,
            scan,
        )
        with pytest.raises(IngestionError, match="proveniência"):
            load_provenance(out)

    def test_publish_failure_leaves_previous_file_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R6-01: falha ao gravar uma nova versão não destrói a anterior —
        e, como há um único arquivo, não há como publicar um par incoerente."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf(1))
        out = tmp_path / "derivado.pdf"
        first = ocr_pdf(StubEngine("primeira versão"), scan, out)
        provenance_before = load_provenance(out).model_dump()

        def boom(_src: Path, _dst: Path) -> None:
            raise OSError("disco cheio simulado")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="disco cheio simulado"):
            ocr_pdf(StubEngine("segunda versão"), scan, out)
        monkeypatch.undo()

        assert hashlib.sha256(out.read_bytes()).hexdigest() == first.sha256
        assert load_provenance(out).model_dump() == provenance_before

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.environ.get("RAG_OCR_E2E") != "1",
        reason="motor OCR real (rapidocr/torch) carrega modelo na 1ª execução; "
        "habilitar com RAG_OCR_E2E=1",
    )
    def test_real_engine_recognizes_text_and_preserves_image(self, tmp_path: Path) -> None:
        """E2E opcional (correção R5-05): imagem raster com frase legível,
        SEM camada de texto — prova que o motor real reconhece o texto (não
        apenas que o pipeline roda) e que o derivado preserva a imagem.
        """
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf_with_text("Capitulo Um licao"))
        assert pdfium.PdfDocument(str(scan))[0].get_textpage().get_text_range().strip() == ""

        out = tmp_path / "derivado.pdf"
        report = ocr_pdf(DoclingOcrEngine(engine="rapidocr"), scan, out)
        assert report.pages == 1
        assert report.lines > 0

        original = pdfium.PdfDocument(str(scan))
        derived = pdfium.PdfDocument(str(out))
        assert _renders_are_pixel_identical(original, derived, 0)

        recognized = derived[0].get_textpage().get_text_range()
        recognized_words = set(recognized.split())
        for expected in ("Capitulo", "Um", "licao"):
            assert expected in recognized_words, recognized

        provenance = load_provenance(out)
        assert provenance.input_sha256 == sha256_of_file(scan)
        assert provenance.pages == 1
        assert provenance.engine == "rapidocr"

    @pytest.mark.integration
    @pytest.mark.skipif(
        os.environ.get("RAG_OCR_E2E") != "1",
        reason="motor OCR real (rapidocr/torch) carrega modelo na 1ª execução; "
        "habilitar com RAG_OCR_E2E=1",
    )
    def test_real_engine_does_not_leak_absolute_paths_to_console(self, tmp_path: Path) -> None:
        """R6-02: RapidOCR/Torch escrevem em stderr por fora do structlog do
        CLI, inclusive caminhos absolutos de modelo — reprodução independente
        em subprocesso, com stdout e stderr capturados separadamente."""
        scan = tmp_path / "scan.pdf"
        scan.write_bytes(make_scanned_pdf_with_text("Capitulo Um licao"))
        script = tmp_path / "probe.py"
        script.write_text(
            "from pathlib import Path\n"
            "from rag.adapters.ocr_adapter import DoclingOcrEngine\n"
            f"DoclingOcrEngine(engine='rapidocr').recognize(Path({str(scan)!r}))\n",
            encoding="utf-8",
        )
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert str(tmp_path) not in combined
        assert "/Users/" not in combined
