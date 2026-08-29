"""Adapter Docling → representação canônica (T05; SPEC §7.2).

Docling é um adapter: este módulo é o ÚNICO ponto do sistema que importa
docling/docling_core. Todo o restante consome `CanonicalDocument`.

Decisões de mapeamento:
- `title` e `section_header` viram headings; nível = profundidade na árvore do
  Docling, normalizada para começar em 0;
- `text`, `list_item`, `footnote` e `caption` viram parágrafos na seção
  vigente — são texto citável de livro, não mobília de página (T5-09);
- `page_header`/`page_footer` são descartados (mobília de página);
- tabelas, figuras, fórmulas e gráficos são ignorados com warning — a fase 1
  é textual (SPEC: sem RAG multimodal);
- qualquer outro rótulo de `TextItem` não mapeado explicitamente acima (ex.:
  `code`, `reference`) também é ignorado, mas SEMPRE com warning — nenhuma
  perda de conteúdo é silenciosa (T5-09);
- um item com proveniência em múltiplas páginas (`prov` com mais de uma
  entrada) é dividido em um bloco por página usando `charspan`; se o
  `charspan` de algum span não permitir recompor exatamente o trecho, a
  extração falha fechado em vez de arriscar atribuir texto à página errada
  (T5-06);
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
_PARAGRAPH_LABELS = {
    DocItemLabel.TEXT,
    DocItemLabel.LIST_ITEM,
    DocItemLabel.FOOTNOTE,
    DocItemLabel.CAPTION,
}
_FURNITURE_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
_SKIPPED_WITH_WARNING = {
    DocItemLabel.TABLE: "tabela",
    DocItemLabel.PICTURE: "figura",
    DocItemLabel.FORMULA: "fórmula",
    DocItemLabel.CHART: "gráfico",
}

_FORMATS = {SourceType.PDF_TEXT: InputFormat.PDF, SourceType.EPUB: InputFormat.EPUB}


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _spans_of(item: TextItem, label: object) -> list[tuple[int | None, str, str, bool]]:
    """Deriva (page_index, texto, original, original_e_fiel) por proveniência
    do item (T5-06).

    Sem `prov` (EPUB): uma posição sem página. Um único `prov`: o item
    inteiro na página indicada — comportamento anterior, preservado. Mais de
    um `prov` (item com conteúdo em múltiplas páginas): cada entrada PRECISA
    trazer um `charspan` que recorte exatamente sua parte de `item.text`;
    sem isso, mapear tudo para a primeira página arriscaria atribuir texto à
    página errada, então a extração falha fechado.

    `original_e_fiel` é `False` quando `original` NÃO é o texto original de
    fato (caiu para o texto normalizado por falta de alinhamento) — o
    chamador deve registrar isso como warning, nunca aceitar em silêncio
    (correção R6-03: um campo chamado `original_text` não pode conter texto
    normalizado sem que a perda seja registrada em algum lugar).
    """
    original_full = item.orig if item.orig and item.orig.strip() else item.text
    if not item.prov:
        return [(None, item.text, original_full, True)]
    if len(item.prov) == 1:
        return [(item.prov[0].page_no - 1, item.text, original_full, True)]
    spans: list[tuple[int | None, str, str, bool]] = []
    for prov in item.prov:
        charspan = getattr(prov, "charspan", None)
        if (
            not charspan
            or len(charspan) != 2
            or charspan[1] <= charspan[0]
            or charspan[1] > len(item.text)
        ):
            raise IngestionError(
                "Item com proveniência em múltiplas páginas sem charspan preciso; não é "
                "possível mapear o texto para a página correta com exatidão (AC-03).",
                context={"label": str(label)},
            )
        start, end = charspan
        segment = item.text[start:end]
        # `charspan` refere-se a `item.text`. `item.orig` só pode ser
        # recortado pelos mesmos índices quando tem exatamente o mesmo
        # comprimento (alinhamento 1:1 garantido); do contrário, recortar
        # `orig` pelos índices de `text` produziria um trecho errado, e
        # cair para o texto normalizado é registrado como perda (R5-11/R6-03).
        aligned = bool(item.orig) and len(item.orig) == len(item.text)
        original_segment = item.orig[start:end] if aligned else segment
        spans.append((prov.page_no - 1, segment, original_segment, aligned))
    return spans


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
    unmapped: dict[str, int] = {}
    heading_levels: set[int] = set()
    original_not_preserved = 0

    for item, _depth in doc.iterate_items():
        label = getattr(item, "label", None)
        if label in _SKIPPED_WITH_WARNING:
            name = _SKIPPED_WITH_WARNING[label]
            skipped[name] = skipped.get(name, 0) + 1
            continue
        if label in _FURNITURE_LABELS:
            continue
        if not isinstance(item, TextItem):
            # Item não textual sem política explícita (ex.: form/key-value): nunca
            # descartado silenciosamente (T5-09).
            name = str(label) if label is not None else type(item).__name__
            unmapped[name] = unmapped.get(name, 0) + 1
            continue
        if label not in _HEADING_LABELS and label not in _PARAGRAPH_LABELS:
            name = str(label) if label is not None else "desconhecido"
            unmapped[name] = unmapped.get(name, 0) + 1
            continue

        for page_index, raw_text, raw_original, original_faithful in _spans_of(item, label):
            text = _normalize(raw_text)
            if not text:
                continue
            original = raw_original if raw_original and raw_original.strip() else raw_text
            if not original_faithful:
                original_not_preserved += 1

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
                    (
                        BlockKind.PARAGRAPH,
                        0,
                        text,
                        original,
                        tuple(t for _, t in headings),
                        page_index,
                    )
                )

    for name, count in sorted(skipped.items()):
        warnings.append(f"{count} {name}(s) ignorada(s): conteúdo não textual fora da fase 1")
    for name, count in sorted(unmapped.items()):
        warnings.append(
            f"{count} item(ns) de rótulo '{name}' ignorado(s): rótulo não mapeado na fase 1"
        )
    if original_not_preserved:
        warnings.append(
            f"{original_not_preserved} bloco(s) sem texto original preservável: item com "
            "proveniência em múltiplas páginas e sem alinhamento exato entre texto normalizado "
            "e original — original_text contém o texto normalizado, não o original de fato "
            "(R6-03)"
        )

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
