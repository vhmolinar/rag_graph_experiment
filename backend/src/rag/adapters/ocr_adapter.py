"""Adapter de OCR para PDF escaneado (T05; SPEC §7.1; contrato §10.1.5).

`rag ocr` NÃO altera a edição nem o artefato original: produz um PDF novo
com camada de texto (derivado versionado), que a ingestão referencia via
`ocr_artifact` nos metadados.

O motor é plugável (`--engine`): ocrmac (macOS, offline), rapidocr (torch,
correção R5-01 — o padrão onnxruntime da lib nunca foi dependência do
projeto), tesseract (binário externo) ou auto (docling escolhe). Testes usam
motor stub via injeção — os motores reais são cobertos por e2e opcional
(RAG_OCR_E2E=1), pois dependem de plataforma/modelos.

Proveniência (correção T5-04, redesenhada em R6-01): o sidecar de arquivo
separado foi abandonado — dois arquivos publicados por dois `os.replace`
sucessivos nunca são atômicos em conjunto (uma falha entre os dois deixa um
par incoerente). Em vez disso, a proveniência (hash de entrada, motor/versão,
contagem de páginas) é embutida como ANEXO dentro do próprio PDF derivado
antes da publicação — um único arquivo, uma única gravação atômica, sem
janela de dessincronia possível. `rag ingest` lê o anexo e valida o hash de
entrada e a contagem de páginas antes de aceitar o `ocr_artifact`.

Terceiros (RapidOCR/Torch) escrevem diretamente em stderr por fora do
structlog do CLI, inclusive caminhos absolutos de arquivos de modelo
(correção R6-02); `_harden_third_party_logging()` redige esses caminhos.
"""

import logging
import re
import sys
import warnings
from importlib.metadata import version as _package_version
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
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from rag.adapters.pdf_writer import (
    OcrLine,
    OcrPage,
    build_text_layer_pdf,
    publish_file,
    read_attachment,
)
from rag.domain.errors import IngestionError
from rag.domain.identifiers import Sha256, sha256_of_file
from rag.domain.versions import utcnow

_ENGINES = ("auto", "ocrmac", "rapidocr", "tesseract")
_PROVENANCE_SCHEMA_VERSION = 2
_PROVENANCE_ATTACHMENT_NAME = "rag-ocr-provenance.json"

_ABS_PATH = re.compile(r"(?:/[^\s:]+)+/([^\s/:]+)")


def _strip_absolute_paths(text: str) -> str:
    return _ABS_PATH.sub(lambda m: m.group(1), text)


class _PathRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _strip_absolute_paths(record.getMessage())
        record.args = ()
        return True


_THIRD_PARTY_LOGGING_HARDENED = False


def _harden_third_party_logging() -> None:
    """RapidOCR/Torch escrevem em stderr por fora do structlog do CLI,
    inclusive caminhos absolutos de arquivos de modelo — contradiz o
    contrato de logs do CLI (correção R6-02). Idempotente; não altera o
    pipeline structlog em si, só redige o que os terceiros escrevem.
    """
    global _THIRD_PARTY_LOGGING_HARDENED
    if _THIRD_PARTY_LOGGING_HARDENED:
        return
    _THIRD_PARTY_LOGGING_HARDENED = True

    path_filter = _PathRedactionFilter()
    rapidocr_logger = logging.getLogger("RapidOCR")
    rapidocr_logger.addFilter(path_filter)
    for handler in rapidocr_logger.handlers:
        handler.addFilter(path_filter)
    if logging.lastResort is not None:
        logging.lastResort.addFilter(path_filter)

    def _redacted_showwarning(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: object = None,
        line: str | None = None,
    ) -> None:
        formatted = warnings.formatwarning(message, category, filename, lineno, line)
        text = _strip_absolute_paths(formatted)
        target = file if file is not None else sys.stderr
        target.write(text)  # type: ignore[union-attr]

    warnings.showwarning = _redacted_showwarning


class OcrEngine(Protocol):
    """Motor de OCR: reconhece texto de um PDF página a página."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def recognize(self, pdf_path: Path) -> list[OcrPage]: ...


class OcrReport(BaseModel):
    """Resultado do OCR. Nunca contém texto reconhecido."""

    model_config = ConfigDict(frozen=True)

    output_path: str
    sha256: Sha256
    engine: str
    pages: int
    lines: int


class OcrProvenance(BaseModel):
    """Metadados verificáveis do derivado OCR (correção T5-04; redesenhado
    em R6-01 para viver DENTRO do PDF, não em sidecar separado).

    `rag ingest` usa `input_sha256` para provar que o `ocr_artifact`
    informado foi de fato produzido a partir do arquivo original sendo
    ingerido (e não de outro livro) e `pages` para provar que a contagem
    bate com o extraído. Não há mais `output_sha256`: como a proveniência
    viaja DENTRO do mesmo arquivo que descreve, não existe um segundo
    artefato com o qual ela possa dessincronizar.

    `adapter_version` (renomeado de `engine_version` na correção R6-05,
    aprovado pelo usuário): identifica a versão do Docling e, quando a
    opção do motor expõe um parâmetro que determina a implementação
    concreta (ex.: `backend` do RapidOCR), esse detalhe. NÃO é uma versão
    de reprodução completa — não resolve `auto` a um motor concreto nem
    captura versão de binário (Tesseract) ou hash de modelo (RapidOCR/
    ocrmac); ver NOTES.md §10.4 para o registro da limitação aceita.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = _PROVENANCE_SCHEMA_VERSION
    input_sha256: Sha256
    engine: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    pages: int = Field(ge=1)
    created_at: AwareDatetime = Field(default_factory=utcnow)


