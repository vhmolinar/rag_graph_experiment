"""Retries transitórios, circuit breaker, concorrência e secret file no
adapter de embeddings (T07 — enriquece o adapter de T06, NOTES.md §10.6
item 6). Contrato HTTP básico (formato de payload, dimensão etc.) já é
coberto por `test_embedding_adapter.py`."""

import asyncio
from pathlib import Path

import httpx
import pytest
import respx

from rag.adapters.embedding_adapter import (
    EmbeddingEndpointSettings,
    OpenAiCompatibleEmbeddingProvider,
)
from rag.domain.errors import ModelResponseError, ModelUnavailableError

_BASE_URL = "http://embeddings.test/v1"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> EmbeddingEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-embedding",
        "retry_backoff_seconds": 0.001,
    }
    defaults.update(overrides)
    return EmbeddingEndpointSettings(**defaults)  # type: ignore[arg-type]


class TestRetry:
    @respx.mock
    async def test_retries_transient_5xx_then_succeeds(self) -> None:
        route = respx.post(f"{_BASE_URL}/embeddings")
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]}),
        ]
        provider = OpenAiCompatibleEmbeddingProvider(_settings(max_retries=2), sleep=_noop_sleep)
        try:
            result = await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()
        assert result == [[0.1, 0.2]]
        assert route.call_count == 3

    @respx.mock
    async def test_gives_up_after_max_retries(self) -> None:
        route = respx.post(f"{_BASE_URL}/embeddings").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatibleEmbeddingProvider(_settings(max_retries=1), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()
        assert route.call_count == 2  # tentativa inicial + 1 retry

    @respx.mock
    async def test_4xx_is_not_retried(self) -> None:
        route = respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(400, json={})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings(max_retries=3), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()
        assert route.call_count == 1


class TestCircuitBreaker:
    @respx.mock
    async def test_opens_after_threshold_and_rejects_without_http_call(self) -> None:
        route = respx.post(f"{_BASE_URL}/embeddings").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatibleEmbeddingProvider(
            _settings(
                max_retries=0,
                circuit_breaker_failure_threshold=2,
                circuit_breaker_reset_seconds=999.0,
            ),
            sleep=_noop_sleep,
        )
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.embed_documents(["um"])
            with pytest.raises(ModelUnavailableError):
                await provider.embed_documents(["dois"])
            calls_before = route.call_count
            with pytest.raises(ModelUnavailableError):
                await provider.embed_documents(["tres"])
            # circuito aberto: a terceira chamada não bate na rede
            assert route.call_count == calls_before
        finally:
            await provider.aclose()


class TestConcurrencyLimit:
    @respx.mock
    async def test_limits_in_flight_requests(self) -> None:
        in_flight = {"count": 0, "max": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            in_flight["count"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["count"])
            await asyncio.sleep(0.01)
            in_flight["count"] -= 1
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})

        respx.post(f"{_BASE_URL}/embeddings").mock(side_effect=handler)
        provider = OpenAiCompatibleEmbeddingProvider(
            _settings(max_concurrency=1), sleep=_noop_sleep
        )
        try:
            await asyncio.gather(
                provider.embed_query("a"),
                provider.embed_query("b"),
                provider.embed_query("c"),
            )
        finally:
            await provider.aclose()
        assert in_flight["max"] == 1


class TestSecretFileAuth:
    @respx.mock
    async def test_uses_api_key_loaded_from_secret_file(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "embedding_api_key"
        secret_file.write_text("chave-do-arquivo", encoding="utf-8")
        route = respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.1]}]})
        )
        settings = _settings(api_key_file=secret_file)
        provider = OpenAiCompatibleEmbeddingProvider(settings, sleep=_noop_sleep)
        try:
            await provider.embed_query("texto")
        finally:
            await provider.aclose()
        assert route.calls.last.request.headers["Authorization"] == "Bearer chave-do-arquivo"
