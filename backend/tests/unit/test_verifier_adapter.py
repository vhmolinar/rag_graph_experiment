"""Contrato HTTP do adapter de verificação compatível com OpenAI (T13).

Mesmo padrão de `test_enrichment_adapter.py` (T11): servidor HTTP simulado
via respx, erros tipados em todos os caminhos de falha (timeout, conexão,
429, 5xx, 4xx, payload malformado, violação de contrato) — AC-14.
"""

import json
from collections.abc import Mapping
from uuid import uuid4

import httpx
import pytest
import respx

from rag.adapters.verifier_adapter import (
    OpenAiCompatibleVerifierProvider,
    VerifierEndpointSettings,
)
from rag.domain.answer import Claim, EvidenceRef
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import VerificationRequest

_BASE_URL = "http://verifier.test/v1"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _settings(**overrides: object) -> VerifierEndpointSettings:
    defaults: dict[str, object] = {
        "base_url": _BASE_URL,
        "model": "qwen3-instruct",
        "max_retries": 0,
    }
    defaults.update(overrides)
    return VerifierEndpointSettings(**defaults)  # type: ignore[arg-type]


def _evidence_ref(passage_id: object = None) -> EvidenceRef:
    return EvidenceRef(
        passage_id=passage_id or uuid4(),  # type: ignore[arg-type]
        edition_id=uuid4(),
        work_id=uuid4(),
        text="Trecho de exemplo.",
        score=1.0,
        rank=0,
    )


def _verify_request() -> VerificationRequest:
    evidence = _evidence_ref()
    return VerificationRequest(
        system_policy="Política imutável.",
        output_contract="Contrato de saída.",
        question="Pergunta de teste?",
        claims=(Claim(id="c1", text="Afirmação de teste.", evidence_ids=(evidence.passage_id,)),),
        evidences=(evidence,),
    )


def _completion_body(payload: Mapping[str, object]) -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(dict(payload))}}]}


class TestVerify:
    @respx.mock
    async def test_parses_verdicts(self) -> None:
        evidence_id = uuid4()
        result = {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "evidence_id": str(evidence_id),
                    "supported": True,
                    "contradiction": False,
                    "detail": None,
                }
            ]
        }
        route = respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            outcome = await provider.verify(_verify_request())
        finally:
            await provider.aclose()
        assert outcome.verdicts[0].claim_id == "c1"
        assert outcome.verdicts[0].evidence_id == evidence_id
        assert outcome.verdicts[0].supported
        sent_body = json.loads(route.calls.last.request.content)
        assert sent_body["model"] == "qwen3-instruct"
        assert sent_body["response_format"] == {"type": "json_object"}
        assert sent_body["messages"][0]["role"] == "system"
        assert sent_body["messages"][1]["role"] == "user"
        assert "Pergunta de teste" in sent_body["messages"][1]["content"]
        assert "c1" in sent_body["messages"][1]["content"]

    @respx.mock
    async def test_unsupported_and_contradiction_verdicts(self) -> None:
        evidence_id = uuid4()
        result = {
            "verdicts": [
                {
                    "claim_id": "c1",
                    "evidence_id": str(evidence_id),
                    "supported": False,
                    "contradiction": True,
                    "detail": "A fonte afirma o oposto.",
                }
            ]
        }
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body(result))
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            outcome = await provider.verify(_verify_request())
        finally:
            await provider.aclose()
        assert not outcome.verdicts[0].supported
        assert outcome.verdicts[0].contradiction
        assert outcome.verdicts[0].detail == "A fonte afirma o oposto."

    @respx.mock
    async def test_authorization_header_sent(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_completion_body({"verdicts": []}))
        )
        provider = OpenAiCompatibleVerifierProvider(
            _settings(api_key="secret-key"), sleep=_noop_sleep
        )
        try:
            await provider.verify(_verify_request())
        finally:
            await provider.aclose()
        request = respx.calls.last.request
        assert request.headers["Authorization"] == "Bearer secret-key"

    @respx.mock
    async def test_timeout_raises_model_timeout_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelTimeoutError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_connection_error_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_429_raises_rate_limit_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "12"})
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(RateLimitError) as exc_info:
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()
        assert exc_info.value.retry_after_seconds == 12

    @respx.mock
    async def test_5xx_raises_model_unavailable_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(502))
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelUnavailableError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_4xx_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(return_value=httpx.Response(422))
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_malformed_envelope_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={"unexpected": True})
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_non_json_content_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "não-json"}}]}
            )
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()

    @respx.mock
    async def test_contract_violation_raises_model_response_error(self) -> None:
        respx.post(f"{_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_completion_body({"verdicts": [{"claim_id": "c1"}]}),  # falta evidence_id
            )
        )
        provider = OpenAiCompatibleVerifierProvider(_settings(), sleep=_noop_sleep)
        try:
            with pytest.raises(ModelResponseError):
                await provider.verify(_verify_request())
        finally:
            await provider.aclose()
