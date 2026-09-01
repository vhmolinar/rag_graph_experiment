"""Contratos dos provedores de modelos (SPEC §11).

O domínio define Protocols; adapters HTTP concretos vivem em `rag.adapters`.
Nenhum tipo de SDK de modelo atravessa estas fronteiras.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.answer import EvidenceRef, GeneratedAnswer
from rag.domain.enums import Depth
from rag.domain.versions import EmbeddingVersion


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


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def embedding_version(self) -> EmbeddingVersion: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class RerankerProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


@runtime_checkable
class GeneratorProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GeneratedAnswer: ...
