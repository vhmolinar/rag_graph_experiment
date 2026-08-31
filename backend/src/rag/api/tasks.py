"""Registro de tarefas ativas e flags de cancelamento por query (T14).

Estado do processo (single worker, NOTAS.md §10.2 item 1); manipulado só no
loop asyncio do processo, sem locks. `QueryExecutor` (query_runner) consulta
`is_cancelled` entre as etapas; `POST /queries/{id}/cancel` marca o flag
cooperativo.
"""

import asyncio
from collections.abc import Coroutine
from uuid import UUID


class QueryRegistry:
    def __init__(self) -> None:
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._cancel_events: dict[UUID, asyncio.Event] = {}

    def start(self, query_id: UUID, coro: Coroutine[None, None, None]) -> None:
        self._tasks[query_id] = asyncio.create_task(coro)
        self._cancel_events[query_id] = asyncio.Event()

    def is_active(self, query_id: UUID) -> bool:
        task = self._tasks.get(query_id)
        return task is not None and not task.done()

    def request_cancel(self, query_id: UUID) -> bool:
        """Marca o flag cooperativo; devolve False se a query não existir."""
        event = self._cancel_events.get(query_id)
        if event is None:
            return False
        event.set()
        return True

    def is_cancelled(self, query_id: UUID) -> bool:
        event = self._cancel_events.get(query_id)
        return event is not None and event.is_set()

    def complete(self, query_id: UUID) -> None:
        self._tasks.pop(query_id, None)
        self._cancel_events.pop(query_id, None)

    async def shutdown(self) -> None:
        """Cancela as tarefas ativas no encerramento da aplicação."""
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._cancel_events.clear()
