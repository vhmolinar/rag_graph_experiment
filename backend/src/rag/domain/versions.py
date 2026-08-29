"""Registros imutáveis de versão (SPEC §6).

Toda configuração que afeta uma resposta é versionada. Uma reindexação cria novos
registros; registros antigos nunca são alterados.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PositiveInt


def utcnow() -> datetime:
    """Relógio padrão do domínio (timezone-aware, UTC)."""
    return datetime.now(UTC)


class VersionRecord(BaseModel):
    """Base imutável para todos os registros de versão."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    label: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime


class ExtractionVersion(VersionRecord):
    """Versão do extrator (adapter Docling + schema canônico)."""


class ChunkingVersion(VersionRecord):
    """Parâmetros de chunking: tamanho, sobreposição, expansão parental."""


class EmbeddingVersion(VersionRecord):
    """Modelo de embedding e dimensão registrada."""

    model_name: str = Field(min_length=1)
    dimensions: PositiveInt


class ModelEndpointVersion(VersionRecord):
    """Endpoint de modelo usado para gerar respostas/rankings."""

    endpoint_kind: Literal["embedding", "reranker", "generator"]
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)


class PromptVersion(VersionRecord):
    """Versão de prompt (template identificado por hash do conteúdo)."""

    template_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class RetrievalPolicyVersion(VersionRecord):
    """Parâmetros de recuperação: top_k, constante RRF, rerank_top_n, diversidade."""
