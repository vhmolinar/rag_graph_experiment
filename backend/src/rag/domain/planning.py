"""Planejamento de consulta: intenção, estratégia, filtros naturais (SPEC §8.2-§8.3, T10).

O núcleo de planejamento é determinístico e puro (SPEC §8.2: "O planejador
produz uma estrutura validada"): a classificação de intenção, a construção da
consulta lexical estruturada, a resolução de estratégia, a diversidade
adaptativa, os filtros naturais e a prioridade de filtros explícitos não
dependem de um modelo (NOTES.md §10.11 item 3). A chamada ao modelo fica
reservada à geração limitada de subperguntas/aliases (`PlannerProvider`,
estratégia `expanded`).

Filtros inferidos NUNCA substituem filtros explícitos; exclusões explícitas
prevalecem sobre inclusões inferidas (SPEC §8.2) — `merge_filters` aplica essa
regra de prioridade antes de executar a recuperação.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from rag.domain.enums import Intent, SearchStrategy
from rag.domain.query import EditionFilter, LexicalQuery, StrategyExplanation

_INTENT_SIGNAL = "intenção={}"

# Palavras de conteúdo ignoradas na construção da consulta lexical (heurística
# calibrable — NOTES.md §4; não é um analizador morfológico).
_STOPWORDS = frozenset(
    {
        # artículos, determinantes e pronomes
        "a",
        "o",
        "os",
        "as",
        "um",
        "uma",
        "uns",
        "umas",
        "meu",
        "minha",
        "meus",
        "minhas",
        "teu",
        "tua",
        "teus",
        "tuas",
        "seu",
        "sua",
        "seus",
        "suas",
        "nosso",
        "nossa",
        "nossos",
        "nossas",
        "este",
        "esta",
        "estes",
        "estas",
        "esse",
        "essa",
        "esses",
        "essas",
        "aquelle",
        "aquella",
        "aques",
        "aquelas",
        "isto",
        "isso",
        "eu",
        "tu",
        "ele",
        "ela",
        "vos",
        "eles",
        "elas",
        "me",
        "te",
        "lhe",
        "lhes",
        "lo",
        "la",
        "los",
        "las",
        "aqui",
        "ali",
        "lá",
        # preposições, conjunções e partículas
        "de",
        "da",
        "do",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nas",
        "com",
        "sem",
        "para",
        "por",
        "sobre",
        "entre",
        "desde",
        "até",
        "ate",
        "ao",
        "aos",
        "à",
        "ou",
        "mas",
        "mais",
        "que",
        "se",
        "como",
        "porque",
        "pois",
        "logo",
        "assim",
        "asim",
        "já",
        "ja",
        "também",
        "tambem",
        "apenas",
        "muito",
        "poco",
        "pouco",
        "menos",
        "maior",
        "cada",
        "todo",
        "toda",
        "todos",
        "todas",
        "algum",
        "alguma",
        "alguns",
        "algumas",
        "ninguém",
        "ninguem",
        "ningua",
        "ninguns",
        "ninguas",
        "outro",
        "outra",
        "outros",
        "outras",
        "nao",
        "só",
        "so",
        # verbos copulativos/auxiliares e modais
        "é",
        "ser",
        "estar",
        "haver",
        "ter",
        "ten",
        "tem",
        "têm",
        "tém",
        "foi",
        "era",
        "estava",
        "ficou",
        "ficar",
        "fos",
        "fora",
        "foram",
        "será",
        "sera",
        "estará",
        "estara",
        "tinho",
        "tinha",
        "tivera",
        "tinhe",
        "poderia",
        "pode",
        "podem",
        "deve",
        "devem",
        "pos",
    }
)

_QUESTION_WORDS = frozenset(
    {
        "qual",
        "quais",
        "quem",
        "onde",
        "como",
        "quando",
        "por",
        "que",
        "o",
        "quanto",
        "quanta",
        "quantos",
        "quantas",
        "quê",
        "quão",
        "porque",
        "de",
        "em",
        "no",
        "na",
    }
)

_GENERIC_NOUNS = frozenset(
    {
        "livro",
        "livros",
        "obra",
        "obras",
        "capítulo",
        "capitulo",
        "capítulos",
        "capitulos",
        "página",
        "pagina",
        "páginas",
        "paginas",
        "texto",
        "textos",
        "seção",
        "secao",
        "seções",
        "secoes",
        "autor",
        "autores",
        "escritor",
        "escritores",
        "volume",
        "volumes",
        "tomo",
        "tomos",
    }
)

_COMPARATIVE_STEMS = (
    "compar",
    "versus",
    "diferenc",
    "diferent",
    "difer",
    "ambos",
    "relação entre",
    "relacao entre",
    "em contraste",
    "ao contrário",
    "ao contrario",
)

_NAVIGATIONAL_STEMS = (
    "onde",
    "em que capítulo",
    "em qual capítulo",
    "em que obra",
    "em qual obra",
    "em que página",
    "em qual página",
    "em que seção",
    "em qual seção",
    "em que livro",
    "em qual livro",
    "localizar",
    "localiza",
    "procura",
    "buscar",
)

_CONCEPTUAL_STEMS = (
    "concep",
    "conceit",
    "ideia",
    "noção",
    "noao",
    "o que é",
    "o que e",
    "que é",
    "que e",
    "significa",
    "defini",
    "visão",
    "visao",
    "pensamento",
    "entende",
    "comprender",
    "tema",
    "filosofia",
    "perspectiva",
    "doutrina",
    "doctrina",
    "argumento",
    "teoria",
    "posição",
    "posicao",
)

_INCLUSION_CUES = (
    "só",
    "so",
    "somente",
    "apenas",
    "exclusivamente",
    "incluindo",
    "incluída",
    "incluida",
    "considerando",
)
_EXCLUSION_CUES = (
    "exceto",
    "excepto",
    "salvo",
    "sem",
    "excluindo",
    "excluída",
    "excluida",
    "fora de",
    "sem contar",
    "sem incluir",
)


def _unaccent(text: str) -> str:
    """Remove acentos via decomposição Unicode (NFD) — robusta para todos os
    diacríticos do português, sem tabela manual."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    """Normalização para correspondência de títulos: minúsculas e sem acentos."""
    return _unaccent(text.lower())


def _words(text: str) -> list[str]:
    """Palavras em minúscula, pontuação removida, acentos preservados."""
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def _content_words(question: str) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for word in _words(question):
        if len(word) <= 2:
            continue
        if word in _STOPWORDS or word in _QUESTION_WORDS or word in _GENERIC_NOUNS:
            continue
        if word in seen:
            continue
        seen.add(word)
        kept.append(word)
    return kept


def classify_intent(question: str) -> Intent:
    """Classifica a intenção da pergunta (SPEC §8.2, AC-07/AC-11).

    Heurística léxica em português, determinística. Ordem de verificação:
    comparativa (sinal mais específico), navegacional, conceitual, factual
    (padrão). NOTAS.md §10.11 item 3: não chama modelo.
    """
    text = question.lower()
    if any(stem in text for stem in _COMPARATIVE_STEMS):
        return Intent.COMPARATIVE
    if any(stem in text for stem in _NAVIGATIONAL_STEMS):
        return Intent.NAVIGATIONAL
    if any(stem in text for stem in _CONCEPTUAL_STEMS):
        return Intent.CONCEPTUAL
    return Intent.FACTUAL


def build_lexical_query(question: str, *, trigram_threshold: float = 0.3) -> LexicalQuery:
    """Construi a consulta lexical estruturada a partir da pergunta (T08).

    Extrai as palavras de conteúdo (removendo stopwords, palavras de pergunta e
    substantivos genéricos) e as expõe como `required_terms` (AND). Sem
    palavras de conteúdo, cai para `phrase` = pergunta inteira — o estágio
    semântico continua funcionando (NOTAS.md §10.11 item 9).
    """
    words = _content_words(question)
    if words:
        return LexicalQuery(required_terms=tuple(words), trigram_threshold=trigram_threshold)
    return LexicalQuery(phrase=question.strip(), trigram_threshold=trigram_threshold)


def build_semantic_query(question: str) -> str:
    """Consulta semântica padrão: a própria pergunta limpa (SPEC §8.2)."""
    return question.strip()


def resolve_strategy(
    requested: SearchStrategy, intent: Intent
) -> tuple[SearchStrategy, StrategyExplanation]:
    """Resolve a estratégia e produz a explicação estruturada (SPEC §8.3).

    `automatic` escolhe uma das outras três e registra a justificativa. Uma
    estratégia explícita é respeitada como tal (AC-07: filtros e estratégia
    do usuário prevalecem).
    """
    if requested is not SearchStrategy.AUTOMATIC:
        return requested, StrategyExplanation(
            requested=requested,
            chosen=requested,
            intent_signals=(_INTENT_SIGNAL.format(intent.value),),
            rationale=f"Estratégia definida explicitamente ({requested.value}); "
            f"intenção {intent.value}.",
        )

    chosen = _automatic_for_intent(intent)
    rationale = {
        Intent.FACTUAL: "Pergunta factual: relevância via fusão híbrida e reranking.",
        Intent.CONCEPTUAL: (
            "Pergunta conceitual: expansão via aliases e subperguntas para "
            "localizar argumentos distribuidos no acervo."
        ),
        Intent.COMPARATIVE: (
            "Pergunta comparativa: expansão para cobertura entre obras, com diversidade adaptativa."
        ),
        Intent.NAVIGATIONAL: ("Pergunta navegacional: localizar trechos literais exatos."),
    }[intent]
    return chosen, StrategyExplanation(
        requested=SearchStrategy.AUTOMATIC,
        chosen=chosen,
        intent_signals=(_INTENT_SIGNAL.format(intent.value),),
        rationale=rationale,
    )


def _automatic_for_intent(intent: Intent) -> SearchStrategy:
    return {
        Intent.FACTUAL: SearchStrategy.HYBRID,
        Intent.CONCEPTUAL: SearchStrategy.EXPANDED,
        Intent.COMPARATIVE: SearchStrategy.EXPANDED,
        Intent.NAVIGATIONAL: SearchStrategy.LITERAL,
    }[intent]


def diversity_for(intent: Intent) -> bool:
    """SPEC §8.6: factuais maximizam relevância; comparativas e conceituais
    aplicam limite flexível por edição (diversidade adaptativa)."""
    return intent in (Intent.COMPARATIVE, Intent.CONCEPTUAL)


def hierarchical_for(intent: Intent) -> bool:
    """SPEC §8.7: resumos/conceitos ajudam a localizar regiões promissoras em
    perguntas conceituais e comparativas (índice hierárquico de T11)."""
    return intent in (Intent.CONCEPTUAL, Intent.COMPARATIVE)


@dataclass(frozen=True)
class CatalogEntry:
    """Entrada do catálogo de obras para resolução de filtros naturais."""

    work_id: UUID
    title: str
    edition_ids: tuple[UUID, ...]


def _find_contiguous(tokens: list[str], needle: tuple[str, ...]) -> list[int]:
    """Índices (start) de todas as ocorrências contíguas de `needle`."""
    if not needle:
        return []
    matches: list[int] = []
    i = 0
    while i <= len(tokens) - len(needle):
        if tuple(tokens[i : i + len(needle)]) == needle:
            matches.append(i)
            i += len(needle)
        else:
            i += 1
    return matches


def _contains_phrase(window: list[str], cue_words: tuple[str, ...] | list[str]) -> bool:
    if len(cue_words) == 1:
        return cue_words[0] in window
    for i in range(len(window) - len(cue_words) + 1):
        if window[i : i + len(cue_words)] == list(cue_words):
            return True
    return False


def _polarity(tokens: list[str], start: int) -> bool | None:
    """Polaridade da menção: True = inclusão, False = exclusão, None = ambigua.

    Olha a janela de até 3 palavras ANTES da menção, com correspondência por
    PALAVRA (nunca substring — evita falsos positivos tipo "sem" dentro de
    "semana"). Sinais de exclusão prevalecem (mais específicos); sinais de
    inclusão limitam a seleção ("só", "somente", ...). Ausência de sinais é
    ambigüidade — a menção não é aplicada silenciosamente (NOTAS.md §10.11
    item 6).
    """
    window = tokens[max(0, start - 3) : start]
    for cue in _EXCLUSION_CUES:
        if _contains_phrase(window, cue.split()):
            return False
    for cue in _INCLUSION_CUES:
        if _contains_phrase(window, cue.split()):
            return True
    return None


def resolve_natural_filters(question: str, catalog: Mapping[str, CatalogEntry]) -> EditionFilter:
    """Resolve menções de obras na pergunta em filtros inferidos (SPEC §8.2).

    O catálogo mapeia título canônico normalizado → entrada. Só menções com
    polaridade explícita entram em `include_work_ids`/`exclude_work_ids`; uma
    menção sem sinais de polaridade (ou com sinais conflitantes) NUNCA é
    aplicada silenciosamente. Nível obra (NOTAS.md §10.11 item 8).
    """
    tokens = _words(question)
    norm_tokens = [_unaccent(t) for t in tokens]
    include: set[UUID] = set()
    exclude: set[UUID] = set()
    for entry in catalog.values():
        title_tokens = tuple(_unaccent(w) for w in _words(entry.title))
        for start in _find_contiguous(norm_tokens, title_tokens):
            polarity = _polarity(tokens, start)
            if polarity is None:
                continue
            if polarity:
                include.add(entry.work_id)
            else:
                exclude.add(entry.work_id)
    conflicting = include & exclude
    include -= conflicting
    exclude -= conflicting
    return EditionFilter(
        include_work_ids=frozenset(include),
        exclude_work_ids=frozenset(exclude),
    )


def merge_filters(explicit: EditionFilter, inferred: EditionFilter) -> EditionFilter:
    """Filtro efetivo com prioridade EXPLÍCITA (SPEC §8.2, AC-07).

    Entradas inferidas nunca substituem filtros explícitos; exclusões
    explícitas prevalecem sobre inclusões inferidas (e simétrico). O resultado
    respeita as invariantes de `EditionFilter` (nunca o mesmo ID em include e
    exclude do mesmo nível). É o filtro que `RetrievalService` deve receber.
    """
    include_edition = (
        explicit.include_edition_ids | inferred.include_edition_ids
    ) - explicit.exclude_edition_ids
    exclude_edition = (
        explicit.exclude_edition_ids | inferred.exclude_edition_ids
    ) - explicit.include_edition_ids
    include_work = (
        explicit.include_work_ids | inferred.include_work_ids
    ) - explicit.exclude_work_ids
    exclude_work = (
        explicit.exclude_work_ids | inferred.exclude_work_ids
    ) - explicit.include_work_ids
    return EditionFilter(
        include_edition_ids=frozenset(include_edition),
        exclude_edition_ids=frozenset(exclude_edition),
        include_work_ids=frozenset(include_work),
        exclude_work_ids=frozenset(exclude_work),
    )
