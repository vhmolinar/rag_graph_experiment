"""Contratos dos provedores de modelos (SPEC §11).

O domínio define Protocols; adapters HTTP concretos vivem em `rag.adapters`.
Nenhum tipo de SDK de modelo atravessa estas fronteiras.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.answer import EvidenceRef, GeneratedAnswer
from rag.domain.enums import Depth
from rag.domain.query import MAX_SUBQUESTIONS

MAX_PLANNED_ALIASES = 50
MAX_PLANNED_CONCEPTS = 50


class GenerationRequest(BaseModel):
    """Blocos separados do prompt dissertativo (SPEC §9.3)."""

    model_config = ConfigDict(frozen=True)

    system_policy: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    question: str = Field(min_length=1)
    scope_description: str = Field(min_length=1)
    evidences: list[EvidenceRef] = Field(min_length=1)
    depth: Depth
    session_context: str | None = None
    prompt_version_id: UUID | None = None


class PlanningRequest(BaseModel):
    """Pedido ao provedor de planejamento (SPEC §8.2, T10).

    A fase de planejamento não tem evidências — por isso o contrato é separado
    de `GenerationRequest` (NOTES.md §10.11 item 4).
    """

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, max_length=4000)
    depth: Depth
    prompt_version_id: UUID | None = None


class PlannedQuery(BaseModel):
    """Sugestão do provedor de planejamento para enriquecer o plano.

    Nunca decide intenção/estratégia por sí só — o planejador integra a
    sugestão dentro do plano determinístico. `subquestions` é limitada
    (`MAX_SUBQUESTIONS`); `aliases`/`concept_labels` também têm limite, validado
    aqui (falha fechada se o provedor violar o contrato).
    """

    model_config = ConfigDict(frozen=True)

    semantic_query: str | None = Field(default=None, min_length=1, max_length=4000)
    subquestions: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_SUBQUESTIONS)
    aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_PLANNED_ALIASES)
    concept_labels: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_PLANNED_CONCEPTS)


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class RerankerProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


@runtime_checkable
class GeneratorProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GeneratedAnswer: ...


@runtime_checkable
class PlannerProvider(Protocol):
    """Geração limitada de subperguntas/aliases no planejamento (SPEC §8.2).

    Só enriquece o plano determinístico (NOTES.md §10.11 item 3/4); nunca
    decide intenção ou estratégia.
    """

    async def plan(self, request: PlanningRequest) -> PlannedQuery: ...
