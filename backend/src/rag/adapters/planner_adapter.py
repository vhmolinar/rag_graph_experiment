"""Adapter HTTP de planejamento compatível com OpenAI (SPEC §8.2, T10).

Cliente mínimo: `POST {base_url}/chat/completions`, modo JSON estruturado
(`response_format={"type": "json_object"}`), mesma convenção dos adapters de
T07. O provedor só enriquece o plano determinístico (NOTAS.md §10.11 item 4):
gera subperguntas/aliases limitados e uma sugestão opcional de consulta
semântica — nunca decide intenção ou estratégia.

Resiliência (retries transitórios, circuit breaker, limite de concorrência) é
reaproveitada via `call_with_resilience`; chaves e payloads nunca entram nos
logs desse caminho (AC-16, garantido por construção — o closure é opaco).
"""

import asyncio
import json

import httpx
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from rag.adapters.model_settings import HttpEndpointSettings
from rag.adapters.resilience import CircuitBreaker, SleepFn, call_with_resilience
from rag.domain.enums import Depth
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import PlannedQuery, PlanningRequest

_DEFAULT_RETRY_AFTER_SECONDS = 60

_DEPTH_INSTRUCTIONS: dict[Depth, str] = {
    Depth.BRIEF: "Sugestões breves, poucas subperguntas.",
    Depth.STANDARD: "Sugestões equilibradas, até cinco subperguntas.",
    Depth.DEEP: "Sugestões aprofundadas, até cinco subperguntas e mais aliases.",
}


class PlannerEndpointSettings(HttpEndpointSettings):
    """Configuração do endpoint de planejamento (compatível com OpenAI).

    Herda `HttpEndpointSettings` (SPEC §11, AC-16): URL http(s) válida e
    recusa de credencial sobre `http://` — `Authorization: Bearer` nunca
    trafega em texto claro (T10-04 da revisão; mesma regra dos adapters de
    T07).
    """

    model_config = SettingsConfigDict(env_prefix="PLANNER_", extra="ignore")

    base_url: str = "http://localhost:8003/v1"
    model: str = "qwen3-instruct"
    timeout_seconds: float = 30.0


def _headers(settings: PlannerEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _build_user_content(request: PlanningRequest) -> str:
    return (
        "# Pergunta\n"
        f"{request.question}\n\n"
        "# Profundidade\n"
        f"{_DEPTH_INSTRUCTIONS[request.depth]}\n\n"
        "Produza uma sugestão de planejamento em JSON conforme o contrato "
        "PlannedQuery: consulta semântica opcional, subperguntas limitadas, "
        "aliases e rótulos de conceitos. Não inventa intenção nem estratégia."
    )


def _build_messages(request: PlanningRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "És um provedor de planejamento de um RAG de livros em "
                "português. Responde apenas em JSON válido."
            ),
        },
        {"role": "user", "content": _build_user_content(request)},
    ]


class OpenAiCompatiblePlannerProvider:
    """Implementa `rag.domain.providers.PlannerProvider` via um endpoint HTTP
    compatível com OpenAI (`POST /chat/completions`, JSON mode)."""

    def __init__(
        self,
        settings: PlannerEndpointSettings,
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

    async def plan(self, request: PlanningRequest) -> PlannedQuery:
        return await call_with_resilience(
            lambda: self._plan_once(request),
            operation_name="planner.plan",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def _plan_once(self, request: PlanningRequest) -> PlannedQuery:
        body = {
            "model": self._settings.model,
            "messages": _build_messages(request),
            "response_format": {"type": "json_object"},
        }
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
                "Resposta do endpoint de planejamento em formato inesperado.", cause=exc
            ) from exc

        try:
            return PlannedQuery.model_validate(parsed)
        except ValidationError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de planejamento não corresponde ao contrato PlannedQuery.",
                cause=exc,
            ) from exc
