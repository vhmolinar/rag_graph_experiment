"""Health endpoints: liveness e readiness (SPEC §14, checklist §14).

- `GET /health/live`: responde 200 sem consultar dependências — nunca causa
  restart por falha transitória externa;
- `GET /health/ready`: consulta PostgreSQL (`SELECT 1` com timeout curto) e
  responde 503 se o banco estiver indisponível.

Ambos ficam isentos do rate limiting (probes de orquestração).
"""

import asyncio

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from rag.api.deps import AppDependencies

router = APIRouter(tags=["health"])

_READINESS_TIMEOUT = 2.0


def _deps(request: Request) -> AppDependencies:
    return request.app.state.deps  # type: ignore[no-any-return]


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    deps = _deps(request)
    try:
        async with asyncio.timeout(_READINESS_TIMEOUT):
            async with deps.db.connection() as conn:
                await conn.execute("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready"})
