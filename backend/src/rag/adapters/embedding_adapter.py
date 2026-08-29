"""Adapter HTTP de embeddings compatível com OpenAI (T06).

Cliente mínimo: `POST {base_url}/embeddings`, autenticação Bearer via
variável de ambiente. Deliberadamente SEM retries, circuit breaker ou
limite de concorrência — esses são o valor agregado explícito de T07
(NOTES.md §10.6 item 6), que deve enriquecer este mesmo adapter, não
substituí-lo. Validação de dimensão contra a `EmbeddingVersion` registrada
acontece na camada de persistência (`PassagesRepository`), não aqui — este
módulo valida que a resposta do endpoint é internamente consistente: cada
item é validado por contrato Pydantic tipado, reordenado pelo campo `index`
da resposta (nunca pela ordem bruta do array `data` — correção T6-03) e
vetores com valor não finito (NaN/±Inf) são rejeitados antes de chegar ao
banco (correção T6-09).
"""

import math
from typing import Self
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)

_DEFAULT_RETRY_AFTER_SECONDS = 60


class EmbeddingEndpointSettings(BaseSettings):
    """Configuração do endpoint de embeddings (SPEC: compatível com OpenAI)."""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore")

    base_url: str = "http://localhost:8080/v1"
    api_key: SecretStr = SecretStr("")
    model: str = "qwen3-embedding"
    # T6-08: identifica uma revisão imutável dos pesos quando o operador do
    # endpoint a expõe (o contrato OpenAI-compatível não garante isso) — vai
    # para `EmbeddingVersion.params`, nunca altera o comportamento do cliente.
    model_revision: str = ""
    timeout_seconds: float = Field(default=30.0, gt=0)
    # T6-06: tamanho de lote configurável, nunca uma única requisição para o
    # livro inteiro; registrado em `EmbeddingVersion.params` para
    # reprodutibilidade (AC-15).
    batch_size: int = Field(default=64, gt=0)

    @field_validator("base_url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url deve ser uma URL http(s) válida")
        return value

    @model_validator(mode="after")
    def _https_required_with_credential(self) -> Self:
        # T6-04: nunca enviar Authorization: Bearer para um endpoint http:// —
        # a credencial trafegaria em texto claro numa rede não confiável.
        if self.api_key.get_secret_value() and not self.base_url.lower().startswith("https://"):
            raise ValueError(
                "EMBEDDING_BASE_URL deve usar https:// quando EMBEDDING_API_KEY está definido"
            )
        return self


def _headers(settings: EmbeddingEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class _EmbeddingItem(BaseModel):
    """Um item da resposta OpenAI-compatible, validado (T6-03/T6-09)."""

    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)

    @field_validator("embedding")
    @classmethod
    def _finite(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(v) for v in value):
            raise ValueError("embedding contém valor não finito (NaN/Inf)")
        return value


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_EmbeddingItem] = Field(min_length=1)


class OpenAiCompatibleEmbeddingProvider:
    """Implementa `rag.domain.providers.EmbeddingProvider` via um endpoint
    HTTP compatível com OpenAI (`POST /embeddings`)."""

    def __init__(
        self,
        settings: EmbeddingEndpointSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            headers=_headers(settings),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                "/embeddings", json={"model": self._settings.model, "input": texts}
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(cause=exc) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(cause=exc) from exc

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            try:
                retry_after = (
                    int(retry_after_header) if retry_after_header else _DEFAULT_RETRY_AFTER_SECONDS
                )
            except ValueError:
                retry_after = _DEFAULT_RETRY_AFTER_SECONDS
            raise RateLimitError(
                retry_after_seconds=retry_after, context={"status": str(response.status_code)}
            )
        if response.status_code >= 500:
            raise ModelUnavailableError(context={"status": str(response.status_code)})
        if response.status_code >= 400:
            raise ModelResponseError(context={"status": str(response.status_code)})

        try:
            payload = response.json()
            parsed = _EmbeddingResponse.model_validate(payload)
        except ValueError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de embeddings em formato inesperado.", cause=exc
            ) from exc

        if len(parsed.data) != len(texts):
            raise ModelResponseError(
                "Quantidade de embeddings retornada não corresponde à quantidade "
                "de textos enviados.",
                context={"esperado": str(len(texts)), "recebido": str(len(parsed.data))},
            )
        # T6-03: a ordem de `data` NÃO é confiável — reordena pelo campo
        # `index` de cada item, exigindo cobertura exata de 0..len(texts)-1
        # (sem repetição, sem lacuna, sem índice fora do intervalo).
        by_index = {item.index: item.embedding for item in parsed.data}
        if set(by_index) != set(range(len(texts))):
            raise ModelResponseError(
                "Índices da resposta de embeddings ausentes, repetidos ou fora do intervalo "
                "esperado.",
                context={
                    "esperado": f"0..{len(texts) - 1}",
                    "recebido": ",".join(str(i) for i in sorted(by_index)),
                },
            )
        embeddings = [by_index[i] for i in range(len(texts))]
        dimensions = {len(vec) for vec in embeddings}
        if len(dimensions) > 1:
            raise ModelResponseError(
                "Embeddings retornados com dimensões inconsistentes entre si.",
                context={"dimensoes": ",".join(str(d) for d in sorted(dimensions))},
            )
        return embeddings

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        (embedding,) = await self._embed([text])
        return embedding
