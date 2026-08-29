"""Chunker estrutural e configurável (T06; SPEC §7.3).

Função pura: consome `CanonicalDocument` (T05) e devolve uma lista plana de
`ChunkNode` — sem tocar banco, artifact store ou providers de modelo. A
persistência (mapear `section_path`/`page_index` para `Section`/`Page` já
gravados, criar `ChunkingVersion`, gerar embeddings) é responsabilidade de
`rag.application.index`.

Garantias do chunker:
- nunca mistura seções: um chunk pertence inteiramente a UMA seção-folha
  (mesmo `section_path` completo) — isso implica, a fortiori, nunca misturar
  capítulos diferentes;
- nunca corta uma frase: todo corte de janela (pai ou filho) acontece numa
  fronteira de sentença, nunca no meio de uma;
- hierarquia pai/filho: cada seção-folha vira uma ou mais janelas "pai"
  (`parent_target_tokens`); cada pai é sub-dividido em janelas "filho"
  (`child_target_tokens`, com sobreposição `child_overlap_tokens`) que
  referenciam o pai por índice. Só os filhos destinam-se a embedding — a
  persistência decide isso (`Passage.embedding_version_id` nulo nos pais);
- offsets recompõem o trecho original: para PDF/EPUB com páginas, o texto do
  nó é uma fatia exata de `CanonicalPage.text` (uma página) ou a
  concatenação de fatias de páginas consecutivas (múltiplas páginas); para
  EPUB (sem páginas), não há offsets — o texto é a junção das sentenças.

Contagem de tokens e fronteiras de sentença são heurísticas (nenhum
tokenizador real está no conjunto de dependências aprovado) — ver
NOTES.md §10.6 item 4. Calibração fica para o benchmark (T19).
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.canonical import BlockKind, CanonicalDocument

# Abreviaturas comuns em português que não terminam uma sentença mesmo
# seguidas de espaço e maiúscula (heurística, não exaustiva).
_ABBREVIATIONS = frozenset(
    {
        "sr",
        "sra",
        "sras",
        "srs",
        "dr",
        "dra",
        "prof",
        "profa",
        "ex",
        "art",
        "num",
        "p",
        "vol",
        "cap",
        "ed",
        "trad",
        "etc",
        "ltda",
        "cia",
        "av",
        "r",
    }
)
_SENTENCE_END_CHARS = ".!?…"


class ChunkingParams(BaseModel):
    """Parâmetros versionados do chunker (viram `ChunkingVersion.params`).

    Valores iniciais conservadores — calibração pertence ao benchmark (T19),
    não a este módulo (NOTES.md §4).
    """

    model_config = ConfigDict(frozen=True)

    child_target_tokens: int = Field(default=400, gt=0)
    child_overlap_tokens: int = Field(default=60, ge=0)
    parent_target_tokens: int = Field(default=1600, gt=0)


@dataclass(frozen=True)
class ChunkNode:
    """Um chunk (pai ou filho) antes da persistência.

    `index` é a posição no resultado de `chunk_document` — um substituto de
    id até a inserção no banco; `parent_index` referencia esse mesmo índice
    (`None` para nós pai). `page_start_index`/`page_end_index` são índices
    físicos (0-based) em `CanonicalDocument.pages`; `None` para EPUB.
    """

    index: int
    parent_index: int | None
    section_path: tuple[str, ...]
    text: str
    token_count: int
    page_start_index: int | None
    page_end_index: int | None
    char_start: int | None
    char_end: int | None


@dataclass(frozen=True)
class _Sentence:
    section_path: tuple[str, ...]
    page_index: int | None
    char_start: int | None
    char_end: int | None
    text: str


def approximate_token_count(text: str) -> int:
    """Heurística sem tokenizador real: ~1 token a cada 4 caracteres,
    nunca zero para texto não vazio (NOTES.md §10.6 item 4)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Spans (start, end) de sentenças em `text`, cobrindo tudo sem lacunas.

    Heurística por pontuação com guarda de abreviações comuns e iniciais
    isoladas ("J."); não é um tokenizador de sentenças real (NOTES.md §10.6
    item 4). Garante que a concatenação dos spans, na ordem, mais os
    caracteres de espaçamento entre eles, reconstitui `text` integralmente.
    """
    n = len(text)
    if n == 0:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    i = 0
    while i < n:
        if text[i] in _SENTENCE_END_CHARS:
            j = i + 1
            while j < n and text[j] in _SENTENCE_END_CHARS:
                j += 1
            if j >= n or text[j].isspace():
                word_start = i
                while word_start > start and text[word_start - 1].isalnum():
                    word_start -= 1
                word = text[word_start:i].lower()
                is_single_initial = len(word) == 1 and word.isalpha()
                if word not in _ABBREVIATIONS and not is_single_initial:
                    spans.append((start, j))
                    while j < n and text[j].isspace():
                        j += 1
                    start = j
                    i = j
                    continue
        i += 1
    if start < n:
        spans.append((start, n))
    return spans


def _leaf_runs(doc: CanonicalDocument) -> list[list[_Sentence]]:
    """Agrupa sentenças em listas contíguas por `section_path` completo —
    nunca mistura seções (a fortiori, nunca mistura capítulos)."""
    runs: list[list[_Sentence]] = []
    current_path: tuple[str, ...] | None = None
    current_run: list[_Sentence] = []
    for block in doc.blocks:
        if block.kind is BlockKind.HEADING:
            continue  # títulos definem o contexto; não viram sentenças
        if block.section_path != current_path:
            if current_run:
                runs.append(current_run)
            current_run = []
            current_path = block.section_path
        for local_start, local_end in split_sentences(block.text):
            sentence_text = block.text[local_start:local_end]
            if not sentence_text.strip():
                continue
            char_start = block.char_start + local_start if block.char_start is not None else None
            char_end = block.char_start + local_end if block.char_start is not None else None
            current_run.append(
                _Sentence(block.section_path, block.page_index, char_start, char_end, sentence_text)
            )
    if current_run:
        runs.append(current_run)
    return runs


def _windows(
    sentences: list[_Sentence], target_tokens: int, overlap_tokens: int
) -> list[list[_Sentence]]:
    """Agrupa sentenças em janelas de até `target_tokens`, nunca cortando
    uma sentença. `overlap_tokens` faz a próxima janela recuar e repetir as
    últimas sentenças da anterior."""
    windows: list[list[_Sentence]] = []
    n = len(sentences)
    i = 0
    while i < n:
        window: list[_Sentence] = []
        tokens = 0
        j = i
        while j < n:
            t = approximate_token_count(sentences[j].text)
            if window and tokens + t > target_tokens:
                break
            window.append(sentences[j])
            tokens += t
            j += 1
        windows.append(window)
        if j >= n:
            break
        back = j
        back_tokens = 0
        while back > i + 1 and back_tokens < overlap_tokens:
            back -= 1
            back_tokens += approximate_token_count(sentences[back].text)
        i = back
    return windows


def _slice_text(
    doc: CanonicalDocument, page_start: int, char_start: int, page_end: int, char_end: int
) -> str:
    """Reconstitui o texto exato entre dois offsets de página — uma fatia
    direta quando o chunk cabe numa única página; concatenação de fatias
    quando atravessa páginas (NOTES.md §10.6 item 3)."""
    pages_by_index = {p.physical_index: p for p in doc.pages}
    if page_start == page_end:
        return pages_by_index[page_start].text[char_start:char_end]
    parts = [pages_by_index[page_start].text[char_start:]]
    parts.extend(pages_by_index[idx].text for idx in range(page_start + 1, page_end))
    parts.append(pages_by_index[page_end].text[:char_end])
    return "\n".join(parts)


def _node_from_sentences(
    doc: CanonicalDocument, index: int, parent_index: int | None, sentences: list[_Sentence]
) -> ChunkNode:
    first, last = sentences[0], sentences[-1]
    if first.page_index is None:  # EPUB: sem endereçamento por página
        text = " ".join(s.text for s in sentences)
        page_start_index = page_end_index = char_start = char_end = None
    else:
        if (
            first.char_start is None
            or last.char_end is None
            or first.page_index is None
            or last.page_index is None
        ):
            raise ValueError(
                "Sentença com page_index mas sem char_start/char_end — "
                "CanonicalDocument viola o invariante de offsets por página."
            )
        page_start_index = first.page_index
        page_end_index = last.page_index
        char_start = first.char_start
        char_end = last.char_end
        text = _slice_text(doc, page_start_index, char_start, page_end_index, char_end)
    return ChunkNode(
        index=index,
        parent_index=parent_index,
        section_path=first.section_path,
        text=text,
        token_count=approximate_token_count(text),
        page_start_index=page_start_index,
        page_end_index=page_end_index,
        char_start=char_start,
        char_end=char_end,
    )


def default_section_header(section_path: tuple[str, ...]) -> str:
    """Parte do cabeçalho contextual derivável só do documento (seção e
    subseção); a camada de aplicação antepõe obra/edição."""
    return " > ".join(section_path)


def chunk_document(doc: CanonicalDocument, params: ChunkingParams) -> list[ChunkNode]:
    """Produz a lista plana de chunks pai/filho para `doc`.

    Cada seção-folha vira uma ou mais janelas-pai (`parent_target_tokens`,
    sem sobreposição entre pais); cada pai é sub-dividido em janelas-filho
    (`child_target_tokens`/`child_overlap_tokens`). A ordem do resultado
    coloca cada pai imediatamente antes de seus filhos.
    """
    nodes: list[ChunkNode] = []
    for run in _leaf_runs(doc):
        for parent_sentences in _windows(run, params.parent_target_tokens, 0):
            parent_index = len(nodes)
            nodes.append(_node_from_sentences(doc, parent_index, None, parent_sentences))
            child_windows = _windows(
                parent_sentences, params.child_target_tokens, params.child_overlap_tokens
            )
            for child_sentences in child_windows:
                child_index = len(nodes)
                nodes.append(_node_from_sentences(doc, child_index, parent_index, child_sentences))
    return nodes
