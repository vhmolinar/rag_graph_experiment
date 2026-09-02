"""Schema canônico de extração (T05; SPEC §7.2; AC-03)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag.domain.canonical import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
)
from rag.domain.enums import SourceType

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "canonical_document.golden.json"


def _pdf_block(
    ordinal: int,
    kind: BlockKind = BlockKind.PARAGRAPH,
    level: int = 0,
    text: str = "texto",
    section_path: tuple[str, ...] = (),
    page_index: int | None = 0,
    char_start: int | None = 0,
    char_end: int | None = 5,
) -> CanonicalBlock:
    return CanonicalBlock(
        ordinal=ordinal,
        kind=kind,
        level=level,
        text=text,
        original_text=text,
        section_path=section_path,
        page_index=page_index,
        char_start=char_start,
        char_end=char_end,
    )


def _pdf_doc(blocks: list[CanonicalBlock] | None = None) -> CanonicalDocument:
    return CanonicalDocument(
        source_type=SourceType.PDF_TEXT,
        blocks=tuple(blocks or [_pdf_block(0)]),
        pages=(CanonicalPage(physical_index=0, printed_label="1", text="texto"),),
    )


class TestBlockInvariants:
    def test_offsets_must_be_paired(self) -> None:
        with pytest.raises(ValidationError, match="ambos"):
            _pdf_block(0, char_start=0, char_end=None)

    def test_offset_end_after_start(self) -> None:
        with pytest.raises(ValidationError, match="char_end"):
            _pdf_block(0, char_start=5, char_end=5)

    def test_heading_requires_section_path(self) -> None:
        with pytest.raises(ValidationError, match="section_path"):
            _pdf_block(0, kind=BlockKind.HEADING, text="Capítulo", section_path=())

    def test_block_is_frozen(self) -> None:
        block = _pdf_block(0)
        with pytest.raises(ValidationError):
            block.text = "alterado"


class TestDocumentInvariants:
    def test_ordinals_must_be_sequential(self) -> None:
        with pytest.raises(ValidationError, match="sequenciais"):
            _pdf_doc([_pdf_block(0), _pdf_block(2)])

    def test_pdf_scan_is_not_directly_extractable(self) -> None:
        with pytest.raises(ValidationError, match="OCR"):
            CanonicalDocument(
                source_type=SourceType.PDF_SCAN,
                blocks=(_pdf_block(0),),
                pages=(CanonicalPage(physical_index=0, text="x"),),
            )

    def test_epub_has_no_pages(self) -> None:
        with pytest.raises(ValidationError, match="EPUB"):
            CanonicalDocument(
                source_type=SourceType.EPUB,
                blocks=(
                    CanonicalBlock(
                        ordinal=0,
                        kind=BlockKind.PARAGRAPH,
                        level=0,
                        text="texto",
                        original_text="texto",
                        page_index=0,
                    ),
                ),
            )

    def test_pdf_block_requires_existing_page(self) -> None:
        with pytest.raises(ValidationError, match="página inexistente"):
            _pdf_doc([_pdf_block(0, page_index=7)])

    def test_pdf_requires_pages(self) -> None:
        with pytest.raises(ValidationError, match="exige páginas"):
            CanonicalDocument(
                source_type=SourceType.PDF_TEXT,
                blocks=(_pdf_block(0),),
            )

    def test_duplicate_physical_index_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicado"):
            CanonicalDocument(
                source_type=SourceType.PDF_TEXT,
                blocks=(_pdf_block(0),),
                pages=(
                    CanonicalPage(physical_index=0, text="a"),
                    CanonicalPage(physical_index=0, text="b"),
                ),
            )


class TestGoldenFile:
    def test_canonical_serialization_is_stable(self) -> None:
        """Golden file: a serialização do schema canônico é estável — qualquer
        mudança de contrato quebra este teste e exige revisão consciente."""
        doc = CanonicalDocument(
            source_type=SourceType.PDF_TEXT,
            blocks=(
                CanonicalBlock(
                    ordinal=0,
                    kind=BlockKind.HEADING,
                    level=0,
                    text="Capítulo I",
                    original_text="CAPÍTULO I",
                    section_path=("Capítulo I",),
                    page_index=0,
                    page_label="1",
                    char_start=0,
                    char_end=10,
                ),
                CanonicalBlock(
                    ordinal=1,
                    kind=BlockKind.PARAGRAPH,
                    level=0,
                    text="Um texto qualquer.",
                    original_text="Um  texto\nqualquer.",
                    section_path=("Capítulo I",),
                    page_index=0,
                    page_label="1",
                    char_start=11,
                    char_end=31,
                ),
            ),
            pages=(
                CanonicalPage(
                    physical_index=0,
                    printed_label="1",
                    text="Capítulo I\nUm texto qualquer.",
                ),
            ),
            warnings=("cabeçalho recorrente removido",),
        )
        serialized = json.dumps(
            json.loads(doc.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True
        )
        assert serialized + "\n" == GOLDEN.read_text(encoding="utf-8")
