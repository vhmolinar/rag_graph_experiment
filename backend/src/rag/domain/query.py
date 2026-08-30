"""Contratos de consulta e planejamento (SPEC §8)."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.domain.enums import AnswerMode, Depth, Intent, SearchStrategy

MAX_QUESTION_LENGTH = 4000
MAX_SUBQUESTIONS = 5
MAX_FILTER_ITEMS = 200


class EditionFilter(BaseModel):
    """Filtros positivos e negativos por obra/edição.

    Invariante: um mesmo identificador nunca pode estar simultaneamente em
    inclusão e exclusão explícitas do mesmo nível.
    """

    model_config = ConfigDict(frozen=True)

    include_edition_ids: frozenset[UUID] = Field(default_factory=frozenset)
    exclude_edition_ids: frozenset[UUID] = Field(default_factory=frozenset)
    include_work_ids: frozenset[UUID] = Field(default_factory=frozenset)
    exclude_work_ids: frozenset[UUID] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def _no_include_exclude_conflict(self) -> Self:
        if self.include_edition_ids & self.exclude_edition_ids:
            raise ValueError("mesma edição não pode ser incluída e excluída ao mesmo tempo")
        if self.include_work_ids & self.exclude_work_ids:
            raise ValueError("mesma obra não pode ser incluída e excluída ao mesmo tempo")
        return self

    def is_empty(self) -> bool:
        return not (
            self.include_edition_ids
            or self.exclude_edition_ids
            or self.include_work_ids
            or self.exclude_work_ids
        )


class QueryRequest(BaseModel):
    """Request lógico da especificação §8.1 — independente de framework."""

    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    answer_mode: AnswerMode
    depth: Depth = Depth.STANDARD
    search_strategy: SearchStrategy = SearchStrategy.AUTOMATIC
    include_edition_ids: list[UUID] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    exclude_edition_ids: list[UUID] = Field(default_factory=list, max_length=MAX_FILTER_ITEMS)
    session_id: UUID | None = None

    @model_validator(mode="after")
    def _explicit_filters_disjoint(self) -> Self:
        if set(self.include_edition_ids) & set(self.exclude_edition_ids):
            raise ValueError("edição não pode aparecer em include e exclude simultaneamente")
        return self

    def explicit_filter(self) -> EditionFilter:
        return EditionFilter(
            include_edition_ids=frozenset(self.include_edition_ids),
            exclude_edition_ids=frozenset(self.exclude_edition_ids),
        )


MAX_LEXICAL_TERM_LENGTH = 200
MAX_LEXICAL_TERMS = 50


class LexicalQuery(BaseModel):
    """Consulta lexical estruturada (SPEC §8.4).

    Estruturada em vez de uma mini-linguagem em string única: mantém a
    responsabilidade de interpretar a pergunta do usuário no planejador
    (T10), que ainda não existe nesta tarefa. `phrase` exige correspondência
    exata e contígua; `required_terms` são obrigatórios (AND, com
    tolerância trigram individual); `excluded_terms` nunca podem aparecer.
    Nenhum campo aqui é interpolado como SQL — cada termo vira um parâmetro
    ligado separado na consulta (`LexicalSearchRepository`).
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    phrase: str | None = Field(default=None, min_length=1, max_length=MAX_QUESTION_LENGTH)
    required_terms: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_LEXICAL_TERMS)
    excluded_terms: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_LEXICAL_TERMS)
    trigram_threshold: float = Field(default=0.3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _terms_are_single_words(self) -> Self:
        """`required_terms`/`excluded_terms` são palavras isoladas (AND/NOT);
        sequências de várias palavras pertencem ao campo `phrase`. Também
        garante, por construção, que a tolerância trigram (que compara o
        termo contra cada palavra da passagem) nunca compara um termo
        multi-palavra contra uma única palavra do documento."""
        for term in (*self.required_terms, *self.excluded_terms):
            if not term:
                raise ValueError("termo lexical não pode ser vazio")
            if len(term) > MAX_LEXICAL_TERM_LENGTH:
                raise ValueError(f"termo lexical excede {MAX_LEXICAL_TERM_LENGTH} caracteres")
            if not term.isalnum():
                raise ValueError(
                    "termo lexical deve ser uma única palavra alfanumérica "
                    "(sem espaços ou pontuação); frases usam o campo 'phrase'"
                )
        return self

    @model_validator(mode="after")
    def _at_least_one_criterion(self) -> Self:
        if not self.phrase and not self.required_terms:
            raise ValueError("consulta lexical exige frase exata ou ao menos um termo obrigatório")
        return self

    @model_validator(mode="after")
    def _required_and_excluded_disjoint(self) -> Self:
        if set(self.required_terms) & set(self.excluded_terms):
            raise ValueError("termo não pode ser obrigatório e excluído ao mesmo tempo")
        return self


class QueryPlan(BaseModel):
    """Plano validado produzido pelo planejador (SPEC §8.2).

    `strategy` é sempre a estratégia RESOLVIDA: quando o request pede
    `automatic`, o planejador escolhe uma das outras três e registra a
    justificativa (SPEC §8.3).
    """

    model_config = ConfigDict(frozen=True)

    intent: Intent
    lexical_query: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    semantic_query: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    strategy: SearchStrategy
    justification: str = Field(min_length=1, max_length=2000)
    subquestions: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_SUBQUESTIONS)
    aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    concept_labels: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    inferred_filters: EditionFilter = Field(default_factory=EditionFilter)
    needs_diversity: bool = False
    needs_hierarchical: bool = False

    @model_validator(mode="after")
    def _strategy_resolved(self) -> Self:
        if self.strategy is SearchStrategy.AUTOMATIC:
            raise ValueError(
                "QueryPlan.strategy deve ser a estratégia resolvida, nunca 'automatic'"
            )
        return self
