"""Adapter HTTP de enriquecimento hierárquico (SPEC §7.4, T11).

Implementa `rag.domain.providers.EnrichmentProvider` via um endpoint compatível
com OpenAI (`POST /chat/completions`, modo JSON estruturado) — mesma convenção
dos adapters de T07/T10. Um provedor, duas operações: síntese de
seção/capítulo/edição (`summarize`) e extração de conceitos/aliases
(`extract_concepts`).

As políticas e contratos de saída viram no request (mesma convenção de
`GenerationRequest`); o adapter monta as mensagens chat (`system` = política +
contrato; `user` = escopo + passagens). Resiliência (retries transitórios,
circuit breaker, limite de concorrência) é reaproveitada via
`call_with_resilience`; chaves e payloads nunca entram nos logs desse caminho
(AC-16, garantido por construção — o closure é opaco).
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
from rag.domain.providers import (
    ConceptExtractRequest,
    ExtractedConcepts,
    PassageRef,
    SummaryRequest,
    SummaryResult,
)

_DEFAULT_RETRY_AFTER_SECONDS = 60


class EnrichmentEndpointSettings(ModelAuthSettings, ResilienceSettings):
    """Configuração do endpoint de enriquecimento (compatível com OpenAI)."""

    model_config = SettingsConfigDict(env_prefix="ENRICHMENT_", extra="ignore")

    base_url: str = "http://localhost:8003/v1"
    model: str = "qwen3-instruct"
    timeout_seconds: float = 60.0


def _headers(settings: EnrichmentEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _passages_text(passages: tuple[PassageRef, ...]) -> str:
    return "\n".join(
        f"{i}. ID={ref.passage_id}\n{ref.text}" for i, ref in enumerate(passages, start=1)
    )


def _summary_user_content(request: SummaryRequest) -> str:
    return (
        "# Escopo\n"
        f"{request.scope_type.value} — {request.scope_description}\n\n"
        "# Passagens\n"
        f"{_passages_text(request.passages)}\n\n"
        "Produza uma síntese fiel aos trechos e lista as passagens de suporte "
        "pelos seus IDs (EXACTAMENTE como informados acima). Se nenhuma "
        "passagem sustentar a síntese, devolve supporting_passage_ids vazio."
    )


def _concept_user_content(request: ConceptExtractRequest) -> str:
    return (
        "# Escopo\n"
        f"{request.scope_description}\n\n"
        "# Passagens\n"
        f"{_passages_text(request.passages)}\n\n"
        "Extrai conceitos e aliases exclusivamente destes trechos, ligando cada "
        "conceito às passagens de suporte pelos seus IDs (EXACTAMENTE como "
        "informados acima). Se um conceito não tiver suporte, devolve "
        "supporting_passage_ids vazio."
    )


def _messages(system_policy: str, output_contract: str, user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": f"{system_policy}\n\n{output_contract}"},
        {"role": "user", "content": user_content},
    ]


class OpenAiCompatibleEnrichmentProvider:
    """Implementa `rag.domain.providers.EnrichmentProvider` via um endpoint
    HTTP compatível com OpenAI (`POST /chat/completions`, JSON mode)."""

    def __init__(
        self,
        settings: EnrichmentEndpointSettings,
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

    async def summarize(self, request: SummaryRequest) -> SummaryResult:
        return await call_with_resilience(
            lambda: self._summarize_once(request),
            operation_name="enrichment.summarize",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def extract_concepts(self, request: ConceptExtractRequest) -> ExtractedConcepts:
        return await call_with_resilience(
            lambda: self._extract_once(request),
            operation_name="enrichment.extract_concepts",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def _summarize_once(self, request: SummaryRequest) -> SummaryResult:
        body = {
            "model": self._settings.model,
            "messages": _messages(
                request.system_policy, request.output_contract, _summary_user_content(request)
            ),
            "response_format": {"type": "json_object"},
        }
        parsed = await self._chat_json(body)
        try:
            return SummaryResult.model_validate(parsed)
        except ValidationError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de síntese não corresponde ao contrato SummaryResult.",
                cause=exc,
            ) from exc

    async def _extract_once(self, request: ConceptExtractRequest) -> ExtractedConcepts:
        body = {
            "model": self._settings.model,
            "messages": _messages(
                request.system_policy, request.output_contract, _concept_user_content(request)
            ),
            "response_format": {"type": "json_object"},
        }
        parsed = await self._chat_json(body)
        try:
            return ExtractedConcepts.model_validate(parsed)
        except ValidationError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de conceitos não corresponde ao contrato ExtractedConcepts.",
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
                "Resposta do endpoint de enriquecimento em formato inesperado.", cause=exc
            ) from exc
        if not isinstance(parsed, dict):
            raise ModelResponseError(
                "Contenido do endpoint de enriquecimento não é um objeto JSON."
            )
        return parsed
