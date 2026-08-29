"""Adapter HTTP de embeddings compatível com OpenAI (T06).

Cliente mínimo: `POST {base_url}/embeddings`, autenticação Bearer via
variável de ambiente. Deliberadamente SEM retries, circuit breaker ou
limite de concorrência — esses são o valor agregado explícito de T07
(NOTES.md §10.6 item 6), que deve enriquecer este mesmo adapter, não
substituí-lo. Validação de dimensão contra a `EmbeddingVersion` registrada
acontece na camada de persistência (`PassagesRepository`), não aqui — este
módulo só valida que a resposta do endpoint é internamente consistente.
"""

import httpx
from pydantic import SecretStr
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
    timeout_seconds: float = 30.0


def _headers(settings: EmbeddingEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


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
            embeddings = [[float(v) for v in item["embedding"]] for item in payload["data"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelResponseError(
                "Resposta do endpoint de embeddings em formato inesperado.", cause=exc
            ) from exc

        if len(embeddings) != len(texts):
            raise ModelResponseError(
                "Quantidade de embeddings retornada não corresponde à quantidade "
                "de textos enviados.",
                context={"esperado": str(len(texts)), "recebido": str(len(embeddings))},
            )
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
