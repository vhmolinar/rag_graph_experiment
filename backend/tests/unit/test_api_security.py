"""Testes unitários da segurança da API (T14; AC-18).

Cobre: TokenBucket (relógio inyectável), RateLimitMiddleware (429 +
`Retry-After`, health isento), headers de segurança, `X-Request-ID`,
parsing de `Range` e mapa `ErrorCode` → status. Não requer banco.
"""

from typing import Any

import httpx
from starlette.types import Receive, Scope, Send

from rag.api.errors import code_to_status
from rag.api.routes.catalog import _parse_range
from rag.api.security import (
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    TokenBucket,
)
from rag.domain.errors import ErrorCode


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    assert scope["type"] == "http"
    body = b"ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_token_bucket_allows_capacity_then_limits() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, refill_per_second=1.0, now=clock)
    assert bucket.consume()
    assert bucket.consume()
    assert bucket.consume()
    assert not bucket.consume()
    assert bucket.retry_after() >= 1


def test_token_bucket_refills_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, refill_per_second=1.0, now=clock)
    for _ in range(3):
        assert bucket.consume()
    clock.advance(1.0)
    assert bucket.consume()
    assert not bucket.consume()
    clock.advance(2.0)
    assert bucket.consume()


def test_token_bucket_retry_after_reflects_refill() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, refill_per_second=60.0, now=clock)
    assert bucket.consume()
    assert bucket.consume()
    assert not bucket.consume()
    assert bucket.retry_after() == 1


async def test_rate_limit_returns_429_with_retry_after() -> None:
    clock = FakeClock()
    app = RateLimitMiddleware(_ok_app, rate_limit_per_minute=3, now=clock)
    client = await _client_for(app)
    async with client:
        for _ in range(3):
            response = await client.get("/x")
            assert response.status_code == 200
        response = await client.get("/x")
        assert response.status_code == 429
        assert response.headers["retry-after"]
        body = response.json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert body["error"]["message"]
        assert body["error"]["request_id"]


async def test_rate_limit_recovers_after_refill() -> None:
    clock = FakeClock()
    app = RateLimitMiddleware(_ok_app, rate_limit_per_minute=1, now=clock)
    client = await _client_for(app)
    async with client:
        assert (await client.get("/x")).status_code == 200
        assert (await client.get("/x")).status_code == 429
        clock.advance(61.0)
        assert (await client.get("/x")).status_code == 200


async def test_rate_limit_exempts_health_prefix() -> None:
    clock = FakeClock()
    app = RateLimitMiddleware(
        _ok_app, rate_limit_per_minute=1, exempt_prefixes=("/api/v1/health/",), now=clock
    )
    client = await _client_for(app)
    async with client:
        for _ in range(5):
            response = await client.get("/api/v1/health/live")
            assert response.status_code == 200
        # caminho não isento continua limitado (o isento não consome tokens)
        assert (await client.get("/api/v1/works")).status_code == 200
        assert (await client.get("/api/v1/works")).status_code == 429


async def _asgi_call(app: Any, client_addr: str, path: str = "/x") -> list[dict[str, Any]]:
    """Invoca o middleware directamente com um `client` específico — permite
    exercitar vários buckets (IPs distintos) sem passar por um servidor."""
    messages: list[dict[str, Any]] = []
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "client": (client_addr, 50000),
        "state": {},
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


async def test_rate_limit_buckets_expire_after_ttl() -> None:
    """T14-03: buckets inativos são coletados depois do TTL — a memória não
    cresce permanentemente com IPs distintos."""
    clock = FakeClock()
    app = RateLimitMiddleware(
        _ok_app, rate_limit_per_minute=100, now=clock, bucket_ttl_seconds=10.0
    )
    await _asgi_call(app, "10.0.0.1")
    await _asgi_call(app, "10.0.0.2")
    assert len(app._buckets) == 2
    clock.advance(11.0)
    await _asgi_call(app, "10.0.0.3")  # dispara a coleta periódica
    assert len(app._buckets) == 1
    assert "10.0.0.3" in app._buckets


