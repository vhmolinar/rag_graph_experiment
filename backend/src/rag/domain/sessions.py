"""Sessões efêmeras e contexto de sessão (SPEC §10.3, AC-13; T14/T15).

A fase 1 a sessão é efêmera (SPEC §3.2, sem memória persistente): identidade e
timestamps (`POST/GET/DELETE /sessions`, T14) + histórico limitado e reescrita
de follow-up para pergunta autônoma (T15, AC-13). Cada execução registra a
pergunta original e a autônoma resultante (`session_entries`).

A reescrita é determinística e pura (decisão do usuário em T15 — NOTAS.md
§10.16): resolve referências anafóricas (ordinais, demonstrativos, pronomes)
contra o histórico da sessão (perguntas + projeção truncada das respostas,
decisão do usuário) e o catálogo de obras/autores. Nunca adivina: uma
referência não resolvida fica como estan e a pergunta autônoma coincide com a
pergunta original.

Nenhum tipo de ORM, FastAPI ou SDK de modelo atravessa esta fronteira
(exercitado em `tests/unit/test_domain_purity.py`).
"""

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from rag.domain.query import MAX_QUESTION_LENGTH
from rag.domain.versions import utcnow

MAX_SESSION_HISTORY = 20  # ponto de calibração (NOTAS.md §4)
MAX_ANSWER_CONTEXT_CHARS = 2000  # projeção truncada da resposta no contexto
MAX_SESSION_PROMPT_CONTEXT_CHARS = 4000  # contexto de sessão no prompt de geração

_ORDINAL_RANK = {
    "primeiro": 1,
    "primeira": 1,
    "segundo": 2,
    "segunda": 2,
    "terceiro": 3,
    "terceira": 3,
    "quarto": 4,
    "quarta": 4,
    "quinto": 5,
    "quinta": 5,
    "sexto": 6,
    "sexta": 6,
    "sétimo": 7,
    "sétima": 7,
    "oitavo": 8,
    "oitava": 8,
    "noveno": 9,
    "novena": 9,
    "décimo": 10,
    "décima": 10,
}
_KIND_NOUNS: dict[str, Literal["obra", "autor"]] = {
    "obra": "obra",
    "obras": "obra",
    "livro": "obra",
    "livros": "obra",
    "autor": "autor",
    "autores": "autor",
    "escritor": "autor",
    "escritores": "autor",
}

_ORDINAL_KIND_RE = re.compile(
    r"\b(?:(?:o|a|os|as)\s+)?(?:primeiro|primeira|segundo|segunda|terceiro|terceira|"
    r"quarto|quarta|quinto|quinta|sexto|sexta|sétimo|sétima|oitavo|oitava|noveno|"
    r"novena|décimo|décima)\s+"
    r"(?:obra|obras|livro|livros|autor|autores|escritor|escritores)\b",
    re.IGNORECASE,
)
_DEMONSTRATIVE_KIND_RE = re.compile(
    r"\b(?:essa|esse|esta|este|aquella|aquele)\s+"
    r"(?:obra|obras|livro|livros|autor|autores|escritor|escritores)\b",
    re.IGNORECASE,
)
_BARE_DEMONSTRATIVE_RE = re.compile(r"\b(?:isso|isto)\b", re.IGNORECASE)
_PRONOUN_RE = re.compile(r"\b(?:ele|ela|eles|elas)\b", re.IGNORECASE)


class Session(BaseModel):
    """Sessão efêmera de consulta. Sem memória persistente (SPEC §3.2)."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)
    last_activity_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _timestamps_coherent(self) -> Self:
        if self.last_activity_at < self.created_at:
            raise ValueError("last_activity_at não pode preceder created_at")
        return self


class SessionEntry(BaseModel):
    """Rodada persistida da sessão (tabela `session_entries`, T03)."""

    model_config = ConfigDict(frozen=True)

    session_id: UUID
    ordinal: int = Field(ge=0)
    question_original: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    question_anonymized: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    rewritten_query: str | None = Field(default=None, max_length=MAX_QUESTION_LENGTH)
    answer_run_id: UUID | None = None
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)


class SessionTurn(BaseModel):
    """Rodada de contexto da sessão para a reescrita.

    `answer_text` é uma projeção TRUNCADA da resposta da rodada (via
    `answer_run_id` da `SessionEntry`), não o texto integral — serve para
    localizar obras/autores mencionados nas respostas (decisão do usuário em
    T15, NOTAS.md §10.16).
    """

    model_config = ConfigDict(frozen=True)

    ordinal: int = Field(ge=0)
    question_original: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    rewritten_query: str | None = Field(default=None, max_length=MAX_QUESTION_LENGTH)
    answer_text: str | None = Field(default=None, max_length=MAX_ANSWER_CONTEXT_CHARS)


class SessionCatalogEntry(BaseModel):
    """Obra e autores do catálogo (ordem canônica) para resolver referências."""

    model_config = ConfigDict(frozen=True)

    work_id: UUID
    title: str = Field(min_length=1, max_length=500)
    authors: tuple[str, ...] = Field(default_factory=tuple)


class RewriteReference(BaseModel):
    """Referência resolvida na reescrita (AC-13: pergunta autônoma inspeccionável)."""

    model_config = ConfigDict(frozen=True)

    expression: str = Field(min_length=1, max_length=200)
    resolved_to: str = Field(min_length=1, max_length=2000)


class RewriteResult(BaseModel):
    """Resultado da reescrita de follow-up para pergunta autônoma (AC-13)."""

    model_config = ConfigDict(frozen=True)

    autonomous_question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    references: tuple[RewriteReference, ...] = Field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.references)


@dataclass(frozen=True)
class _Referent:
    kind: Literal["obra", "autor"]
    label: str


def _unaccent(text: str) -> str:
    """Remove acentos via decomposição Unicode (NFD) — mesmo padrão do domínio."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def _mentions_title(text: str, title: str) -> bool:
    return _unaccent(title.lower()) in _unaccent(text.lower())


