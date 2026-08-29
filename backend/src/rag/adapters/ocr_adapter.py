"""Adapter de OCR para PDF escaneado (T05; SPEC §7.1; contrato §10.1.5).

`rag ocr` NÃO altera a edição nem o artefato original: produz um PDF novo
com camada de texto (derivado versionado), que a ingestão referencia via
`ocr_artifact` nos metadados.

O motor é plugável (`--engine`): ocrmac (macOS, offline), rapidocr (ONNX),
tesseract (binário externo) ou auto (docling escolhe). Testes usam motor
stub via injeção — os motores reais são cobertos por e2e opcional
(RAG_OCR_E2E=1), pois dependem de plataforma/modelos.
"""

from pathlib import Path
from typing import Protocol

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrAutoOptions,
    OcrMacOptions,
    OcrOptions,
    PdfPipelineOptions,
    RapidOcrOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.items.text import TextItem
from pydantic import BaseModel, ConfigDict

from rag.adapters.pdf_writer import OcrLine, OcrPage, write_text_layer_pdf
from rag.domain.errors import IngestionError
from rag.domain.identifiers import Sha256

_ENGINES = ("auto", "ocrmac", "rapidocr", "tesseract")


class OcrEngine(Protocol):
    """Motor de OCR: reconhece texto de um PDF página a página."""

    @property
    def name(self) -> str: ...

    def recognize(self, pdf_path: Path) -> list[OcrPage]: ...


class OcrReport(BaseModel):
    """Resultado do OCR. Nunca contém texto reconhecido."""

    model_config = ConfigDict(frozen=True)

    output_path: str
    sha256: Sha256
    engine: str
    pages: int
    lines: int


def _ocr_options(engine: str) -> OcrOptions:
    if engine == "auto":
        return OcrAutoOptions()
    if engine == "ocrmac":
        return OcrMacOptions()
    if engine == "rapidocr":
        return RapidOcrOptions()
    if engine == "tesseract":
        return TesseractCliOcrOptions()
    raise IngestionError(
        "Motor de OCR desconhecido.",
        context={"engine": engine, "available": ",".join(_ENGINES)},
    )


class DoclingOcrEngine:
    """Motor real: pipeline Docling com OCR habilitado."""

    def __init__(self, engine: str = "auto") -> None:
        options = PdfPipelineOptions(do_ocr=True, ocr_options=_ocr_options(engine))
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        self._name = engine

    @property
    def name(self) -> str:
        return self._name

    def recognize(self, pdf_path: Path) -> list[OcrPage]:
        try:
            result = self._converter.convert(str(pdf_path))
        except Exception as exc:
            raise IngestionError("Falha no OCR do documento.", cause=exc) from exc
        doc = result.document

        sizes = {
            page_no: (page.size.width, page.size.height) for page_no, page in doc.pages.items()
        }
        lines_by_page: dict[int, list[OcrLine]] = {p: [] for p in sizes}
        for item, _depth in doc.iterate_items():
            if not isinstance(item, TextItem) or not item.prov:
                continue
            text = " ".join(item.text.split())
            if not text:
                continue
            prov = item.prov[0]
            _width, height = sizes.get(prov.page_no, (612.0, 792.0))
            bbox = prov.bbox
            line_height = max(
                1.0,
                bbox.b - bbox.t if bbox.coord_origin is CoordOrigin.TOPLEFT else bbox.t - bbox.b,
            )
            y = height - bbox.b if bbox.coord_origin is CoordOrigin.TOPLEFT else bbox.b
            lines_by_page.setdefault(prov.page_no, []).append(
                OcrLine(text=text, x=bbox.l, y=y, height=line_height)
            )

        if not sizes:
            raise IngestionError("PDF sem páginas reconhecíveis.")
        return [
            OcrPage(
                width=sizes[page_no][0],
                height=sizes[page_no][1],
                lines=tuple(lines_by_page.get(page_no, [])),
            )
            for page_no in sorted(sizes)
        ]


def ocr_pdf(engine: OcrEngine, input_path: Path, output_path: Path) -> OcrReport:
    """Executa OCR e grava o derivado com camada de texto (atômico)."""
    if not input_path.is_file():
        raise IngestionError("Arquivo de origem não encontrado.", context={"name": input_path.name})
    if input_path.suffix.lower() != ".pdf":
        raise IngestionError("OCR aceita somente PDF.", context={"suffix": input_path.suffix})
    if output_path.suffix.lower() != ".pdf":
        # SPEC §7.1: --output pode ser um diretório.
        output_path = output_path / f"{input_path.stem}_ocr.pdf"
    output_path = output_path.resolve()
    if output_path == input_path.resolve():
        raise IngestionError("O derivado OCR não pode sobrescrever o original.")

    pages = engine.recognize(input_path)
    sha256 = write_text_layer_pdf(pages, output_path)
    return OcrReport(
        output_path=str(output_path),
        sha256=sha256,
        engine=engine.name,
        pages=len(pages),
        lines=sum(len(p.lines) for p in pages),
    )
