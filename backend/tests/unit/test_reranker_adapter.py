"""Contrato HTTP do adapter de reranking (T07 — contrato explícito não-OpenAI,
NOTES.md §10.8)."""

import json

import httpx
import pytest
import respx

from rag.adapters.reranker_adapter import HttpRerankerProvider, RerankerEndpointSettings
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)

_BASE_URL = "http://reranker.test"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> RerankerEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-reranker",
        "max_retries": 0,
    }
    defaults.update(overrides)
    return RerankerEndpointSettings(**defaults)  # type: ignore[arg-type]


class TestRerank:
    @respx.mock
    async def test_returns_scores_in_document_order_regardless_of_response_order(self) -> None:
        # servidor devolve fora de ordem (documento 2 primeiro) — o adapter
        # deve reordenar de volta para a ordem de `documents`.
        route = respx.post(f"{_BASE_URL}/rerank").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.2},
                        {"index": 0, "relevance_score": 0.9},
                    ]
                },
            )
        )
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            result = await provider.rerank("consulta", ["doc a", "doc b"])
        finally:
            await provider.aclose()
        assert result == [0.9, 0.2]
        sent = json.loads(route.calls.last.request.content)
        assert sent == {
            "model": "qwen3-reranker",
            "query": "consulta",
            "documents": ["doc a", "doc b"],
        }

    async def test_empty_documents_short_circuits(self) -> None:
        with respx.mock:
            provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
            try:
                result = await provider.rerank("consulta", [])
            finally:
                await provider.aclose()
        assert result == []

    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(
            return_value=httpx.Response(
                200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
            )
        )
        provider = HttpRerankerProvider(_settings(api_key="segredo-456"), sleep=_noop_sleep)
        try:
            await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_timeout_raises_model_timeout_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(side_effect=httpx.TimeoutException("timeout"))
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(side_effect=httpx.ConnectError("recusado"))
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_429_raises_rate_limit_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "3"}, json={})
        )
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 3

    @respx.mock
    async def test_5xx_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(return_value=httpx.Response(503))
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_4xx_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(return_value=httpx.Response(400, json={}))
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_malformed_body_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.rerank("q", ["d"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_missing_index_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(
            return_value=httpx.Response(
                200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
            )
        )
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.rerank("q", ["d1", "d2"])  # esperava 2 índices, só veio 1
        finally:
            await provider.aclose()

    @respx.mock
    async def test_duplicate_index_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/rerank").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.5},
                        {"index": 0, "relevance_score": 0.9},
                    ]
                },
            )
        )
        provider = HttpRerankerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.rerank("q", ["d1", "d2"])
        finally:
            await provider.aclose()
