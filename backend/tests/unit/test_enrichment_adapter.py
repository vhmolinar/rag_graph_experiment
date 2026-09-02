"""Contrato HTTP do adapter de enriquecimento compatível com OpenAI (T11).

Mesmo padrão de `test_planner_adapter.py`/`test_generation_adapter.py` (T07/T10):
servidor HTTP simulado via respx, erros tipados em todos os caminhos de falha
(timeout, conexão, 429, 5xx, 4xx, payload malformado, violação de contrato).
"""

import json
from collections.abc import Mapping
from uuid import uuid4

import httpx
import pytest
import respx

from rag.adapters.enrichment_adapter import (
    EnrichmentEndpointSettings,
    OpenAiCompatibleEnrichmentProvider,
)
from rag.domain.enums import SummaryScope
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import (
    ConceptExtractRequest,
    PassageRef,
    SummaryRequest,
)

_BASE_URL = "http://enrichment.test/v1"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> EnrichmentEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-instruct",
        "max_retries": 0,
    }
    defaults.update(overrides)
    return EnrichmentEndpointSettings(**defaults)  # type: ignore[arg-type]


def _passage_ref() -> PassageRef:
    return PassageRef(passage_id=uuid4(), text="Trecho de exemplo.")


def _summary_request() -> SummaryRequest:
    return SummaryRequest(
        system_policy="Política imutável.",
        output_contract="Contrato de saída.",
        scope_type=SummaryScope.SECTION,
        scope_description="Seção de exemplo.",
        passages=(_passage_ref(),),
    )


def _concept_request() -> ConceptExtractRequest:
    return ConceptExtractRequest(
        system_policy="Política imutável.",
        output_contract="Contrato de saída.",
        scope_description="Edição de exemplo.",
        passages=(_passage_ref(),),
    )


def _completion_body(payload: Mapping[str, object]) -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(dict(payload))}}]}


class TestSummarize:
    @respx.mock
    async def test_parses_summary_result(self) -> None:
        passage_id = uuid4()
        result = {"text": "Síntese de exemplo.", "supporting_passage_ids": [str(passage_id)]}
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            outcome = await provider.summarize(_summary_request())
        finally:
            await provider.aclose()
        assert outcome.text == "Síntese de exemplo."
        assert outcome.supporting_passage_ids == (passage_id,)
        sent_body = json.loads(route.calls.last.request.content)
        assert sent_body["model"] == "qwen3-instruct"
        assert sent_body["response_format"] == {"type": "json_object"}
        assert sent_body["messages"][0]["role"] == "system"
        assert sent_body["messages"][1]["role"] == "user"
        assert "Seção de exemplo" in sent_body["messages"][1]["content"]

    @respx.mock
    async def test_empty_support_is_valid_contract(self) -> None:
        # O contrato permite suporte vazio (SPEC §7.4) — é o SERVIÇO que
        # decide a publicação (NOTES.md §10.12 item 2).
        result = {"text": "Sem suporte.", "supporting_passage_ids": []}
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            outcome = await provider.summarize(_summary_request())
        finally:
            await provider.aclose()
        assert outcome.supporting_passage_ids == ()

    @respx.mock
    async def test_missing_text_raises_model_response_error(self) -> None:
        result: dict[str, object] = {"supporting_passage_ids": []}
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.summarize(_summary_request())
        finally:
            await provider.aclose()


class TestExtractConcepts:
    @respx.mock
    async def test_parses_extracted_concepts(self) -> None:
        passage_id = uuid4()
        result = {
            "concepts": [
                {
                    "normalized_label": "liberdade",
                    "description": "Conceito de exemplo.",
                    "aliases": ["autonomia"],
                    "supporting_passage_ids": [str(passage_id)],
                }
            ]
        }
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            outcome = await provider.extract_concepts(_concept_request())
        finally:
            await provider.aclose()
        assert outcome.concepts[0].normalized_label == "liberdade"
        assert outcome.concepts[0].aliases == ("autonomia",)
        assert outcome.concepts[0].supporting_passage_ids == (passage_id,)
        sent_body = json.loads(route.calls.last.request.content)
        assert sent_body["model"] == "qwen3-instruct"
        assert "Edição de exemplo" in sent_body["messages"][1]["content"]

    @respx.mock
    async def test_empty_concepts_is_valid_contract(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body({"concepts": []}))
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            outcome = await provider.extract_concepts(_concept_request())
        finally:
            await provider.aclose()
        assert outcome.concepts == ()

    @respx.mock
    async def test_invalid_concept_raises_model_response_error(self) -> None:
        # rótulo vazio viola o contrato ExtractedConcept — falha fechada.
        result = {"concepts": [{"normalized_label": "", "supporting_passage_ids": []}]}
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.extract_concepts(_concept_request())
        finally:
            await provider.aclose()


class TestHttpFailures:
    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_completion_body({"text": "x", "supporting_passage_ids": []})
            )
        )
        provider = OpenAiCompatibleEnrichmentProvider(
            _settings(api_key="segredo-123"), sleep=_noop_sleep
        )
        try:
            await provider.summarize(_summary_request())
        finally:
            await provider.aclose()
        assert route.calls.last.request.headers["Authorization"] == "Bearer segredo-123"

    @respx.mock
    async def test_timeout_raises_model_timeout_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.summarize(_summary_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(side_effect=httpx.ConnectError("recusado"))
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.summarize(_summary_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_429_raises_rate_limit_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "9"}, json={})
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.summarize(_summary_request())
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 9

    @respx.mock
    async def test_5xx_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.extract_concepts(_concept_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_4xx_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(400))
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.summarize(_summary_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_malformed_envelope_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.summarize(_summary_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_content_not_json_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "não é json"}}]}
            )
        )
        provider = OpenAiCompatibleEnrichmentProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.extract_concepts(_concept_request())
        finally:
            await provider.aclose()
