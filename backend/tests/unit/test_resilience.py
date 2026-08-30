"""Circuit breaker, retry transitório e limite de concorrência (SPEC §11, T07)."""

import asyncio

import pytest
import structlog

from rag.adapters.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    call_with_resilience,
)
from rag.domain.errors import ModelResponseError, ModelTimeoutError, ModelUnavailableError


async def _noop_sleep(_seconds: float) -> None:
    return None


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=10.0)
        breaker.before_call()  # não levanta

    def test_opens_after_consecutive_failures(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=10.0)
        breaker.on_failure()
        breaker.before_call()  # ainda fechado (1 falha)
        breaker.on_failure()
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()

    def test_success_resets_failure_count(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=10.0)
        breaker.on_failure()
        breaker.on_success()
        breaker.on_failure()
        breaker.before_call()  # apenas 1 falha desde o último sucesso

    def test_half_opens_after_reset_timeout(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=5.0, clock=clock.now)
        breaker.on_failure()
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()
        clock.advance(5.0)
        breaker.before_call()  # meio-aberto: permite tentativa de teste

    def test_half_open_failure_reopens_immediately(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=5.0, clock=clock.now)
        breaker.on_failure()
        clock.advance(5.0)
        breaker.before_call()  # half-open
        breaker.on_failure()
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()

    def test_half_open_success_closes(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=5.0, clock=clock.now)
        breaker.on_failure()
        clock.advance(5.0)
        breaker.before_call()  # half-open
        breaker.on_success()
        breaker.before_call()  # fechado de novo, sem exceção

    def test_half_open_allows_single_probe_concurrently(self) -> None:
        # T7-04: enquanto a probe half-open está em andamento, uma segunda
        # chamada é rejeitada — apenas uma tentativa de teste chega ao endpoint.
        clock = _FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=5.0, clock=clock.now)
        breaker.on_failure()
        clock.advance(5.0)
        breaker.before_call()  # primeira probe: permitida e marcada em andamento
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()  # segunda probe concorrente: rejeitada
        breaker.on_success()
        breaker.before_call()  # após o sucesso, volta a permitir (fechado)

    def test_half_open_probe_cleared_on_failure(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=5.0, clock=clock.now)
        breaker.on_failure()
        clock.advance(5.0)
        breaker.before_call()  # probe 1 em andamento
        breaker.on_failure()  # probe falha -> abre de novo, limpa a flag
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()
        clock.advance(5.0)
        breaker.before_call()  # nova probe permitida após novo reset


