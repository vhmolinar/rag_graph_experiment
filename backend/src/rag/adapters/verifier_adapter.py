"""Adapter HTTP de verificação de afirmações (SPEC §9.4, §11, T13).

Implementa `rag.domain.providers.VerifierProvider` via um endpoint compatível
com OpenAI (`POST /chat/completions`, modo JSON estruturado) — mesma convenção
dos adapters de T07/T11. O provedor julga CADA par (afirmação, evidência):
suporte e contradição. Nunca introduz novas afirmações — o contrato de saída
devolve SÓ veredictos (`VerificationVerdict`).

As políticas e contratos de saída viram no request (mesma convenção de
`GenerationRequest`); o adapter monta as mensagens chat (`system` = política +
contrato; `user` = pergunta + afirmações + evidências numeradas). Resiliência
(retries transitórios, circuit breaker, limite de concorrência) é reaproveitada
via `call_with_resilience`; chaves e payloads nunca entram nos logs desse
caminho (AC-16, garantido por construção — o closure é opaco).
"""

import asyncio
import json
from collections.abc import Mapping

import httpx
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from rag.adapters.model_settings import ModelAuthSettings, ResilienceSettings
from rag.adapters.resilience import CircuitBreaker, SleepFn, call_with_resilience
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import VerificationRequest, VerificationVerdict

_DEFAULT_RETRY_AFTER_SECONDS = 60


class VerifierEndpointSettings(ModelAuthSettings, ResilienceSettings):
    """Configuração do endpoint de verificação (compatível com OpenAI)."""

    model_config = SettingsConfigDict(env_prefix="VERIFIER_", extra="ignore")

    base_url: str = "http://localhost:8003/v1"
    model: str = "qwen3-instruct"
    timeout_seconds: float = 60.0


def _headers(settings: VerifierEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _claims_text(request: VerificationRequest) -> str:
    lines: list[str] = []
    for index, claim in enumerate(request.claims, start=1):
        cited = ", ".join(str(evidence_id) for evidence_id in claim.evidence_ids) or "(nenhuna)"
        lines.append(f"{index}. ID={claim.id} texto={claim.text} evidence_ids={cited}")
    return "\n".join(lines)


def _evidences_text(request: VerificationRequest) -> str:
    return "\n".join(
        f"{i}. ID={ref.passage_id}\n{ref.text}" for i, ref in enumerate(request.evidences, start=1)
    )


def _verify_user_content(request: VerificationRequest) -> str:
    return (
        "# Pergunta\n"
        f"{request.question}\n\n"
        "# Afirmações\n"
        f"{_claims_text(request)}\n\n"
        "# Evidências\n"
        f"{_evidences_text(request)}\n\n"
        "Julga, para CADA par (afirmação, evidência) citado em evidence_ids, se "
        "a evidência REALMENTE sustenta a afirmação e se a contradice. Devolve "
        "um veredicto para cada par informado."
    )


def _messages(system_policy: str, output_contract: str, user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": f"{system_policy}\n\n{output_contract}"},
        {"role": "user", "content": user_content},
    ]


class OpenAiCompatibleVerifierProvider:
    """Implementa `rag.domain.providers.VerifierProvider` via um endpoint HTTP
    compatível com OpenAI (`POST /chat/completions`, JSON mode)."""

    def __init__(
        self,
        settings: VerifierEndpointSettings,
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

    async def verify(self, request: VerificationRequest) -> VerificationVerdict:
        return await call_with_resilience(
            lambda: self._verify_once(request),
            operation_name="verification.verify",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def _verify_once(self, request: VerificationRequest) -> VerificationVerdict:
        body = {
            "model": self._settings.model,
            "messages": _messages(
                request.system_policy, request.output_contract, _verify_user_content(request)
            ),
            "response_format": {"type": "json_object"},
        }
        parsed = await self._chat_json(body)
        try:
            return VerificationVerdict.model_validate(parsed)
        except ValidationError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de verificação não corresponde ao contrato "
                "VerificationVerdict.",
                cause=exc,
            ) from exc

    async def _chat_json(self, body: Mapping[str, object]) -> dict[str, object]:
        """POST + parsing do conteúdo JSON; erros HTTP mapeados para erros tipados."""
        try:
            response = await self._client.post("/chat/completions", json=body)
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
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ModelResponseError(
                "Resposta do endpoint de verificação em formato inesperado.", cause=exc
            ) from exc
        if not isinstance(parsed, dict):
            raise ModelResponseError("Contenido do endpoint de verificação não é um objeto JSON.")
        return parsed
