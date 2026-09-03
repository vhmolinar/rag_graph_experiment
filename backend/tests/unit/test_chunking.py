"""Chunker estrutural e configurável (T06; SPEC §7.3)."""

from itertools import pairwise

from rag.domain.canonical import BlockKind, CanonicalBlock, CanonicalDocument, CanonicalPage
from rag.domain.chunking import (
    ChunkingParams,
    approximate_token_count,
    chunk_document,
    split_sentences,
)
from rag.domain.enums import SourceType


def _pdf_doc(pages: list[list[tuple[tuple[str, ...], str]]]) -> CanonicalDocument:
    """`pages[i]` = parágrafos (section_path, texto normalizado) em ordem de
    leitura na página `i`. Constrói `CanonicalPage.text` exatamente como
    `docling_adapter.py` faz (parágrafos unidos por "\\n"), para que fatiar
    por offset reproduza o texto original de verdade."""
    blocks: list[CanonicalBlock] = []
    canonical_pages: list[CanonicalPage] = []
    ordinal = 0
    for page_index, paragraphs in enumerate(pages):
        texts: list[str] = []
        offset = 0
        for section_path, text in paragraphs:
            start = offset
            end = start + len(text)
            blocks.append(
                CanonicalBlock(
                    ordinal=ordinal,
                    kind=BlockKind.PARAGRAPH,
                    level=0,
                    text=text,
                    original_text=text,
                    section_path=section_path,
                    page_index=page_index,
                    char_start=start,
                    char_end=end,
                )
            )
            ordinal += 1
            texts.append(text)
            offset = end + 1  # +1: separador "\n" entre parágrafos na página
        canonical_pages.append(
            CanonicalPage(
                physical_index=page_index, printed_label=str(page_index + 1), text="\n".join(texts)
            )
        )
    return CanonicalDocument(
        source_type=SourceType.PDF_TEXT, blocks=tuple(blocks), pages=tuple(canonical_pages)
    )


def _epub_doc(paragraphs: list[tuple[tuple[str, ...], str]]) -> CanonicalDocument:
    blocks = tuple(
        CanonicalBlock(
            ordinal=i,
            kind=BlockKind.PARAGRAPH,
            level=0,
            text=text,
            original_text=text,
            section_path=path,
        )
        for i, (path, text) in enumerate(paragraphs)
    )
    return CanonicalDocument(source_type=SourceType.EPUB, blocks=blocks)


CH1 = ("Capitulo Um",)
CH2 = ("Capitulo Dois",)


class TestSplitSentences:
    def test_splits_on_terminal_punctuation(self) -> None:
        text = "Esta e a primeira frase. Esta e a segunda frase!"
        spans = split_sentences(text)
        assert [text[s:e] for s, e in spans] == [
            "Esta e a primeira frase.",
            "Esta e a segunda frase!",
        ]

    def test_covers_whole_text_without_gaps(self) -> None:
        text = "Uma. Duas. Tres sem ponto final"
        spans = split_sentences(text)
        assert spans[0][0] == 0
        assert spans[-1][1] == len(text)
        for (_, end), (next_start, _) in pairwise(spans):
            assert text[end:next_start].strip() == ""

    def test_does_not_split_on_abbreviation(self) -> None:
        text = "Dr. Silva chegou. Ele trouxe flores."
        spans = split_sentences(text)
        sentences = [text[s:e] for s, e in spans]
        assert sentences == ["Dr. Silva chegou.", "Ele trouxe flores."]

    def test_does_not_split_on_single_initial(self) -> None:
        text = "J. R. R. Tolkien escreveu o livro. Foi publicado depois."
        spans = split_sentences(text)
        sentences = [text[s:e] for s, e in spans]
        assert sentences == ["J. R. R. Tolkien escreveu o livro.", "Foi publicado depois."]

    def test_empty_text_yields_no_spans(self) -> None:
        assert split_sentences("") == []


class TestApproximateTokenCount:
    def test_non_empty_text_is_never_zero(self) -> None:
        assert approximate_token_count("a") >= 1

    def test_empty_text_is_zero(self) -> None:
        assert approximate_token_count("") == 0

    def test_scales_with_length(self) -> None:
        assert approximate_token_count("a" * 400) > approximate_token_count("a" * 40)