def _extract_referents(
    history: tuple[SessionTurn, ...],
    catalog: Mapping[str, SessionCatalogEntry],
) -> list[_Referent]:
    """Obras e autores do catálogo mencionados no histórico, em ordem de aparición.

    Varre perguntas, reescritas e projeção de respostas de cada rodada (em
    ordem). Quando um título canônico aparece, a obra e os seus autores (ordem
    canônica do catálogo) viram referentes — sem duplicados.
    """
    referents: list[_Referent] = []
    seen: set[tuple[str, str]] = set()
    for turn in history:
        for text in (turn.question_original, turn.rewritten_query or "", turn.answer_text or ""):
            if not text:
                continue
            for entry in catalog.values():
                if not _mentions_title(text, entry.title):
                    continue
                if ("obra", entry.title) not in seen:
                    seen.add(("obra", entry.title))
                    referents.append(_Referent("obra", entry.title))
                for author in entry.authors:
                    if ("autor", author) not in seen:
                        seen.add(("autor", author))
                        referents.append(_Referent("autor", author))
    return referents


def _referent_by_rank(
    referents: list[_Referent],
    kind: Literal["obra", "autor"],
    *,
    rank: int | None,
) -> _Referent | None:
    """Referente da `kind`: o enésimo (1-based) em ordem de aparición, ou o
    mais recente quando `rank` for None. Fora do alcance → None (unresolved)."""
    of_kind = [r for r in referents if r.kind == kind]
    if rank is None:
        return of_kind[-1] if of_kind else None
    if 1 <= rank <= len(of_kind):
        return of_kind[rank - 1]
    return None


def _resolve_ordinal(match: re.Match[str], referents: list[_Referent]) -> str | None:
    words = match.group(0).split()
    rank = _ORDINAL_RANK[words[-2].lower()]
    kind = _KIND_NOUNS[words[-1].lower()]
    referent = _referent_by_rank(referents, kind, rank=rank)
    return referent.label if referent is not None else None


def _resolve_demonstrative_kind(match: re.Match[str], referents: list[_Referent]) -> str | None:
    kind = _KIND_NOUNS[match.group(0).split()[-1].lower()]
    referent = _referent_by_rank(referents, kind, rank=None)
    return referent.label if referent is not None else None


def _resolve_bare_demonstrative(match: re.Match[str], referents: list[_Referent]) -> str | None:
    # "isso"/"isto" → a obra mais recente (tópico da última rodada); sem obra,
    # o referente mais recente de qualquer tipo.
    referent = _referent_by_rank(referents, "obra", rank=None)
    if referent is None and referents:
        referent = referents[-1]
    return referent.label if referent is not None else None


def _resolve_pronoun(match: re.Match[str], referents: list[_Referent]) -> str | None:
    if match.group(0).lower() in ("ele", "ela"):
        return referents[-1].label if referents else None
    return None  # eles/elas (plural): não há referentes plurais no conjunto atual


_Resolver = Callable[[re.Match[str], list[_Referent]], str | None]


def _apply_pattern(
    text: str,
    pattern: re.Pattern[str],
    resolver: _Resolver,
    referents: list[_Referent],
    resolved: list[RewriteReference],
) -> str:
    def _repl(match: re.Match[str]) -> str:
        replacement = resolver(match, referents)
        if replacement is None:
            return match.group(0)
        # Defensivo: a pergunta autônoma nunca pode exceder o limite do request.
        if len(text) - len(match.group(0)) + len(replacement) > MAX_QUESTION_LENGTH:
            return match.group(0)
        if match.group(0)[0].isupper() and replacement[0].islower():
            replacement = replacement[0].upper() + replacement[1:]
        resolved.append(RewriteReference(expression=match.group(0), resolved_to=replacement))
        return replacement

    return pattern.sub(_repl, text)


def rewrite_follow_up(
    question: str,
    history: tuple[SessionTurn, ...],
    catalog: Mapping[str, SessionCatalogEntry],
) -> RewriteResult:
    """Resolve referências anafóricas da pergunta contra o histórico da sessão.

    Determinística, sem modelo (NOTAS.md §10.16). Detecta, em ordem de
    especificidade: ordinal + substantivo ("o segundo autor"), demonstrativo +
    substantivo ("essa obra"), demonstrativo puro ("isso"/"isto") e pronomes
    ("ele"/"ela"). Referentes são obras e autores do catálogo mencionados no
    histórico (perguntas + respostas), em ordem de aparición. Uma referência
    não resolvida fica como estan — a pergunta autônoma nunca adivina.
    """
    if not history:
        return RewriteResult(autonomous_question=question)
    referents = _extract_referents(history, catalog)
    if not referents:
        return RewriteResult(autonomous_question=question)

    resolved: list[RewriteReference] = []
    text = question
    for pattern, resolver in (
        (_ORDINAL_KIND_RE, _resolve_ordinal),
        (_DEMONSTRATIVE_KIND_RE, _resolve_demonstrative_kind),
        (_BARE_DEMONSTRATIVE_RE, _resolve_bare_demonstrative),
        (_PRONOUN_RE, _resolve_pronoun),
    ):
        text = _apply_pattern(text, pattern, resolver, referents, resolved)

    return RewriteResult(autonomous_question=text, references=tuple(resolved))
