"""Contrato de metadados de ingestão e derivação de seções (T05; SPEC §7.1)."""

from pathlib import Path
from uuid import uuid4

import pytest

from rag.application.ingest import (
    derive_sections,
    load_metadata,
    sha256_of_file,
)
from rag.domain.canonical import BlockKind, CanonicalBlock, CanonicalDocument, CanonicalPage
from rag.domain.enums import SourceType
from rag.domain.errors import IngestionError


class TestLoadMetadata:
    def test_valid_minimal(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text("title: Um Livro\n", encoding="utf-8")
        loaded = load_metadata(meta)
        assert loaded.title == "Um Livro"
        assert loaded.language == "pt"
        assert loaded.authors == ()

    def test_full_contract(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text(
            "title: Um Livro\n"
            "authors: [Autor A, Autor B]\n"
            "publisher: Editora\n"
            "publication_year: 1936\n"
            "isbn: '9788500000000'\n"
            "edition_label: 2ª edição\n"
            "license_status: public_domain\n",
            encoding="utf-8",
        )
        loaded = load_metadata(meta)
        assert loaded.authors == ("Autor A", "Autor B")
        assert loaded.publication_year == 1936

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(IngestionError, match="não encontrado"):
            load_metadata(tmp_path / "ausente.yaml")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text("title: [quebrado\n", encoding="utf-8")
        with pytest.raises(IngestionError, match="YAML"):
            load_metadata(meta)

    def test_non_mapping_yaml(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text("- item\n", encoding="utf-8")
        with pytest.raises(IngestionError, match="objeto"):
            load_metadata(meta)

    def test_missing_title(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text("publisher: Editora\n", encoding="utf-8")
        with pytest.raises(IngestionError, match="Metadados inválidos"):
            load_metadata(meta)

    def test_non_portuguese_rejected_with_clear_message(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text("title: A Book\nlanguage: en\n", encoding="utf-8")
        with pytest.raises(IngestionError, match="Metadados inválidos") as exc_info:
            load_metadata(meta)
        assert "somente 'pt'" in str(exc_info.value.cause)

    def test_error_does_not_leak_yaml_internals(self, tmp_path: Path) -> None:
        meta = tmp_path / "livro.yaml"
        meta.write_text("title: x\npublication_year: não-número\n", encoding="utf-8")
        with pytest.raises(IngestionError) as exc_info:
            load_metadata(meta)
        assert "não-número" not in str(exc_info.value)


class TestSha256OfFile:
    def test_matches_content(self, tmp_path: Path) -> None:
        import hashlib

        f = tmp_path / "a.bin"
        f.write_bytes(b"conteudo" * 1000)
        assert sha256_of_file(f) == hashlib.sha256(b"conteudo" * 1000).hexdigest()


def _block(
    ordinal: int,
    kind: BlockKind,
    level: int,
    text: str,
    path: tuple[str, ...],
    page: int,
) -> CanonicalBlock:
    return CanonicalBlock(
        ordinal=ordinal,
        kind=kind,
        level=level,
        text=text,
        original_text=text,
        section_path=path,
        page_index=page,
        char_start=0,
        char_end=len(text),
    )


def _doc(blocks: list[CanonicalBlock], pages: int) -> CanonicalDocument:
    return CanonicalDocument(
        source_type=SourceType.PDF_TEXT,
        blocks=tuple(blocks),
        pages=tuple(
            CanonicalPage(physical_index=i, printed_label=str(i + 1), text=f"p{i}")
            for i in range(pages)
        ),
    )


class TestDeriveSections:
    def test_hierarchy_parents_and_page_ranges(self) -> None:
        doc = _doc(
            [
                _block(0, BlockKind.HEADING, 0, "Cap I", ("Cap I",), 0),
                _block(1, BlockKind.PARAGRAPH, 0, "texto", ("Cap I",), 0),
                _block(2, BlockKind.HEADING, 1, "Sec 1.1", ("Cap I", "Sec 1.1"), 1),
                _block(3, BlockKind.PARAGRAPH, 1, "detalhe", ("Cap I", "Sec 1.1"), 2),
                _block(4, BlockKind.HEADING, 0, "Cap II", ("Cap II",), 3),
            ],
            pages=4,
        )
        sections = derive_sections(uuid4(), doc)
        assert [(s.level, s.title) for s in sections] == [
            (0, "Cap I"),
            (1, "Sec 1.1"),
            (0, "Cap II"),
        ]
        cap1, sec11, cap2 = sections
        assert sec11.parent_section_id == cap1.id
        assert cap2.parent_section_id is None
        assert (cap1.start_page, cap1.end_page) == (0, 2)  # subtree inclui Sec 1.1
        assert (sec11.start_page, sec11.end_page) == (1, 2)
        assert (cap2.start_page, cap2.end_page) == (3, 3)
        assert [s.ordinal for s in sections] == [0, 1, 2]

    def test_document_without_headings_yields_no_sections(self) -> None:
        doc = _doc([_block(0, BlockKind.PARAGRAPH, 0, "texto", (), 0)], pages=1)
        assert derive_sections(uuid4(), doc) == []
