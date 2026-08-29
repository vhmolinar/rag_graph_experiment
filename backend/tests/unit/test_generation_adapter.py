"""Contrato HTTP do adapter de geração compatível com OpenAI (T07)."""

import json
from uuid import uuid4

import httpx
import pytest
import respx

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

_BASE_URL = "http://generator.test/v1"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> GenerationEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-instruct",
        "max_retries": 0,
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
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.generate(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(side_effect=httpx.ConnectError("recusado"))
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.generate(_request())
        finally:
            await provider.aclose()

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
        respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatibleGeneratorProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.generate(_request())
        finally:
            await provider.aclose()

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
