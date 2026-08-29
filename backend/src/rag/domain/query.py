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
