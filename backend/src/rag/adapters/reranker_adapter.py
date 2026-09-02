"""Adapter HTTP de reranking (SPEC §11, T07).

Não existe convenção "compatível com OpenAI" para reranking (a API da
OpenAI não tem esse endpoint). Contrato explícito adotado — desvio
documentado em NOTES.md §10.8, seguindo a convenção difundida entre
servidores de reranking auto-hospedados (Cohere, Text Embeddings
Inference, Infinity):

    POST {base_url}/rerank
    {"model": ..., "query": ..., "documents": [...]}
    -> {"results": [{"index": int, "relevance_score": float}, ...]}

`rerank()` devolve as pontuações na MESMA ordem de `documents` (contrato
`RerankerProvider`, SPEC §11) — este módulo reordena a resposta por
`index`, não expõe a ordem de relevância do servidor.
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


class RerankerEndpointSettings(HttpEndpointSettings):
    """Configuração do endpoint de reranking (contrato explícito, não OpenAI).

    Reranking é uma operação idempotente (a mesma consulta + os mesmos
    documentos produzem as mesmas pontuações, sem efeito colateral), então
    retries transitórios são permitidos. A validação de URL/credencial vem de
    `HttpEndpointSettings`.
    """

    model_config = SettingsConfigDict(env_prefix="RERANKER_", extra="ignore")

    base_url: str = "http://localhost:8002"
    model: str = "qwen3-reranker"
    timeout_seconds: float = Field(default=30.0, gt=0)


class _RerankItem(BaseModel):
    """Um item da resposta de reranking, validado (T7-03).

    Exige índice inteiro não negativo e score **finito**: `NaN`/`Infinity`
    passariam por `float()` e contaminariam o ranking.
    """

    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    relevance_score: float

    @field_validator("relevance_score")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("relevance_score contém valor não finito (NaN/Inf)")
        return value


class _RerankResponse(BaseModel):
    """Envelope da resposta de reranking (T7-03)."""

    model_config = ConfigDict(extra="ignore")

    results: list[_RerankItem]


def _headers(settings: RerankerEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class HttpRerankerProvider:
    """Implementa `rag.domain.providers.RerankerProvider` via `POST /rerank`."""

    def __init__(
        self,
        settings: RerankerEndpointSettings,
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

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return await call_with_resilience(
            lambda: self._rerank_once(query, documents),
            operation_name="reranker.rerank",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def _rerank_once(self, query: str, documents: list[str]) -> list[float]:
        try:
            response = await self._client.post(
                "/rerank",
                json={"model": self._settings.model, "query": query, "documents": documents},
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
            parsed = _RerankResponse.model_validate(payload)
        except ValueError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de reranking em formato inesperado.", cause=exc
            ) from exc

        # T7-03: exige cardinalidade EXATA à entrada (n<len> item por
        # documento) e índice único cobrindo 0..n-1. Isso rejeita duplicatas
        # (inclusive para um único documento), lacunas e índices fora do
        # intervalo — o dict não pode sobrescrever silenciosamente resultados
        # repetidos.
        if len(parsed.results) != len(documents):
            raise ModelResponseError(
                "Resultados de reranking em quantidade diferente dos documentos enviados.",
                context={
                    "esperado": str(len(documents)),
                    "recebido": str(len(parsed.results)),
                },
            )
        by_index = {item.index: item.relevance_score for item in parsed.results}
        expected_indices = set(range(len(documents)))
        if set(by_index) != expected_indices:
            raise ModelResponseError(
                "Resposta de reranking não cobre exatamente os documentos enviados.",
                context={
                    "esperado": str(len(documents)),
                    "recebido": str(len(by_index)),
                },
            )
        return [by_index[i] for i in range(len(documents))]
