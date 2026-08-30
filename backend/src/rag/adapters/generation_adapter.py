"""Adapter HTTP de geração compatível com OpenAI (SPEC §9.3, §11, T07).

Cliente mínimo: `POST {base_url}/chat/completions`, modo JSON estruturado
(`response_format={"type": "json_object"}`, suportado pela maioria dos
servidores compatíveis com OpenAI) para produzir diretamente um
`GeneratedAnswer` validável. Os seis blocos do prompt dissertativo
(SPEC §9.3) são transmitidos como uma mensagem `system` (política +
contrato de saída) e uma mensagem `user` com seções delimitadas (pergunta e
contexto de sessão, escopo, evidências numeradas, instrução de
profundidade) — o protocolo chat só tem os papéis `system`/`user`/
`assistant`, então os blocos não viram mensagens separadas, mas continuam
distinguíveis e na ordem da especificação.

Profundidade `deep` usa um timeout maior (`deep_timeout_seconds`), aplicado
por requisição — não altera o timeout padrão do cliente HTTP.
"""

import asyncio
import json

import httpx
from pydantic import Field, ValidationError
from pydantic_settings import SettingsConfigDict

from rag.adapters.model_settings import HttpEndpointSettings
from rag.adapters.resilience import CircuitBreaker, SleepFn, call_with_resilience
from rag.domain.answer import GeneratedAnswer
from rag.domain.enums import Depth
from rag.domain.errors import (
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
    RateLimitError,
)
from rag.domain.providers import GenerationRequest

_DEFAULT_RETRY_AFTER_SECONDS = 60

_DEPTH_INSTRUCTIONS: dict[Depth, str] = {
    Depth.BRIEF: "Responda de forma breve e direta, em poucas frases.",
    Depth.STANDARD: "Responda de forma equilibrada, cobrindo os pontos principais.",
    Depth.DEEP: "Responda de forma aprofundada e completa, explorando nuances relevantes.",
}


class GenerationEndpointSettings(HttpEndpointSettings):
    """Configuração do endpoint de geração (SPEC: compatível com OpenAI).

    `POST /chat/completions` NÃO é idempotente (uma nova geração pode
    consumir recursos e produzir conteúdo diferente para a mesma execução, se
    a primeira chamada já tiver sido processada). Por isso `max_retries` tem
    default 0 (SPEC §11: retries apenas para falhas transitórias E operações
    idempotentes — T7-01). A validação de URL/credencial vem de
    `HttpEndpointSettings`.
    """

    model_config = SettingsConfigDict(env_prefix="GENERATOR_", extra="ignore")

    base_url: str = "http://localhost:8003/v1"
    model: str = "qwen3-instruct"
    timeout_seconds: float = Field(default=60.0, gt=0)
    deep_timeout_seconds: float = Field(default=180.0, gt=0)
    max_retries: int = Field(default=0, ge=0)


def _headers(settings: GenerationEndpointSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _build_user_content(request: GenerationRequest) -> str:
    sections = [f"# Pergunta\n{request.question}"]
    if request.session_context:
        sections.append(f"# Contexto da sessão\n{request.session_context}")
    sections.append(f"# Escopo\n{request.scope_description}")
    evidence_lines = "\n".join(
        f"{i}. ID={evidence.passage_id}\n{evidence.text}"
        for i, evidence in enumerate(request.evidences, start=1)
    )
    sections.append(f"# Evidências\n{evidence_lines}")
    sections.append(f"# Profundidade\n{_DEPTH_INSTRUCTIONS[request.depth]}")
    return "\n\n".join(sections)


def _build_messages(request: GenerationRequest) -> list[dict[str, str]]:
    system_content = f"{request.system_policy}\n\n{request.output_contract}"
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": _build_user_content(request)},
    ]


class OpenAiCompatibleGeneratorProvider:
    """Implementa `rag.domain.providers.GeneratorProvider` via um endpoint
    HTTP compatível com OpenAI (`POST /chat/completions`, JSON mode)."""

    def __init__(
        self,
        settings: GenerationEndpointSettings,
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

    async def generate(self, request: GenerationRequest) -> GeneratedAnswer:
        return await call_with_resilience(
            lambda: self._generate_once(request),
            operation_name="generation.generate",
            breaker=self._breaker,
            semaphore=self._semaphore,
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.retry_backoff_seconds,
            backoff_multiplier=self._settings.retry_backoff_multiplier,
            **self._sleep_kwargs,
        )

    async def _generate_once(self, request: GenerationRequest) -> GeneratedAnswer:
        timeout = (
            self._settings.deep_timeout_seconds
            if request.depth is Depth.DEEP
            else self._settings.timeout_seconds
        )
        body = {
            "model": self._settings.model,
            "messages": _build_messages(request),
            "response_format": {"type": "json_object"},
        }
        try:
            response = await self._client.post("/chat/completions", json=body, timeout=timeout)
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
                "Resposta do endpoint de geração em formato inesperado.", cause=exc
            ) from exc

        try:
            return GeneratedAnswer.model_validate(parsed)
        except ValidationError as exc:
            raise ModelResponseError(
                "Resposta do endpoint de geração não corresponde ao contrato GeneratedAnswer.",
                cause=exc,
            ) from exc