async def test_rate_limit_buckets_are_recreated_after_ttl() -> None:
    """T14-03: após expirar, um IP anterior volta a ter bucket (limite novo)."""
    clock = FakeClock()
    app = RateLimitMiddleware(_ok_app, rate_limit_per_minute=1, now=clock, bucket_ttl_seconds=5.0)
    await _asgi_call(app, "10.0.0.1")
    assert (await _asgi_call(app, "10.0.0.1"))[0]["status"] == 429
    clock.advance(6.0)
    await _asgi_call(app, "10.0.0.2")  # dispara a coleta
    assert (await _asgi_call(app, "10.0.0.1"))[0]["status"] == 200


async def test_rate_limit_caps_bucket_cardinality() -> None:
    """T14-03: com `max_buckets`, o bucket menos recente é desalojado — o
    dicionário fica limitado em cardinalidade."""
    clock = FakeClock()
    app = RateLimitMiddleware(_ok_app, rate_limit_per_minute=100, now=clock, max_buckets=3)
    for index in range(5):
        await _asgi_call(app, f"10.0.0.{index}")
    assert len(app._buckets) == 3
    # os 2 menos recentes foram desalojados; restan os 3 últimos
    assert set(app._buckets) == {"10.0.0.2", "10.0.0.3", "10.0.0.4"}


async def test_security_headers_are_present() -> None:
    app = SecurityHeadersMiddleware(_ok_app)
    client = await _client_for(app)
    async with client:
        response = await client.get("/x")
        assert response.headers["strict-transport-security"] == (
            "max-age=31536000; includeSubDomains"
        )
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["permissions-policy"]


async def test_request_id_header_is_added() -> None:
    app = RequestIdMiddleware(_ok_app)
    client = await _client_for(app)
    async with client:
        response = await client.get("/x")
        assert response.headers["x-request-id"]
        assert len(response.headers["x-request-id"]) == 32  # uuid4().hex


def test_parse_range_valid() -> None:
    assert _parse_range("bytes=0-4", 10) == (0, 4)
    assert _parse_range("bytes=5-", 10) == (5, 9)
    assert _parse_range("bytes=-3", 10) == (7, 9)
    assert _parse_range("bytes=0-99", 10) == (0, 9)  # fim clampeado


def test_parse_range_invalid() -> None:
    assert _parse_range("bytes=10-11", 10) is None  # início >= total
    assert _parse_range("bytes=5-2", 10) is None  # fim < início
    assert _parse_range("bytes=abc", 10) is None
    assert _parse_range("items=0-4", 10) is None
    assert _parse_range("bytes=0-4,5-8", 10) is None  # multipart não suportado
    assert _parse_range("", 10) is None


def test_code_to_status_mapping() -> None:
    assert code_to_status(ErrorCode.VALIDATION_ERROR) == 400
    assert code_to_status(ErrorCode.NOT_FOUND) == 404
    assert code_to_status(ErrorCode.CONFLICT) == 409
    assert code_to_status(ErrorCode.RATE_LIMITED) == 429
    assert code_to_status(ErrorCode.MODEL_TIMEOUT) == 504
    assert code_to_status(ErrorCode.MODEL_UNAVAILABLE) == 503
    assert code_to_status(ErrorCode.MODEL_INVALID_RESPONSE) == 502
    assert code_to_status(ErrorCode.EMBEDDING_DIMENSION_MISMATCH) == 502
    assert code_to_status(ErrorCode.VERIFICATION_FAILED) == 502
    assert code_to_status(ErrorCode.STORAGE_ERROR) == 500
    assert code_to_status(ErrorCode.DATABASE_ERROR) == 500
    assert code_to_status(ErrorCode.INTERNAL_ERROR) == 500
    assert code_to_status(ErrorCode.CANCELLED) == 409
