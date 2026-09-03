"""Testes de fidelidade literal para EPUB (R01).

Verifica que a citação literal de EPUB preserva o texto original do bloco,
mesmo quando o chunk cobre apenas parte das sentenças do bloco.
"""

from rag.domain.canonical import BlockKind, CanonicalBlock, CanonicalDocument
from rag.domain.chunking import ChunkingParams, chunk_document
from rag.domain.enums import SourceType


def _make_epub_doc(blocks: list[CanonicalBlock]) -> CanonicalDocument:
    return CanonicalDocument(
        source_type=SourceType.EPUB,
        blocks=tuple(blocks),
        pages=(),
        warnings=(),
    )


class TestEpubLiteralFidelity:
    """Garante que EPUB preserve texto original dos blocos (AC-03, R01)."""

    def test_epub_chunk_uses_block_original_text_even_when_partial(self) -> None:
        """Para EPUB, mesmo quando um chunk cobre apenas algumas sentenças
        de um bloco, o `original_text` deve conter o bloco inteiro — nunca
        o texto normalizado parcial (R01)."""
        block = CanonicalBlock(
            ordinal=0,
            kind=BlockKind.PARAGRAPH,
            level=0,
            text="Primeira frase. Segunda frase. Terceira frase.",
            original_text="Primeira frase. Segunda frase. Terceira frase.",
            section_path=("Capítulo I",),
            page_index=None,
            page_label=None,
            char_start=None,
            char_end=None,
        )
        doc = _make_epub_doc([block])
        params = ChunkingParams(child_target_tokens=10, child_overlap_tokens=0)
        nodes = chunk_document(doc, params)
        
        assert len(nodes) >= 2, "esperava múltiplos chunks para forçar cobertura parcial"
        for node in nodes:
            assert node.original_text is not None
            assert "Primeira frase." in node.original_text
            assert "Segunda frase." in node.original_text
            assert "Terceira frase." in node.original_text

    def test_epub_chunk_preserves_original_whitespace_and_formatting(self) -> None:
        """EPUB deve preservar formatação original (quebras de linha, espaços
        múltiplos) no `original_text`, mesmo que o `text` normalize (R01)."""
        block = CanonicalBlock(
            ordinal=0,
            kind=BlockKind.PARAGRAPH,
            level=0,
            text="Texto com múltiplos espaços e quebras.",
            original_text="Texto  com  múltiplos  espaços\ne quebras de linha.",
            section_path=("Capítulo I",),
            page_index=None,
            page_label=None,
            char_start=None,
            char_end=None,
        )
        doc = _make_epub_doc([block])
        params = ChunkingParams()
        nodes = chunk_document(doc, params)
        
        assert len(nodes) >= 1
        node = nodes[0]
        assert node.original_text is not None
        assert "  " in node.original_text, "esperava espaços múltiplos preservados"
        assert "\n" in node.original_text, "esperava quebra de linha preservada"

    def test_epub_chunk_with_multiple_blocks_preserves_all_originals(self) -> None:
        """EPUB com múltiplos blocos: cada chunk deve conter o `original_text`
        de todos os blocos envolvidos, na ordem (R01)."""
        blocks = [
            CanonicalBlock(
                ordinal=0,
                kind=BlockKind.PARAGRAPH,
                level=0,
                text="Primeiro parágrafo.",
                original_text="Primeiro parágrafo.",
                section_path=("Capítulo I",),
                page_index=None,
                page_label=None,
                char_start=None,
                char_end=None,
            ),
            CanonicalBlock(
                ordinal=1,
                kind=BlockKind.PARAGRAPH,
                level=0,
                text="Segundo parágrafo.",
                original_text="Segundo parágrafo.",
                section_path=("Capítulo I",),
                page_index=None,
                page_label=None,
                char_start=None,
                char_end=None,
            ),
        ]
        doc = _make_epub_doc(blocks)
        params = ChunkingParams()
        nodes = chunk_document(doc, params)
        
        assert len(nodes) >= 1
        parent = nodes[0]
        assert parent.original_text is not None
        assert "Primeiro parágrafo." in parent.original_text
        assert "Segundo parágrafo." in parent.original_text
        assert parent.original_text.index("Primeiro") < parent.original_text.index("Segundo")

    def test_pdf_chunk_with_partial_block_uses_normalized_text(self) -> None:
        """Para PDF (com páginas), quando um chunk cobre apenas parte de um
        bloco, deve usar o texto normalizado — os offsets de página garantem
        precisão (comportamento anterior, preservado)."""
        from rag.domain.canonical import CanonicalPage
        
        page = CanonicalPage(
            physical_index=0,
            printed_label="1",
            text="Texto completo da página com várias frases.",
        )
        block = CanonicalBlock(
            ordinal=0,
            kind=BlockKind.PARAGRAPH,
            level=0,
            text="Texto completo da página com várias frases.",
            original_text="Texto completo da página com várias frases.",
            section_path=("Capítulo I",),
            page_index=0,
            page_label="1",
            char_start=0,
            char_end=47,
        )
        doc = CanonicalDocument(
            source_type=SourceType.PDF_TEXT,
            blocks=(block,),
            pages=(page,),
            warnings=(),
        )
        params = ChunkingParams(child_target_tokens=10, child_overlap_tokens=0)
        nodes = chunk_document(doc, params)
        
        assert len(nodes) >= 1
        for node in nodes:
            assert node.original_text is not None
            assert node.text is not None
