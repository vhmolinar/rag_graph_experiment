"""Adapter HTTP de embeddings compatível com OpenAI (T06, enriquecido em T07).

Cliente mínimo: `POST {base_url}/embeddings`, autenticação Bearer via
variável de ambiente ou secret file. Retries transitórios, circuit breaker
e limite de concorrência vêm de `rag.adapters.resilience` (T07,
NOTES.md §10.6 item 6): este módulo continua sendo o único adapter de
embeddings, apenas enriquecido, não substituído. Validação de dimensão
contra a `EmbeddingVersion` registrada acontece na camada de persistência
(`PassagesRepository`), não aqui — este módulo valida a resposta do endpoint
por contrato Pydantic tipado, reordena pelo campo `index` (nunca pela ordem
bruta de `data`) e rejeita vetores não finitos antes da persistência.
"""

import asyncio
import math

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import SettingsConfigDict

from rag.adapters.model_settings import HttpEndpointSettings
from rag.adapters.resilience import CircuitBreaker, SleepFn, call_with_resilience
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)

_DEFAULT_RETRY_AFTER_SECONDS = 60


class EmbeddingEndpointSettings(HttpEndpointSettings):
    """Configuração do endpoint de embeddings (SPEC: compatível com OpenAI).

    Validações de URL e credencial-sobre-https vêm de `HttpEndpointSettings`.
    """

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore")

    base_url: str = "http://localhost:8080/v1"
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
        sleep: SleepFn | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            headers=_headers(settings),
        )
        self._sleep_kwargs = {} if sleep is None else {"sleep": sleep}
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            reset_timeout_seconds=settings.circuit_breaker_reset_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await call_with_resilience(
            lambda: self._embed_once(texts),
            operation_name="embedding.embed",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def _embed_once(self, texts: list[str]) -> list[list[float]]:
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
