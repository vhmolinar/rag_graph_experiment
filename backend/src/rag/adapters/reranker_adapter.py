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

import httpx
from pydantic_settings import SettingsConfigDict

from rag.adapters.model_settings import ModelAuthSettings, ResilienceSettings
from rag.adapters.resilience import CircuitBreaker, SleepFn, call_with_resilience
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)

_DEFAULT_RETRY_AFTER_SECONDS = 60


class RerankerEndpointSettings(ModelAuthSettings, ResilienceSettings):
    """Configuração do endpoint de reranking (contrato explícito, não OpenAI)."""

    model_config = SettingsConfigDict(env_prefix="RERANKER_", extra="ignore")

    base_url: str = "http://localhost:8002"
    model: str = "qwen3-reranker"
    timeout_seconds: float = 30.0


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
            results = payload["results"]
            by_index = {int(item["index"]): float(item["relevance_score"]) for item in results}
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelResponseError(
                "Resposta do endpoint de reranking em formato inesperado.", cause=exc
            ) from exc

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
