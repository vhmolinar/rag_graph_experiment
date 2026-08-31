"""Testes da aplicação FastAPI sem banco (T14; AC-18).

Cobre: OpenAPI validada, headers de segurança e `X-Request-ID` na resposta,
validação 422 tipada, 404 com envelope, CORS restrito. Não requer banco —
os caminhos exercitados não tocam PostgreSQL (o lifespan não é executado pelo
ASGI transport).
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import (
    ConceptEmbeddingProvider,
    FakeGeneratorProvider,
    FakeRerankerProvider,
    FakeVerifierProvider,
)

from rag.api.app import create_app
from rag.api.settings import ApiSettings
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.config import DatabaseSettings
from rag.infrastructure.db import Database

_REQUIRED_PATHS = {
    "/api/v1/queries",
    "/api/v1/queries/{query_id}",
    "/api/v1/queries/{query_id}/events",
    "/api/v1/queries/{query_id}/cancel",
    "/api/v1/works",
    "/api/v1/works/{work_id}",
    "/api/v1/editions/{edition_id}",
    "/api/v1/editions/{edition_id}/passages/{passage_id}",
    "/api/v1/editions/{edition_id}/source",
    "/api/v1/sessions",
    "/api/v1/sessions/{session_id}",
    "/api/v1/health/live",
    "/api/v1/health/ready",
}


def _unopened_db() -> Database:
    """Database não aberto: suficiente para os caminhos sem banco desta file."""
    return Database(
        DatabaseSettings(host="localhost", port=1, db="x", user="x", password=SecretStr(""))
    )


def _app() -> FastAPI:
    store_root = Path(tempfile.mkdtemp(prefix="rag-api-test-artifacts-"))
    return create_app(
        db=_unopened_db(),
        store=ArtifactStore(store_root),
        settings=ApiSettings(
            cors_allowed_origins="http://localhost:5173", rate_limit_per_minute=10000
        ),
        embedding_provider=ConceptEmbeddingProvider(),
        reranker_provider=FakeRerankerProvider(),
        generator_provider=FakeGeneratorProvider(),
        verifier_provider=FakeVerifierProvider(),
        planner_provider=None,
        generator_model_name="fake-generator",
    )


async def _client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_openapi_is_well_formed() -> None:
    app = _app()
    spec = app.openapi()
    assert spec["openapi"].startswith("3.")
    paths = spec["paths"]
    assert set(paths) >= _REQUIRED_PATHS
    for path, operations in paths.items():
        for operation in operations.values():
            for status_code, response in operation.get("responses", {}).items():
                if int(status_code) in (204, 304):
                    continue  # respostas sem corpo são válidas
                assert "content" in response, f"resposta sem content: {path} {operation}"


async def test_liveness_returns_ok_with_security_headers() -> None:
    app = _app()
    client = await _client_for(app)
    async with client:
        response = await client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["x-request-id"]
        assert response.headers["strict-transport-security"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


async def test_validation_error_returns_422_envelope() -> None:
    app = _app()
    client = await _client_for(app)
    async with client:
        response = await client.post("/api/v1/queries", json={"question": ""})
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"]
        assert body["error"]["request_id"]


async def test_unknown_route_returns_envelope() -> None:
    app = _app()
    client = await _client_for(app)
    async with client:
        response = await client.get("/api/v1/nope")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["request_id"]


async def test_cors_allows_configured_origin() -> None:
    app = _app()
    client = await _client_for(app)
    async with client:
        response = await client.options(
            "/api/v1/works",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_cors_rejects_foreign_origin() -> None:
    app = _app()
    client = await _client_for(app)
    async with client:
        response = await client.get(
            "/api/v1/health/live",
            headers={"Origin": "http://evil.example"},
        )
        assert response.headers.get("access-control-allow-origin") is None


async def test_error_response_never_leaks_internals() -> None:
    """O handler de exceção genérica sanitiza: código + mensagem + request_id,
    sem traceback ou detalhes do erro interno."""

    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from rag.api.errors import _handle_unhandled

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [],
            "state": {"request_id": "abc"},
        }
    )
    response = _handle_unhandled(request, RuntimeError("segredo=topsecret /home/user/x.sql"))
    assert isinstance(response, JSONResponse)
    body = bytes(response.body).decode()
    assert response.status_code == 500
    assert "segredo" not in body
    assert "/home" not in body
    assert "Traceback" not in body
    payload = json.loads(body)
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"] == "Erro interno do servidor."
    assert payload["error"]["request_id"] == "abc"
