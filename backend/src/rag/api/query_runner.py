"""Executor de consulta em processo (T14; NOTES.md §10.2 item 1, AC-18).

`POST /queries` agenda uma tarefa asyncio (`QueryRegistry.start`); o
`QueryExecutor` orquesta as etapas da especificação: planejamento (T10) →
recuperação (T09) → montagem de contexto (T12) → modo quote/dissertativo
(T12/T13). Cada etapa publica um evento SSE e o flag de cancelamento
cooperativo (`asyncio.Event`) é verificado ENTRE as etapas — nunca dentro de
uma chamada ao provedor de modelo (NOTES.md §10.15 item 1).

O `AnswerRun` é persistido em cada transição (status/plan/candidatos/response/
verification); o executor mantém o `RetrievalResult` em memória entre a
recuperação e a montagem de contexto (não é columna do schema — T09/T12).
"""

import time
from asyncio import CancelledError
from uuid import UUID

import structlog

from rag.api.deps import AppDependencies
from rag.api.events import QueryEvent
from rag.api.schemas import build_query_state
from rag.domain.enums import AnswerMode, QueryStatus
from rag.domain.errors import ErrorCode, NotFoundError, RagError
from rag.domain.planning import merge_filters
from rag.domain.query import QueryPlan, QueryRequest
from rag.domain.retrieval import RetrievalResult
from rag.domain.runs import AnswerRun, StageLatency
from rag.infrastructure.repositories.runs import AnswerRunsRepository
from rag.infrastructure.repositories.sessions import SessionsRepository

_LOGGER = structlog.get_logger(__name__)


