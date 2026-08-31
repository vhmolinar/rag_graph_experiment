"""Eventos SSE e broker em memória por query (T14; SPEC §10.1, AC-18).

`POST /queries` publica eventos de estado por estágio; `GET
/queries/{id}/events` os streamea via SSE (`text/event-stream`, implementação
própria — sem `sse-starlette`, fora do conjunto aprovado).

O `EventBroker` mantém, por query: um conjunto de colas de subscriptores e o
último evento terminal. Um cliente conectado DEPOIS do fim recebe o evento
terminal imediatamente; o stream encerra em sucesso, erro e cancelamento.
"""

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

_TERMINAL_EVENTS = frozenset({"result"})


@dataclass(frozen=True)
class QueryEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.event in _TERMINAL_EVENTS


def format_sse(event: QueryEvent) -> str:
    """Serializa um evento no formato SSE (`event:` + `data:` + linha vazia)."""
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n"


class EventBroker:
    """Publicador/subscriptor de eventos por query, seguro para o loop único
    asyncio do processo (NOTES.md §10.2 item 1)."""

    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[QueryEvent]]] = defaultdict(set)
        self._terminal: dict[UUID, QueryEvent] = {}

    def publish(self, query_id: UUID, event: QueryEvent) -> None:
        for queue in list(self._subscribers.get(query_id, ())):
            queue.put_nowait(event)
        if event.is_terminal:
            self._terminal[query_id] = event

    def subscribe(self, query_id: UUID) -> asyncio.Queue[QueryEvent]:
        queue: asyncio.Queue[QueryEvent] = asyncio.Queue()
        self._subscribers[query_id].add(queue)
        return queue

    def unsubscribe(self, query_id: UUID, queue: asyncio.Queue[QueryEvent]) -> None:
        self._subscribers.get(query_id, set()).discard(queue)

    def terminal(self, query_id: UUID) -> QueryEvent | None:
        return self._terminal.get(query_id)

    def clear(self, query_id: UUID) -> None:
        self._subscribers.pop(query_id, None)
        self._terminal.pop(query_id, None)