class TestChunkDocument:
    def test_chunks_never_mix_chapters(self) -> None:
        doc = _pdf_doc(
            [
                [
                    (CH1, "Marca UM primeira frase."),
                    (CH1, "Marca UM segunda frase."),
                    (CH2, "Marca DOIS primeira frase."),
                    (CH2, "Marca DOIS segunda frase."),
                ]
            ]
        )
        nodes = chunk_document(doc, ChunkingParams())
        for node in nodes:
            if node.section_path == CH1:
                assert "DOIS" not in node.text
            elif node.section_path == CH2:
                assert "UM" not in node.text

    def test_sentences_are_never_cut(self) -> None:
        sentences = [f"Esta e a frase numero {i} do capitulo." for i in range(20)]
        doc = _pdf_doc([[(CH1, s) for s in sentences]])
        params = ChunkingParams(
            child_target_tokens=15, child_overlap_tokens=0, parent_target_tokens=15
        )
        nodes = chunk_document(doc, params)
        assert nodes
        for node in nodes:
            reconstructed = [node.text[s:e] for s, e in split_sentences(node.text)]
            # cada span recuperado de dentro do chunk é EXATAMENTE uma das
            # frases originais — nunca um fragmento cortado no meio.
            for sentence in reconstructed:
                assert sentence in sentences

    def test_offsets_recompose_original_single_page(self) -> None:
        doc = _pdf_doc(
            [
                [
                    (CH1, "Primeira frase do paragrafo."),
                    (CH1, "Segunda frase do paragrafo."),
                    (CH1, "Terceira frase do paragrafo."),
                ]
            ]
        )
        nodes = chunk_document(doc, ChunkingParams())
        page = doc.pages[0]
        for node in nodes:
            assert node.page_start_index == 0
            assert node.page_end_index == 0
            assert node.char_start is not None
            assert node.char_end is not None
            assert page.text[node.char_start : node.char_end] == node.text

    def test_offsets_recompose_original_across_pages(self) -> None:
        doc = _pdf_doc(
            [
                [(CH1, "Frase na primeira pagina, ainda no mesmo paragrafo.")],
                [(CH1, "Frase na segunda pagina, mesma secao.")],
            ]
        )
        params = ChunkingParams(child_target_tokens=1000, parent_target_tokens=1000)
        nodes = chunk_document(doc, params)
        spanning = [n for n in nodes if n.page_start_index != n.page_end_index]
        assert spanning, "esperava ao menos um chunk atravessando páginas"
        for node in spanning:
            assert node.char_start is not None
            assert node.char_end is not None
            assert node.page_start_index is not None
            assert node.page_end_index is not None
            start_page = doc.pages[node.page_start_index]
            end_page = doc.pages[node.page_end_index]
            reconstructed = (
                start_page.text[node.char_start :] + "\n" + end_page.text[: node.char_end]
            )
            assert reconstructed == node.text

    def test_overlap_repeats_trailing_sentences(self) -> None:
        # ~11 tokens/frase: alvo 25 cabe 2 frases por janela; sobreposição de
        # 11 tokens recua exatamente 1 frase para a próxima janela.
        sentences = [f"Frase numero {i} do capitulo estudado aqui." for i in range(30)]
        doc = _pdf_doc([[(CH1, s) for s in sentences]])
        params = ChunkingParams(
            child_target_tokens=25, child_overlap_tokens=11, parent_target_tokens=1000
        )
        nodes = chunk_document(doc, params)
        children = [n for n in nodes if n.parent_index is not None]
        assert len(children) >= 2
        # com sobreposição > 0, frases finais de um filho devem reaparecer no próximo.
        overlaps_found = 0
        for prev, nxt in pairwise(children):
            prev_sentences = {s for s in sentences if s in prev.text}
            next_sentences = {s for s in sentences if s in nxt.text}
            if prev_sentences & next_sentences:
                overlaps_found += 1
        assert overlaps_found > 0

    def test_parent_child_hierarchy(self) -> None:
        sentences = [f"Frase numero {i} do capitulo estudado aqui hoje." for i in range(30)]
        doc = _pdf_doc([[(CH1, s) for s in sentences]])
        params = ChunkingParams(
            child_target_tokens=20, child_overlap_tokens=5, parent_target_tokens=60
        )
        nodes = chunk_document(doc, params)
        parents = [n for n in nodes if n.parent_index is None]
        children = [n for n in nodes if n.parent_index is not None]
        assert parents
        assert children
        by_index = {n.index: n for n in nodes}
        for child in children:
            parent = by_index[child.parent_index]  # type: ignore[index]
            assert parent.parent_index is None
            assert parent.section_path == child.section_path
            # o pai aparece antes de seus filhos na lista (persistência insere pai primeiro).
            assert parent.index < child.index
            if parent.char_start is not None and child.char_start is not None:
                assert parent.char_start <= child.char_start
                assert child.char_end <= parent.char_end  # type: ignore[operator]

    def test_epub_has_no_page_offsets(self) -> None:
        doc = _epub_doc([(CH1, "Frase unica do epub sem paginas fisicas.")])
        nodes = chunk_document(doc, ChunkingParams())
        assert nodes
        for node in nodes:
            assert node.page_start_index is None
            assert node.page_end_index is None
            assert node.char_start is None
            assert node.char_end is None

    def test_empty_document_without_paragraphs_yields_no_nodes(self) -> None:
        doc = CanonicalDocument(
            source_type=SourceType.EPUB,
            blocks=(
                CanonicalBlock(
                    ordinal=0,
                    kind=BlockKind.HEADING,
                    level=0,
                    text="Titulo",
                    original_text="Titulo",
                    section_path=CH1,
                ),
            ),
        )
        assert chunk_document(doc, ChunkingParams()) == []