class QueryExecutor:
    """Orquestra a pipeline de consulta num único processo."""

    def __init__(self, deps: AppDependencies) -> None:
        self._deps = deps

    async def run(self, query_id: UUID, request: QueryRequest) -> None:
        run: AnswerRun | None = None
        try:
            run = await self._create_and_start(query_id, request)
            self._emit_status(query_id, run, stage="planning")
            if self._cancelled(query_id):
                await self._cancel(query_id, run)
                return

            run = await self._plan(query_id, run, request)
            self._emit_status(query_id, run, stage="retrieval")
            if self._cancelled(query_id):
                await self._cancel(query_id, run)
                return

            retrieval = await self._retrieve(query_id, run, request)
            self._emit_status(query_id, run, stage="context")
            if self._cancelled(query_id):
                await self._cancel(query_id, run)
                return

            if request.answer_mode is AnswerMode.QUOTE:
                run = await self._quote(query_id, run, request, retrieval)
            else:
                run = await self._dissertate(query_id, run, request, retrieval)
            self._emit_result(query_id, run)
        except CancelledError:
            raise
        except RagError as exc:
            await self._fail(query_id, run, exc)
        except Exception:
            _LOGGER.exception("query.unexpected_error", query_id=str(query_id))
            await self._fail_internal(query_id, run)
        finally:
            self._deps.registry.complete(query_id)

    async def _create_and_start(self, query_id: UUID, request: QueryRequest) -> AnswerRun:
        async with self._deps.db.connection() as conn:
            if request.session_id is not None:
                session = await SessionsRepository(conn).get(request.session_id)
                if session is None:
                    raise NotFoundError(
                        "Sessão não encontrada.",
                        context={"session_id": str(request.session_id)},
                    )
            run = AnswerRun(
                id=query_id,
                question_original=request.question,
                question_anonymized=request.question,
                explicit_filters=request.explicit_filter(),
                session_id=request.session_id,
            )
            runs = AnswerRunsRepository(conn)
            run = await runs.create(run)
            run = await runs.save(run.transition(QueryStatus.RUNNING))
        return run

    async def _plan(self, query_id: UUID, run: AnswerRun, request: QueryRequest) -> AnswerRun:
        started = time.perf_counter()
        async with self._deps.db.connection() as conn:
            plan = await self._deps.planner.plan(conn, request)
            run = await AnswerRunsRepository(conn).save(
                run.transition(
                    QueryStatus.RUNNING,
                    plan=plan,
                    inferred_filters=plan.inferred_filters,
                    rewritten_query=plan.semantic_query,
                    latencies=(StageLatency(stage="planning", duration_ms=_duration_ms(started)),),
                )
            )
        return run

    async def _retrieve(
        self, query_id: UUID, run: AnswerRun, request: QueryRequest
    ) -> RetrievalResult:
        plan = self._require_plan(run)
        filters = merge_filters(request.explicit_filter(), plan.inferred_filters)
        started = time.perf_counter()
        async with self._deps.db.connection() as conn:
            retrieval = await self._deps.retrieval.retrieve(
                conn,
                lexical_query=plan.lexical_query,
                semantic_query=plan.semantic_query,
                filters=filters,
                policy=self._deps.retrieval_policy,
                depth=request.depth,
            )
            run = await AnswerRunsRepository(conn).save(
                run.transition(
                    QueryStatus.RUNNING,
                    candidates=retrieval.answer_run_candidates(),
                    latencies=(StageLatency(stage="retrieval", duration_ms=_duration_ms(started)),),
                )
            )
        return retrieval

    async def _quote(
        self,
        query_id: UUID,
        run: AnswerRun,
        request: QueryRequest,
        retrieval: RetrievalResult,
    ) -> AnswerRun:
        plan = self._require_plan(run)
        async with self._deps.db.connection() as conn:
            quote = await self._deps.context.quote(
                conn,
                plan=plan,
                retrieval=retrieval,
                depth=request.depth,
                policy=self._deps.context_policy,
            )
            run = await AnswerRunsRepository(conn).save(
                run.transition(QueryStatus.SUCCEEDED, response=quote)
            )
        return run

    async def _dissertate(
        self,
        query_id: UUID,
        run: AnswerRun,
        request: QueryRequest,
        retrieval: RetrievalResult,
    ) -> AnswerRun:
        plan = self._require_plan(run)
        async with self._deps.db.connection() as conn:
            packed = await self._deps.context.assemble(
                conn,
                plan=plan,
                retrieval=retrieval,
                depth=request.depth,
                policy=self._deps.context_policy,
            )
        self._emit_status(query_id, run, stage="generation")
        async with self._deps.db.connection() as conn:
            diss = await self._deps.dissertative.answer(
                conn,
                question=request.question,
                session_context=None,  # contexto de sessão é T15
                depth=request.depth,
                plan=plan,
                packed=packed,
                verification_policy=self._deps.verification_policy,
                model_name=self._deps.generator_model_name,
            )
            status = QueryStatus.ABSTAINED if diss.answer.abstained else QueryStatus.SUCCEEDED
            run = await AnswerRunsRepository(conn).save(
                run.transition(
                    status,
                    response=diss.answer,
                    verification=diss.verification,
                    selected_evidence_ids=tuple(
                        item.evidence.passage_id for item in packed.evidences
                    ),
                )
            )
        return run

    def _require_plan(self, run: AnswerRun) -> QueryPlan:
        """Plano é invariante interno da execução; ausência é bug (falha fechada)."""
        if run.plan is None:
            raise RuntimeError("plano ausente na execução de consulta")
        return run.plan

    def _cancelled(self, query_id: UUID) -> bool:
        return self._deps.registry.is_cancelled(query_id)

    async def _cancel(self, query_id: UUID, run: AnswerRun | None) -> None:
        if run is None:
            return
        async with self._deps.db.connection() as conn:
            run = await AnswerRunsRepository(conn).save(run.transition(QueryStatus.CANCELLED))
        self._emit_result(query_id, run)

    async def _fail(self, query_id: UUID, run: AnswerRun | None, exc: RagError) -> None:
        _LOGGER.info("query.failed", query_id=str(query_id), code=exc.code.value, error=exc.message)
        if run is None:
            return
        async with self._deps.db.connection() as conn:
            run = await AnswerRunsRepository(conn).save(
                run.transition(
                    QueryStatus.FAILED,
                    error_code=exc.code,
                    error_message=exc.message,
                )
            )
        self._emit_result(query_id, run)

    async def _fail_internal(self, query_id: UUID, run: AnswerRun | None) -> None:
        if run is None:
            return
        async with self._deps.db.connection() as conn:
            run = await AnswerRunsRepository(conn).save(
                run.transition(
                    QueryStatus.FAILED,
                    error_code=ErrorCode.INTERNAL_ERROR,
                    error_message="Erro interno do servidor.",
                )
            )
        self._emit_result(query_id, run)

    def _emit_status(self, query_id: UUID, run: AnswerRun, *, stage: str) -> None:
        self._deps.broker.publish(
            query_id,
            QueryEvent(
                event="status",
                data={
                    "query_id": str(query_id),
                    "status": run.status.value,
                    "stage": stage,
                },
            ),
        )

    def _emit_result(self, query_id: UUID, run: AnswerRun) -> None:
        self._deps.broker.publish(
            query_id,
            QueryEvent(
                event="result",
                data=build_query_state(run).model_dump(mode="json"),
            ),
        )


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
