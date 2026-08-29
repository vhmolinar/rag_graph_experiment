"""Adapter Docling → representação canônica (T05; SPEC §7.2).

Docling é um adapter: este módulo é o ÚNICO ponto do sistema que importa
docling/docling_core. Todo o restante consome `CanonicalDocument`.

Decisões de mapeamento:
- `title` e `section_header` viram headings; nível = profundidade na árvore do
  Docling, normalizada para começar em 0;
- `text` e `list_item` viram parágrafos na seção vigente;
- `page_header`/`page_footer` são descartados (mobília de página);
- tabelas, figuras, fórmulas, notas e legendas são ignoradas com warning —
  a fase 1 é textual (SPEC: sem RAG multimodal);
- texto da página é a concatenação dos blocos normalizados em ordem de
  leitura; offsets do bloco referem-se a esse texto, garantindo que o trecho
  se recompõe exatamente (AC-03);
- PDF sem texto extraível (escaneado) falha fechado com orientação de OCR.
"""

from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.text import SectionHeaderItem, TextItem
from docling_core.types.doc.labels import DocItemLabel

from rag.domain.canonical import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
)
from rag.domain.enums import SourceType
from rag.domain.errors import IngestionError

_HEADING_LABELS = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
_PARAGRAPH_LABELS = {DocItemLabel.TEXT, DocItemLabel.LIST_ITEM}
_FURNITURE_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
_SKIPPED_WITH_WARNING = {
    DocItemLabel.TABLE: "tabela",
    DocItemLabel.PICTURE: "figura",
    DocItemLabel.FORMULA: "fórmula",
    DocItemLabel.FOOTNOTE: "nota de rodapé",
    DocItemLabel.CAPTION: "legenda",
    DocItemLabel.CHART: "gráfico",
}

_FORMATS = {SourceType.PDF_TEXT: InputFormat.PDF, SourceType.EPUB: InputFormat.EPUB}


def _normalize(text: str) -> str:
    return " ".join(text.split())


class DoclingExtractor:
    """Extrai PDF-texto e EPUB para o schema canônico (OCR desabilitado)."""

    def __init__(self, converter: DocumentConverter | None = None) -> None:
        if converter is None:
            pdf_options = PdfPipelineOptions(do_ocr=False, generate_picture_images=False)
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
            )
        self._converter = converter

    def extract(self, path: Path, source_type: SourceType) -> CanonicalDocument:
        if source_type not in _FORMATS:
            raise IngestionError(
                "Tipo de fonte não extraível diretamente.",
                context={"source_type": str(source_type)},
            )
        try:
            result = self._converter.convert(str(path))
        except Exception as exc:
            raise IngestionError(
                "Falha ao converter o documento.",
                cause=exc,
                context={"source_type": str(source_type)},
            ) from exc
        return _to_canonical(result.document, source_type)


def _to_canonical(doc: object, source_type: SourceType) -> CanonicalDocument:
    """Traduz o documento Docling para o schema canônico (função pura testável)."""
    if not isinstance(doc, DoclingDocument):
        raise IngestionError(
            "Conversor retornou documento em formato inesperado.",
            context={"type": type(doc).__name__},
        )

    warnings: list[str] = []
    headings: list[tuple[int, str]] = []  # pilha de (nível, título) vigente
    raw_blocks: list[tuple[BlockKind, int, str, str, tuple[str, ...], int | None]] = []
    skipped: dict[str, int] = {}
    heading_levels: set[int] = set()

    for item, _depth in doc.iterate_items():
        label = getattr(item, "label", None)
        if label in _SKIPPED_WITH_WARNING:
            name = _SKIPPED_WITH_WARNING[label]
            skipped[name] = skipped.get(name, 0) + 1
            continue
        if not isinstance(item, TextItem):
            continue
        if label in _FURNITURE_LABELS:
            continue
        if label not in _HEADING_LABELS and label not in _PARAGRAPH_LABELS:
            continue
        text = _normalize(item.text)
        if not text:
            continue
        original = item.orig if item.orig and item.orig.strip() else item.text
        page_index = item.prov[0].page_no - 1 if item.prov else None

        if label in _HEADING_LABELS:
            # Nível hierárquico vem do próprio item (1-based no Docling);
            # title equivale ao nível mais alto.
            hlevel = item.level if isinstance(item, SectionHeaderItem) else 1
            heading_levels.add(hlevel)
            while headings and headings[-1][0] >= hlevel:
                headings.pop()
            headings.append((hlevel, text))
            raw_blocks.append(
                (
                    BlockKind.HEADING,
                    hlevel,
                    text,
                    original,
                    tuple(t for _, t in headings),
                    page_index,
                )
            )
        else:
            raw_blocks.append(
                (BlockKind.PARAGRAPH, 0, text, original, tuple(t for _, t in headings), page_index)
            )

    for name, count in sorted(skipped.items()):
        warnings.append(f"{count} {name}(s) ignorada(s): conteúdo não textual fora da fase 1")

    if not raw_blocks:
        raise IngestionError(
            "Nenhum texto extraível encontrado. Para PDF escaneado, execute "
            "'rag ocr' antes de ingerir.",
            context={"source_type": str(source_type)},
        )

    # Normaliza níveis: a menor profundidade de heading vira nível 0.
    level_map = {d: i for i, d in enumerate(sorted(heading_levels))} or {0: 0}

    def _level_of(kind: BlockKind, depth: int, path: tuple[str, ...]) -> int:
        if kind is BlockKind.HEADING:
            return level_map[depth]
        return max(len(path) - 1, 0)

    # Monta texto de página e offsets (PDF): blocos em ordem, separados por \n.
    page_texts: dict[int, str] = {}
    page_offsets: list[tuple[int, int] | None] = []
    for _kind, _lvl, text, _original, _path, page_index in raw_blocks:
        if page_index is None:
            page_offsets.append(None)
            continue
        current = page_texts.get(page_index, "")
        start = len(current)
        page_texts[page_index] = f"{current}{text}\n"
        page_offsets.append((start, start + len(text)))

    blocks = tuple(
        CanonicalBlock(
            ordinal=i,
            kind=kind,
            level=_level_of(kind, depth, path),
            text=text,
            original_text=original,
            section_path=path,
            page_index=page_index,
            page_label=str(page_index + 1) if page_index is not None else None,
            char_start=offsets[0] if offsets else None,
            char_end=offsets[1] if offsets else None,
        )
        for i, ((kind, depth, text, original, path, page_index), offsets) in enumerate(
            zip(raw_blocks, page_offsets, strict=True)
        )
    )
    pages = tuple(
        CanonicalPage(
            physical_index=index,
            printed_label=str(index + 1),
            text=page_texts[index].rstrip("\n"),
        )
        for index in sorted(page_texts)
    )
    return CanonicalDocument(
        source_type=source_type, blocks=blocks, pages=pages, warnings=tuple(warnings)
    )
