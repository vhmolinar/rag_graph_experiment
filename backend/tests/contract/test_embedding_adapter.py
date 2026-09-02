"""Contrato HTTP do adapter de embeddings compatível com OpenAI (T06)."""

import json

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from rag.adapters.embedding_adapter import (
    EmbeddingEndpointSettings,
    OpenAiCompatibleEmbeddingProvider,
)
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)

_BASE_URL = "http://embeddings.test/v1"
_HTTPS_BASE_URL = "https://embeddings.test/v1"


def _settings(**overrides: object) -> EmbeddingEndpointSettings:
    defaults: dict[str, object] = {"base_url": _BASE_URL, "model": "qwen3-embedding"}
    defaults.update(overrides)
    return EmbeddingEndpointSettings(**defaults)  # type: ignore[arg-type]


class TestEndpointSettings:
    def test_https_required_when_api_key_set(self) -> None:
        with pytest.raises(ValidationError, match="https"):
            _settings(api_key=SecretStr("segredo"))

    def test_https_with_api_key_is_accepted(self) -> None:
        settings = _settings(base_url=_HTTPS_BASE_URL, api_key=SecretStr("segredo"))
        assert settings.api_key.get_secret_value() == "segredo"

    def test_http_without_api_key_is_accepted(self) -> None:
        assert _settings().api_key.get_secret_value() == ""

    def test_invalid_base_url_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(base_url="not-a-url")

    def test_non_positive_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(timeout_seconds=0)

    def test_non_positive_batch_size_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(batch_size=0)


class TestEmbedDocuments:
    @respx.mock
    async def test_returns_vectors_for_each_text(self) -> None:
        route = respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"embedding": [0.1, 0.2], "index": 0},
                        {"embedding": [0.3, 0.4], "index": 1},
                    ]
                },
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            result = await provider.embed_documents(["um", "dois"])
        finally:
            await provider.aclose()
        assert result == [[0.1, 0.2], [0.3, 0.4]]
        assert route.called
        sent = route.calls.last.request
        assert sent.headers.get("Authorization") is None

    @respx.mock
    async def test_reorders_by_index_when_response_is_permuted(self) -> None:
        """T6-03: a ordem do array `data` não é confiável — só o índice é."""
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"embedding": [0.3, 0.4], "index": 1},
                        {"embedding": [0.1, 0.2], "index": 0},
                    ]
                },
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            result = await provider.embed_documents(["um", "dois"])
        finally:
            await provider.aclose()
        assert result == [[0.1, 0.2], [0.3, 0.4]]

    @respx.mock
    async def test_duplicate_index_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"embedding": [0.1, 0.2], "index": 0},
                        {"embedding": [0.3, 0.4], "index": 0},
                    ]
                },
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um", "dois"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_out_of_range_index_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"embedding": [0.1, 0.2], "index": 0},
                        {"embedding": [0.3, 0.4], "index": 5},
                    ]
                },
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um", "dois"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_missing_index_field_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_non_finite_value_raises_model_response_error(self) -> None:
        # `json=` não serializa NaN/Infinity (não são JSON estritamente válidos);
        # `json.loads` os aceita de volta como extensão — corpo bruto simula um
        # endpoint real que os emite (math.nan/math.inf seriam perdidos por
        # `json.dumps` antes de chegar à rede).
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                content=b'{"data": [{"embedding": [0.1, NaN], "index": 0}]}',
                headers={"Content-Type": "application/json"},
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_infinite_value_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                content=b'{"data": [{"embedding": [Infinity, 0.2], "index": 0}]}',
                headers={"Content-Type": "application/json"},
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um"])
        finally:
            await provider.aclose()

    async def test_empty_list_short_circuits_without_network_call(self) -> None:
        with respx.mock:
            provider = OpenAiCompatibleEmbeddingProvider(_settings())
            try:
                result = await provider.embed_documents([])
            finally:
                await provider.aclose()
        assert result == []

    @respx.mock
    async def test_sends_bearer_auth_when_api_key_set(self) -> None:
        route = respx.post(f"{_HTTPS_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})
        )
        provider = OpenAiCompatibleEmbeddingProvider(
            _settings(base_url=_HTTPS_BASE_URL, api_key=SecretStr("segredo-123"))
        )
        try:
            await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()
        assert route.calls.last.request.headers["Authorization"] == "Bearer segredo-123"

    @respx.mock
    async def test_sends_model_and_input_in_body(self) -> None:
        route = respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings(model="meu-modelo"))
        try:
            await provider.embed_documents(["ola"])
        finally:
            await provider.aclose()
        body = json.loads(route.calls.last.request.content)
        assert body == {"model": "meu-modelo", "input": ["ola"]}

    @respx.mock
    async def test_timeout_raises_model_timeout_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(side_effect=httpx.TimeoutException("timeout"))
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(side_effect=httpx.ConnectError("recusado"))
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_429_raises_rate_limit_error_with_retry_after(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "42"}, json={})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 42

    @respx.mock
    async def test_429_without_retry_after_uses_default(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(return_value=httpx.Response(429, json={}))
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 60

    @respx.mock
    async def test_5xx_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_4xx_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(return_value=httpx.Response(400, json={}))
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_malformed_body_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["texto"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_embedding_count_mismatch_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.1], "index": 0}]})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um", "dois"])
        finally:
            await provider.aclose()

    @respx.mock
    async def test_inconsistent_dimensions_raise_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"embedding": [0.1, 0.2], "index": 0},
                        {"embedding": [0.3], "index": 1},
                    ]
                },
            )
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            with pytest.raises(ModelResponseError):
                await provider.embed_documents(["um", "dois"])
        finally:
            await provider.aclose()


class TestEmbedQuery:
    @respx.mock
    async def test_returns_single_vector(self) -> None:
        respx.post(f"{_BASE_URL}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [0.5, 0.6], "index": 0}]})
        )
        provider = OpenAiCompatibleEmbeddingProvider(_settings())
        try:
            result = await provider.embed_query("pergunta")
        finally:
            await provider.aclose()
        assert result == [0.5, 0.6]
