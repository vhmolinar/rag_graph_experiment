"""Segurança HTTP da API (T14; SPEC §14, AC-18): rate limiting por token
bucket, CORS restrito e headers de segurança. Middlewares ASGI puros
(no `BaseHTTPMiddleware`) para preservar streaming/SSE e a detecção de
disconexão.

Rate limiting: token bucket por endereço IP do cliente, implementação própria
sem dependência nova (NOTES.md §10.2 item 6). O relógio é inyectável para
testes. Endpoints de health ficam isentos (probes de orquestração).
"""

import json
import math
import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rag.api.settings import ApiSettings

_LOGGER = structlog.get_logger(__name__)

_SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # A API serve JSON/SSE; nenhuna origem pode executar script a partir dela.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
}

_JSON_CONTENT_TYPE = "application/json"


class TokenBucket:
    """Token bucket com relógio monotónico inyectável (para testes)."""

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._capacity = max(1.0, float(capacity))
        self._refill = max(0.0, float(refill_per_second))
        self._now = now or time.monotonic
        self._tokens = self._capacity
        self._last = self._now()

    def consume(self, tokens: float = 1.0) -> bool:
        now = self._now()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill)
        self._last = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def retry_after(self) -> int:
        """Segundos até que ao menos um token esteja disponível."""
        missing = 1.0 - self._tokens
        if missing <= 0:
            return 1
        return max(1, math.ceil(missing / self._refill)) if self._refill > 0 else 60


class RequestIdMiddleware:
    """Gera `request_id` por requisição, liga ao contexto de logs (structlog)
    e expõe-o no header `X-Request-ID` de qualquer resposta (incluindo 429/erros)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        method = scope.get("method", "?")
        path = scope.get("path", "?")
        started = time.perf_counter()
        status_code = 0
        started_sent = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, started_sent
            if message["type"] == "http.response.start":
                status_code = message["status"]
                started_sent = True
                headers = MutableHeaders(raw=message["headers"])
                headers.append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            _LOGGER.exception("http.request_error", method=method, path=path)
            if not started_sent:
                # Falha em middleware interno antes de qualquer resposta: devolve
                # o envelope sanitizado (nunca stack trace/SQL/caminhos).
                body = json.dumps(
                    {
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "Erro interno do servidor.",
                            "request_id": request_id,
                        }
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                headers = [
                    (b"content-type", _JSON_CONTENT_TYPE.encode()),
                    (b"content-length", str(len(body)).encode()),
                ]
                await send_wrapper(
                    {"type": "http.response.start", "status": 500, "headers": headers}
                )
                await send_wrapper({"type": "http.response.body", "body": body})
                return
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        _LOGGER.info(
            "http.response",
            method=method,
            path=path,
            status=status_code,
            duration_ms=round(duration_ms, 1),
        )


class SecurityHeadersMiddleware:
    """Aplica headers de segurança a toda resposta (SPEC §14)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RateLimitMiddleware:
    """Token bucket por IP (SPEC §14). Resposta 429 com `Retry-After`."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        rate_limit_per_minute: int,
        exempt_prefixes: tuple[str, ...] = ("/api/v1/health/",),
        now: Callable[[], float] | None = None,
    ) -> None:
        self.app = app
        self._refill = rate_limit_per_minute / 60.0
        self._capacity = float(rate_limit_per_minute)
        self._exempt_prefixes = exempt_prefixes
        self._now = now or time.monotonic
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self._capacity, self._refill, now=self._now)
            self._buckets[key] = bucket
        return bucket

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt_prefixes):
            return await self.app(scope, receive, send)
        client = scope.get("client")
        key = client[0] if client else "unknown"
        bucket = self._bucket(key)
        if bucket.consume():
            return await self.app(scope, receive, send)
        request_id = scope.get("state", {}).get("request_id", "desconhecido")
        retry_after = bucket.retry_after()
        body = json.dumps(
            {
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Limite de requisições excedido.",
                    "request_id": request_id,
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [
            (b"content-type", _JSON_CONTENT_TYPE.encode()),
            (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(retry_after).encode()),
        ]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def install_security(
    app: FastAPI,
    settings: ApiSettings,
    *,
    now: Callable[[], float] | None = None,
) -> None:
    """Registra middlewares na ordem desejada.

    Com `add_middleware`, o primeiro registrado fica mais externo (starlette
    `build_middleware_stack` inverte a lista). Ordem desejada (externo→interno):
    RequestId → SecurityHeaders → RateLimit → CORS. Assim a resposta 429 do
    rate limiter recebe o `X-Request-ID` e os headers de segurança.
    """
    app.add_middleware(
        RequestIdMiddleware,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
    )
    app.add_middleware(
        RateLimitMiddleware,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        now=now,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "X-Request-ID"],
        allow_credentials=False,
        max_age=86400,
    )