class TestOriginalText:
    """T6-05 (REVIEW_T06.md): o original do bloco canônico sobrevive ao
    chunking, mesmo quando diverge do texto normalizado usado por `text`."""

    def test_original_text_matches_when_source_has_no_normalization_loss(self) -> None:
        doc = _pdf_doc([[(CH1, "Frase unica no paragrafo.")]])
        nodes = chunk_document(doc, ChunkingParams())
        assert nodes
        for node in nodes:
            assert node.original_text == "Frase unica no paragrafo."

    def test_split_aligned_block_has_exact_text_per_child(self) -> None:
        """Para EPUB, mesmo quando o bloco está alinhado (text == original_text),
        os filhos recebem o bloco inteiro como original_text para garantir
        fidelidade literal (R01, AC-03)."""
        text = (
            "Primeira frase suficientemente longa. "
            "Segunda frase suficientemente longa. "
            "Terceira frase suficientemente longa."
        )
        doc = CanonicalDocument(
            source_type=SourceType.EPUB,
            blocks=(
                CanonicalBlock(
                    ordinal=0,
                    kind=BlockKind.PARAGRAPH,
                    level=0,
                    text=text,
                    original_text=text,
                    section_path=CH1,
                ),
            ),
        )
        children = [
            node
            for node in chunk_document(
                doc,
                ChunkingParams(
                    child_target_tokens=10, child_overlap_tokens=0, parent_target_tokens=1000
                ),
            )
            if node.parent_index is not None
        ]
        assert len(children) == 3
        # Para EPUB, cada filho recebe o bloco inteiro como original_text
        # (mesmo quando cobre apenas parte das sentenças) — fidelidade literal.
        for child in children:
            assert child.original_text == text
            assert child.original_text != child.text or child.text == text

    def test_pdf_citable_text_recomposes_offsets_across_blocks(self) -> None:
        first = "Primeira frase completa."
        second = "Segunda frase longa completa. Terceira frase longa completa."
        page = first + "\n" + second
        doc = CanonicalDocument(
            source_type=SourceType.PDF_TEXT,
            pages=(CanonicalPage(physical_index=0, text=page),),
            blocks=(
                CanonicalBlock(
                    ordinal=0,
                    kind=BlockKind.PARAGRAPH,
                    level=0,
                    text=first,
                    original_text=first,
                    section_path=CH1,
                    page_index=0,
                    char_start=0,
                    char_end=len(first),
                ),
                CanonicalBlock(
                    ordinal=1,
                    kind=BlockKind.PARAGRAPH,
                    level=0,
                    text=second,
                    original_text=second,
                    section_path=CH1,
                    page_index=0,
                    char_start=len(first) + 1,
                    char_end=len(page),
                ),
            ),
        )
        children = [
            n
            for n in chunk_document(
                doc,
                ChunkingParams(
                    child_target_tokens=14, child_overlap_tokens=0, parent_target_tokens=1000
                ),
            )
            if n.parent_index is not None
        ]
        for node in children:
            assert node.char_start is not None
            assert node.char_end is not None
            assert node.original_text == page[node.char_start : node.char_end]

    def test_original_text_preserves_exact_source_when_it_differs_from_normalized(self) -> None:
        blocks = (
            CanonicalBlock(
                ordinal=0,
                kind=BlockKind.PARAGRAPH,
                level=0,
                text="Texto normalizado sem hifen.",
                original_text="Texto nor-\nmalizado sem hífen.",
                section_path=CH1,
            ),
        )
        doc = CanonicalDocument(source_type=SourceType.EPUB, blocks=blocks)
        nodes = chunk_document(doc, ChunkingParams())
        assert nodes
        assert nodes[0].original_text == "Texto nor-\nmalizado sem hífen."
        assert nodes[0].text != nodes[0].original_text

    def test_original_text_joins_multiple_contributing_blocks_without_duplication(self) -> None:
        blocks = (
            CanonicalBlock(
                ordinal=0,
                kind=BlockKind.PARAGRAPH,
                level=0,
                text="Primeiro paragrafo.",
                original_text="Primeiro parágrafo original.",
                section_path=CH1,
            ),
            CanonicalBlock(
                ordinal=1,
                kind=BlockKind.PARAGRAPH,
                level=0,
                text="Segundo paragrafo.",
                original_text="Segundo parágrafo original.",
                section_path=CH1,
            ),
        )
        doc = CanonicalDocument(source_type=SourceType.EPUB, blocks=blocks)
        # janela grande o bastante para juntar os dois parágrafos num único chunk.
        nodes = chunk_document(
            doc, ChunkingParams(child_target_tokens=1000, parent_target_tokens=1000)
        )
        assert len(nodes) == 2  # 1 pai + 1 filho, ambos cobrindo os dois parágrafos
        for node in nodes:
            assert "Primeiro parágrafo original." in node.original_text
            assert "Segundo parágrafo original." in node.original_text
            assert node.original_text.count("Primeiro parágrafo original.") == 1
            assert node.text != node.original_text
