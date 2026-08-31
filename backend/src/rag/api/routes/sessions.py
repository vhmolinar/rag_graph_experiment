"""Rotas de sessões (SPEC §10.3): criar, consultar e excluir.

Contexto de sessão e reescrita de follow-up são T15 (AC-13); aqui só o
contrato mínimo de identidade/timestamps (NOTES.md §10.15 item 3).
"""

from uuid import UUID

from fastapi import APIRouter, Request, Response

from rag.api.deps import AppDependencies
from rag.api.schemas import SessionOut
from rag.domain.errors import NotFoundError
from rag.domain.sessions import Session
from rag.infrastructure.repositories.sessions import SessionsRepository

router = APIRouter(tags=["sessions"])


def _deps(request: Request) -> AppDependencies:
    return request.app.state.deps  # type: ignore[no-any-return]


@router.post("/sessions", status_code=201, response_model=SessionOut)
async def create_session(request: Request) -> SessionOut:
    deps = _deps(request)
    session = Session()
    async with deps.db.connection() as conn:
        session = await SessionsRepository(conn).create(session)
    return SessionOut(
        session_id=session.id,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
    )


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: UUID, request: Request) -> SessionOut:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        session = await SessionsRepository(conn).get(session_id)
    if session is None:
        raise NotFoundError("Sessão não encontrada.", context={"session_id": str(session_id)})
    return SessionOut(
        session_id=session.id,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID, request: Request) -> Response:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        await SessionsRepository(conn).delete(session_id)
    return Response(status_code=204)
