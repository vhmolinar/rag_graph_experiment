"""Contrato HTTP do adapter de planejamento compatível com OpenAI (T10)."""

import json
from collections.abc import Mapping

import httpx
import pytest
import respx

from rag.adapters.planner_adapter import (
    OpenAiCompatiblePlannerProvider,
    PlannerEndpointSettings,
)
from rag.domain.enums import Depth
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import PlanningRequest

_BASE_URL = "http://planner.test/v1"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> PlannerEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-instruct",
        "max_retries": 0,
    }
    defaults.update(overrides)
    return PlannerEndpointSettings(**defaults)  # type: ignore[arg-type]


def _request() -> PlanningRequest:
    return PlanningRequest(question="Qual é a concepção de spleen?", depth=Depth.STANDARD)


def _completion_body(payload: Mapping[str, object]) -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(dict(payload))}}]}


class TestPlan:
    @respx.mock
    async def test_parses_planned_query(self) -> None:
        suggestion = {
            "semantic_query": "sugestão",
            "subquestions": ["s1", "s2"],
            "aliases": ["spleen"],
            "concept_labels": [],
        }
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(suggestion))
        )
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            result = await provider.plan(_request())
        finally:
            await provider.aclose()
        assert result.semantic_query == "sugestão"
        assert result.subquestions == ("s1", "s2")
        assert result.aliases == ("spleen",)
        sent_body = json.loads(route.calls.last.request.content)
        assert sent_body["model"] == "qwen3-instruct"
        assert sent_body["response_format"] == {"type": "json_object"}
        assert sent_body["messages"][0]["role"] == "system"
        assert sent_body["messages"][1]["role"] == "user"
        assert "spleen" in sent_body["messages"][1]["content"]

    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_completion_body({"semantic_query": "q", "subquestions": []})
            )
        )
        provider = OpenAiCompatiblePlannerProvider(
            _settings(api_key="segredo-123"), sleep=_noop_sleep
        )
        try:
            await provider.plan(_request())
        finally:
            await provider.aclose()
        assert route.calls.last.request.headers["Authorization"] == "Bearer segredo-123"

    @respx.mock
    async def test_timeout_raises_model_timeout_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.plan(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(side_effect=httpx.ConnectError("recusado"))
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.plan(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_429_raises_rate_limit_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "7"}, json={})
        )
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.plan(_request())
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 7

    @respx.mock
    async def test_5xx_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(503))
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.plan(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_malformed_envelope_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.plan(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_content_not_json_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "não é json"}}]}
            )
        )
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.plan(_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_subquestions_over_limit_raises_model_response_error(self) -> None:
        # 6 subperguntas viola o contrato PlannedQuery (max 5) — falha fechada.
        suggestion = {"subquestions": ["s"] * 6}
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(suggestion))
        )
        provider = OpenAiCompatiblePlannerProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.plan(_request())
        finally:
            await provider.aclose()