class TestCallWithResilience:
    async def test_success_returns_result(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=10.0)

        async def op() -> str:
            return "ok"

        result = await call_with_resilience(
            op,
            operation_name="test",
            breaker=breaker,
            semaphore=asyncio.Semaphore(1),
            max_retries=2,
            backoff_seconds=0.01,
            backoff_multiplier=2.0,
            sleep=_noop_sleep,
        )
        assert result == "ok"

    async def test_retries_transient_error_then_succeeds(self) -> None:
        breaker = CircuitBreaker(failure_threshold=5, reset_timeout_seconds=10.0)
        attempts = {"count": 0}

        async def op() -> str:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ModelTimeoutError()
            return "ok"

        result = await call_with_resilience(
            op,
            operation_name="test",
            breaker=breaker,
            semaphore=asyncio.Semaphore(1),
            max_retries=2,
            backoff_seconds=0.01,
            backoff_multiplier=2.0,
            sleep=_noop_sleep,
        )
        assert result == "ok"
        assert attempts["count"] == 3

    async def test_exhausts_retries_and_raises(self) -> None:
        breaker = CircuitBreaker(failure_threshold=10, reset_timeout_seconds=10.0)
        attempts = {"count": 0}

        async def op() -> str:
            attempts["count"] += 1
            raise ModelUnavailableError()

        with pytest.raises(ModelUnavailableError):
            await call_with_resilience(
                op,
                operation_name="test",
                breaker=breaker,
                semaphore=asyncio.Semaphore(1),
                max_retries=2,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )
        assert attempts["count"] == 3  # tentativa inicial + 2 retries

    async def test_non_transient_error_is_not_retried(self) -> None:
        breaker = CircuitBreaker(failure_threshold=10, reset_timeout_seconds=10.0)
        attempts = {"count": 0}

        async def op() -> str:
            attempts["count"] += 1
            raise ModelResponseError()

        with pytest.raises(ModelResponseError):
            await call_with_resilience(
                op,
                operation_name="test",
                breaker=breaker,
                semaphore=asyncio.Semaphore(1),
                max_retries=2,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )
        assert attempts["count"] == 1

    async def test_breaker_opens_and_stops_further_attempts(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=999.0)
        attempts = {"count": 0}

        async def op() -> str:
            attempts["count"] += 1
            raise ModelUnavailableError()

        with pytest.raises(CircuitBreakerOpenError):
            await call_with_resilience(
                op,
                operation_name="test",
                breaker=breaker,
                semaphore=asyncio.Semaphore(1),
                max_retries=5,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )
        # 1 tentativa abre o breaker (threshold=1); o retry seguinte é barrado
        # pelo before_call() antes de chamar a operação de novo.
        assert attempts["count"] == 1

    async def test_open_breaker_rejects_without_calling_operation(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=999.0)
        breaker.on_failure()
        calls = {"count": 0}

        async def op() -> str:
            calls["count"] += 1
            return "não deveria ser chamada"

        with pytest.raises(CircuitBreakerOpenError):
            await call_with_resilience(
                op,
                operation_name="test",
                breaker=breaker,
                semaphore=asyncio.Semaphore(1),
                max_retries=0,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )
        assert calls["count"] == 0

    async def test_half_open_lets_only_one_probe_reach_endpoint(self) -> None:
        # T7-04: após o reset, apenas UMA chamada (a probe de teste) chega ao
        # endpoint; a chamada concorrente é rejeitada pelo breaker.
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.0)
        breaker.on_failure()  # abre o circuito (threshold=1)

        reached = {"count": 0}

        async def op() -> str:
            reached["count"] += 1
            await asyncio.sleep(0.05)  # mantém a probe em andamento
            return "ok"

        async def call() -> str:
            return await call_with_resilience(
                lambda: op(),
                operation_name="test",
                breaker=breaker,
                semaphore=asyncio.Semaphore(2),
                max_retries=0,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )

        # reset_timeout=0: a primeira chamada já sai de OPEN e vira a probe.
        results = await asyncio.gather(call(), call(), return_exceptions=True)
        ok_calls = [r for r in results if r == "ok"]
        rejected = [r for r in results if isinstance(r, CircuitBreakerOpenError)]
        assert len(ok_calls) == 1
        assert len(rejected) == 1
        assert reached["count"] == 1

    async def test_concurrency_limit_serializes_calls(self) -> None:
        breaker = CircuitBreaker(failure_threshold=10, reset_timeout_seconds=10.0)
        semaphore = asyncio.Semaphore(1)
        in_flight = {"count": 0, "max": 0}

        async def op() -> str:
            in_flight["count"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["count"])
            await asyncio.sleep(0.01)
            in_flight["count"] -= 1
            return "ok"

        async def call() -> str:
            return await call_with_resilience(
                op,
                operation_name="test",
                breaker=breaker,
                semaphore=semaphore,
                max_retries=0,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )

        results = await asyncio.gather(call(), call(), call())
        assert list(results) == ["ok", "ok", "ok"]
        assert in_flight["max"] == 1

    async def test_failure_logs_are_free_of_operation_content(self) -> None:
        """AC-16: o log de retry/circuit-breaker só conhece a exceção e o
        nome da operação — nunca o corpo da requisição/resposta. Prova: um
        segredo presente apenas no corpo da operação nunca aparece no log,
        porque `call_with_resilience` nunca recebe esse corpo."""
        breaker = CircuitBreaker(failure_threshold=10, reset_timeout_seconds=10.0)
        forbidden_marker = "sk-super-secreta-nao-deve-vazar"

        async def op() -> str:
            # O segredo existe no escopo do teste, mas nunca é passado para
            # call_with_resilience nem para a exceção levantada.
            assert forbidden_marker
            raise ModelTimeoutError()

        with structlog.testing.capture_logs() as captured, pytest.raises(ModelTimeoutError):
            await call_with_resilience(
                op,
                operation_name="test.op",
                breaker=breaker,
                semaphore=asyncio.Semaphore(1),
                max_retries=1,
                backoff_seconds=0.01,
                backoff_multiplier=2.0,
                sleep=_noop_sleep,
            )
        assert captured
        for entry in captured:
            rendered = repr(entry)
            assert forbidden_marker not in rendered
            assert entry["operation"] == "test.op"
            assert entry["error_type"] == "ModelTimeoutError"
