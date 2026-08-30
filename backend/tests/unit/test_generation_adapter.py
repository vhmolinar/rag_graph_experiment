"""Contrato HTTP do adapter de geração compatível com OpenAI (T07)."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import ValidationError

from rag.adapters.generation_adapter import (
    GenerationEndpointSettings,
    OpenAiCompatibleGeneratorProvider,
)
from rag.domain.answer import EvidenceRef
from rag.domain.enums import Depth
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import GenerationRequest

_BASE_URL = "https://generator.test/v1"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> GenerationEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-instruct",
    }
    defaults.update(overrides)
    return GenerationEndpointSettings(**defaults)  # type: ignore[arg-type]


def _request(*, depth: Depth = Depth.STANDARD) -> GenerationRequest:
    evidence = EvidenceRef(
        passage_id=uuid4(),
        edition_id=uuid4(),
        work_id=uuid4(),
        text="Trecho de evidência.",
        score=0.9,
        rank=0,
    )
    return GenerationRequest(
        system_policy="Responda apenas com base nas evidências.",
        output_contract="Responda em JSON conforme o contrato GeneratedAnswer.",
        question="Qual é a pergunta?",
        scope_description="Obra X, edição Y.",
        evidences=[evidence],
        depth=depth,
    )


def _completion_body(payload: dict[str, object]) -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class TestGenerationEndpointSettings:
    """T7-02/T7-05: URL válida, credencial só sobre https e limites de resiliência."""

    def test_http_with_api_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="https"):
            _settings(base_url="http://generator.test/v1", api_key="segredo-123")

    def test_http_without_api_key_is_accepted(self) -> None:
        assert _settings().api_key.get_secret_value() == ""

    def test_https_with_api_key_is_accepted(self) -> None:
        settings = _settings(api_key="segredo-123")
        assert settings.api_key.get_secret_value() == "segredo-123"

    def test_http_with_api_key_file_is_rejected(self, tmp_path: Path) -> None:
        # T7-02: a chave lida do secret file também não pode ir por http:// —
        # garante que a resolução do api_key ocorre antes da checagem de https.
        secret_file = tmp_path / "gen_key"
        secret_file.write_text("segredo-123", encoding="utf-8")
        with pytest.raises(ValidationError, match="https"):
            _settings(base_url="http://generator.test/v1", api_key_file=secret_file)

    def test_invalid_base_url_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(base_url="not-a-url")

    def test_non_positive_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(timeout_seconds=0)

    def test_non_positive_deep_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(deep_timeout_seconds=0)

    def test_negative_max_retries_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(max_retries=-1)

    def test_zero_max_concurrency_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(max_concurrency=0)

    def test_default_max_retries_is_zero(self) -> None:
        # T7-01: geração NÃO é idempotente — o default não pode retentar.
        assert _settings().max_retries == 0


class TestGenerate:
    @respx.mock
    async def test_parses_generated_answer(self) -> None:
        request = _request()
        answer_payload = {
            "answer_markdown": "Resposta.",
            "claims": [
                {
                    "id": "c1",
                    "text": "Afirmação.",
                    "evidence_ids": [str(request.evidences[0].passage_id)],
                    "inference": False,
                }
            ],
            "limitations": [],
            "abstained": False,
            "abstention_reason": None,
        }
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(answer_payload))
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            result = await provider.generate(request)
        finally:
            await provider.aclose()
        assert result.answer_markdown == "Resposta."
        assert result.claims[0].evidence_ids == (request.evidences[0].passage_id,)
        sent_body = json.loads(route.calls.last.request.content)
        assert sent_body["model"] == "qwen3-instruct"
        assert sent_body["response_format"] == {"type": "json_object"}
        assert sent_body["messages"][0]["role"] == "system"
        assert sent_body["messages"][1]["role"] == "user"
        assert str(request.evidences[0].passage_id) in sent_body["messages"][1]["content"]

    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        answer_payload = {
            "answer_markdown": "x",
            "claims": [],
            "limitations": [],
            "abstained": True,
            "abstention_reason": "sem suporte",
        }
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(answer_payload))
        )
        provider = OpenAiCompatibleGeneratorProvider(
            _settings(api_key="segredo-123"), sleep=_noop_sleep
        )
        try:
            await provider.generate(_request())
        finally:
            await provider.aclose()
        assert route.calls.last.request.headers["Authorization"] == "Bearer segredo-123"

    @respx.mock
    async def test_deep_depth_uses_longer_timeout(self) -> None:
        answer_payload = {
            "answer_markdown": "x",
            "claims": [],
            "limitations": [],
            "abstained": True,
            "abstention_reason": "sem suporte",
        }
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(answer_payload))
        )
        settings = _settings(timeout_seconds=10.0, deep_timeout_seconds=99.0)
        provider = OpenAiCompatibleGeneratorProvider(settings, sleep=_noop_sleep)
        try:
            await provider.generate(_request(depth=Depth.DEEP))
        finally:
            await provider.aclose()
        sent_timeout = route.calls.last.request.extensions["timeout"]
        assert sent_timeout["read"] == 99.0

    @respx.mock
    async def test_timeout_raises_model_timeout_error(self) -> None:
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.generate(_request())
        finally:
            await provider.aclose()
        # T7-01: geração não é idempotente — não retenta (default max_retries=0).
        assert route.call_count == 1

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.ConnectError("recusado")
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.generate(_request())
        finally:
            await provider.aclose()
        assert route.call_count == 1

    @respx.mock
    async def test_429_raises_rate_limit_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "7"}, json={})
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.generate(_request())
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 7

    @respx.mock
    async def test_5xx_raises_model_unavailable_error(self) -> None:
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.generate(_request())
        finally:
            await provider.aclose()
        # T7-01: 5xx (falha transitória) ainda assim não retenta — não idempotente.
        assert route.call_count == 1

    @respx.mock
    async def test_malformed_envelope_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.generate(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_content_not_json_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "não é json"}}]}
            )
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.generate(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_content_violates_generated_answer_contract_raises_model_response_error(
        self,
    ) -> None:
        # abstained=True mas com claims: viola o invariante de GeneratedAnswer.
        bad_payload = {
            "answer_markdown": "x",
            "claims": [{"id": "c1", "text": "y", "evidence_ids": [], "inference": True}],
            "limitations": [],
            "abstained": True,
            "abstention_reason": "motivo",
        }
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(bad_payload))
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.generate(_request())
        finally:
            await provider.aclose()
