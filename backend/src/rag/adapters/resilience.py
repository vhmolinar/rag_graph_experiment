"""Resiliência compartilhada dos adapters HTTP de modelos (SPEC §11, T07).

`call_with_resilience` só recebe um closure opaco (`operation`) e objetos de
exceção — nunca o corpo da requisição ou resposta. Isso garante, por
construção, que os logs de retry/circuit-breaker emitidos aqui não podem
conter prompts, documentos ou chaves (AC-16): esta função não tem acesso a
esse conteúdo.

Retries só ocorrem para falhas transitórias (`ModelTimeoutError`,
`ModelUnavailableError` — timeout, conexão recusada, 5xx). Erros de
validação (`ModelResponseError`), limite de taxa (`RateLimitError`) e
dimensão inválida (`EmbeddingDimensionError`) nunca são retentados
automaticamente: não são transitórios do endpoint, e sim da requisição ou
da resposta recebida (SPEC §11 "retries apenas para falhas transitórias e
operações idempotentes").
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum, auto

import structlog

from rag.domain.errors import ModelTimeoutError, ModelUnavailableError

_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (ModelTimeoutError, ModelUnavailableError)

SleepFn = Callable[[float], Awaitable[None]]


class CircuitBreakerOpenError(ModelUnavailableError):
    def __init__(self) -> None:
        super().__init__("Circuit breaker aberto: endpoint de modelo marcado como indisponível.")


class _State(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """Máquina de estados simples: abre após N falhas consecutivas; permite
    uma tentativa de teste (half-open) após o tempo de reset."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold deve ser >= 1")
        self._failure_threshold = failure_threshold
        self._reset_timeout_seconds = reset_timeout_seconds
        self._clock = clock
        self._state = _State.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0  # só significativo enquanto _state is OPEN

    @property
    def is_open(self) -> bool:
        return self._state is _State.OPEN

    def before_call(self) -> None:
        if self._state is not _State.OPEN:
            return
        if self._clock() - self._opened_at < self._reset_timeout_seconds:
            raise CircuitBreakerOpenError()
        self._state = _State.HALF_OPEN

    def on_success(self) -> None:
        self._state = _State.CLOSED
        self._consecutive_failures = 0

    def on_failure(self) -> None:
        if self._state is _State.HALF_OPEN:
            self._state = _State.OPEN
            self._opened_at = self._clock()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._state = _State.OPEN
            self._opened_at = self._clock()


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def call_with_resilience[T](
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    breaker: CircuitBreaker,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    backoff_seconds: float,
    backoff_multiplier: float,
    sleep: SleepFn = _default_sleep,
) -> T:
    """Executa `operation` sob limite de concorrência, circuit breaker e
    retry de falhas transitórias com backoff exponencial."""

    log = structlog.get_logger()
    async with semaphore:
        delay = backoff_seconds
        attempt = 0
        while True:
            breaker.before_call()
            try:
                result = await operation()
            except Exception as exc:
                breaker.on_failure()
                retryable = isinstance(exc, _TRANSIENT_ERRORS) and attempt < max_retries
                log.warning(
                    "model_call.failed",
                    operation=operation_name,
                    error_type=type(exc).__name__,
                    attempt=attempt,
                    will_retry=retryable,
                )
                if retryable:
                    attempt += 1
                    await sleep(delay)
                    delay *= backoff_multiplier
                    continue
                raise
            else:
                breaker.on_success()
                return result