def load_provenance(pdf_path: Path) -> OcrProvenance:
    """Lê e valida a proveniência embutida no derivado OCR."""
    data = read_attachment(pdf_path, _PROVENANCE_ATTACHMENT_NAME)
    if data is None:
        raise IngestionError(
            "Metadados de proveniência do OCR não encontrados no PDF: execute 'rag ocr' "
            "novamente ou informe o arquivo correto.",
            context={"name": pdf_path.name},
        )
    try:
        return OcrProvenance.model_validate_json(data)
    except ValueError as exc:
        raise IngestionError(
            "Metadados de proveniência do OCR inválidos.",
            cause=exc,
            context={"name": pdf_path.name},
        ) from exc


def _ocr_options(engine: str) -> OcrOptions:
    if engine == "auto":
        return OcrAutoOptions()
    if engine == "ocrmac":
        return OcrMacOptions()
    if engine == "rapidocr":
        # backend="torch": único backend do RapidOCR verificado disponível no
        # conjunto de dependências aprovado (o padrão da lib é "onnxruntime",
        # que NÃO é dependência do projeto — correção R5-01: nunca escolher
        # silenciosamente um backend que exigiria pacote não aprovado).
        return RapidOcrOptions(backend="torch")
    if engine == "tesseract":
        return TesseractCliOcrOptions()
    raise IngestionError(
        "Motor de OCR desconhecido.",
        context={"engine": engine, "available": ",".join(_ENGINES)},
    )


def _adapter_version(engine: str, ocr_options: OcrOptions) -> str:
    """Identificação parcial do adapter/motor (correção R5-08, escopo
    reduzido e renomeado em R6-05 — ver docstring de `OcrProvenance`).
    """
    docling_version = f"docling-{_package_version('docling')}"
    backend = getattr(ocr_options, "backend", None)
    if backend:
        return f"{docling_version}+{engine}-backend={backend}"
    return f"{docling_version}+{engine}"


class DoclingOcrEngine:
    """Motor real: pipeline Docling com OCR habilitado."""

    def __init__(self, engine: str = "auto") -> None:
        _harden_third_party_logging()
        ocr_options = _ocr_options(engine)
        options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        self._name = engine
        self._version = _adapter_version(engine, ocr_options)

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

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
            _page_width, height = sizes.get(prov.page_no, (612.0, 792.0))
            bbox = prov.bbox
            top_origin = bbox.coord_origin is CoordOrigin.TOPLEFT
            line_height = max(1.0, bbox.b - bbox.t if top_origin else bbox.t - bbox.b)
            y = height - bbox.b if top_origin else bbox.b
            width = max(0.0, bbox.r - bbox.l)
            lines_by_page.setdefault(prov.page_no, []).append(
                OcrLine(text=text, x=bbox.l, y=y, height=line_height, width=width)
            )

        if not sizes:
            raise IngestionError("PDF sem páginas reconhecíveis.")
        return [
            OcrPage(
                physical_index=page_no - 1,
                width=sizes[page_no][0],
                height=sizes[page_no][1],
                lines=tuple(lines_by_page.get(page_no, [])),
            )
            for page_no in sorted(sizes)
        ]


def ocr_pdf(engine: OcrEngine, input_path: Path, output_path: Path) -> OcrReport:
    """Executa OCR e publica o derivado com camada de texto e proveniência
    embutida, atomicamente, como um único arquivo (correção R6-01)."""
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

    input_sha256 = sha256_of_file(input_path)
    pages = engine.recognize(input_path)
    provenance = OcrProvenance(
        input_sha256=input_sha256,
        engine=engine.name,
        adapter_version=engine.version,
        pages=len(pages),
    )
    provenance_bytes = provenance.model_dump_json(indent=2).encode("utf-8")
    pdf_bytes = build_text_layer_pdf(
        pages, input_path, attachment=(_PROVENANCE_ATTACHMENT_NAME, provenance_bytes)
    )
    sha256 = publish_file(pdf_bytes, output_path)
    return OcrReport(
        output_path=str(output_path),
        sha256=sha256,
        engine=engine.name,
        pages=len(pages),
        lines=sum(len(p.lines) for p in pages),
    )
