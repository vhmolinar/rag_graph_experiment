"""Rotas de consultas (SPEC §10.1): criar, estado, eventos SSE, cancelamento."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from rag.api.deps import AppDependencies
from rag.api.events import EventBroker, QueryEvent, format_sse
from rag.api.query_runner import QueryExecutor
from rag.api.schemas import QueryAccepted, QueryCancelled, QueryState, build_query_state
from rag.domain.errors import ConflictError, NotFoundError
from rag.domain.query import QueryRequest
from rag.domain.runs import AnswerRun
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.sessions import SessionsRepository

router = APIRouter(tags=["queries"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
_KEEPALIVE_SECONDS = 15.0


def _deps(request: Request) -> AppDependencies:
    return request.app.state.deps  # type: ignore[no-any-return]


@router.post("/queries", status_code=202, response_model=QueryAccepted)
async def create_query(payload: QueryRequest, request: Request) -> QueryAccepted:
    deps = _deps(request)
    query_id = uuid4()
    request_id = str(getattr(request.state, "request_id", ""))
    # O `AnswerRun` é criado SINCRONAMENTE (status `queued`) antes de devolver
    # 202 — um `GET /queries/{id}` imediatamente subsequente nunca devolve 404
    # (revisão T14: corrida do ciclo POST→GET). Validação síncrona da sessão
    # (4xx rápido; a rota é a única autoridade da criação).
    async with deps.db.connection() as conn:
        if (
            payload.session_id is not None
            and await SessionsRepository(conn).get(payload.session_id) is None
        ):
            raise NotFoundError(
                "Sessão não encontrada.",
                context={"session_id": str(payload.session_id)},
            )
        run = AnswerRun(
            id=query_id,
            question_original=payload.question,
            question_anonymized=payload.question,
            explicit_filters=payload.explicit_filter(),
            session_id=payload.session_id,
            request_id=request_id,
        )
        await AnswerRunsRepository(conn).create(run)
    deps.registry.start(query_id, QueryExecutor(deps).run(query_id, payload, run))
    deps.broker.publish(
        query_id,
        QueryEvent(
            event="status",
            data={"query_id": str(query_id), "status": "queued", "stage": "queued"},
        ),
    )
    return QueryAccepted(query_id=query_id, status="queued")


@router.get("/queries/{query_id}", response_model=QueryState)
async def get_query(query_id: UUID, request: Request) -> QueryState:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        run = await AnswerRunsRepository(conn).get(query_id)
    if run is None:
        raise NotFoundError("Consulta não encontrada.", context={"query_id": str(query_id)})
    return build_query_state(run)


@router.get("/queries/{query_id}/events")
async def query_events(query_id: UUID, request: Request) -> StreamingResponse:
    deps = _deps(request)
    async with deps.db.connection() as conn:
        run = await AnswerRunsRepository(conn).get(query_id)
    if run is None:
        raise NotFoundError("Consulta não encontrada.", context={"query_id": str(query_id)})
    broker: EventBroker = deps.broker

    async def event_stream() -> AsyncIterator[str]:
        # Cliente conectado DEPOIS do fim: o último evento terminal é devolvido
        # imediatamente e o stream encerra (NOTES.md §10.15 item 6).
        terminal = broker.terminal(query_id)
        if terminal is not None:
            yield format_sse(terminal)
            return
        queue = broker.subscribe(query_id)
        try:
            # Janela atómica sem await: se o terminal foi publicado entre o
            # check acima e a subscripta, o evento já está na cola; se for
            # publicado depois, a subscripta já o recebe.
            terminal = broker.terminal(query_id)
            if terminal is not None:
                yield format_sse(terminal)
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield format_sse(event)
                if event.is_terminal:
                    return
        finally:
            broker.unsubscribe(query_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/queries/{query_id}/cancel", status_code=202, response_model=QueryCancelled)
async def cancel_query(query_id: UUID, request: Request) -> QueryCancelled:
    deps = _deps(request)
    if deps.registry.request_cancel(query_id):
        return QueryCancelled(query_id=query_id, status="cancelling")
    async with deps.db.connection() as conn:
        run = await AnswerRunsRepository(conn).get(query_id)
    if run is None:
        raise NotFoundError("Consulta não encontrada.", context={"query_id": str(query_id)})
    raise ConflictError(
        "Consulta já concluída; cancelamento não é possível.",
        context={"query_id": str(query_id), "status": run.status.value},
    )
