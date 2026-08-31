"""Testes unitários do broker de eventos SSE e da formatação (T14; AC-18).

Cobre: formatação SSE (`event:`/`data:`), eventos terminais, subscriptores
conectados depois do fim, e desinscrição.
"""

import asyncio
from uuid import uuid4

from rag.api.events import EventBroker, QueryEvent, format_sse


def test_format_sse_status_event() -> None:
    event = QueryEvent(event="status", data={"query_id": "q", "stage": "planning"})
    text = format_sse(event)
    assert text.startswith("event: status\n")
    assert 'data: {"query_id":"q","stage":"planning"}' in text
    assert text.endswith("\n\n")
    assert not event.is_terminal


def test_format_sse_result_event_is_terminal() -> None:
    event = QueryEvent(event="result", data={"query_id": "q", "status": "succeeded"})
    assert event.is_terminal
    text = format_sse(event)
    assert text.startswith("event: result\n")
    assert 'data: {"query_id":"q","status":"succeeded"}' in text


def test_broker_delivers_events_to_subscriber() -> None:
    broker = EventBroker()
    query_id = uuid4()
    queue = broker.subscribe(query_id)
    first = QueryEvent(event="status", data={"stage": "planning"})
    second = QueryEvent(event="result", data={"status": "succeeded"})
    broker.publish(query_id, first)
    broker.publish(query_id, second)
    assert queue.get_nowait() == first
    assert queue.get_nowait() == second


def test_broker_terminal_available_to_late_subscriber() -> None:
    broker = EventBroker()
    query_id = uuid4()
    terminal = QueryEvent(event="result", data={"status": "succeeded"})
    broker.publish(query_id, terminal)
    assert broker.terminal(query_id) == terminal
    # Um subscriptor posterior não recebe o evento na cola (vazio), mas o
    # terminal fica disponível — o handler SSE o devolve e encerra.
    queue = broker.subscribe(query_id)
    assert queue.empty()
    assert broker.terminal(query_id) == terminal


def test_broker_unsubscribe_stops_delivery() -> None:
    broker = EventBroker()
    query_id = uuid4()
    queue = broker.subscribe(query_id)
    broker.unsubscribe(query_id, queue)
    broker.publish(query_id, QueryEvent(event="status", data={"stage": "x"}))
    assert queue.empty()
    assert broker.terminal(query_id) is None  # não-terminal não é guardado


def test_broker_clear_removes_state() -> None:
    broker = EventBroker()
    query_id = uuid4()
    broker.publish(query_id, QueryEvent(event="result", data={"status": "failed"}))
    broker.clear(query_id)
    assert broker.terminal(query_id) is None


async def test_broker_works_across_async_boundaries() -> None:
    """Publicar e consumir de tarefas separadas (o uso real do SSE)."""
    broker = EventBroker()
    query_id = uuid4()
    queue = broker.subscribe(query_id)
    broker.publish(query_id, QueryEvent(event="status", data={"stage": "retrieval"}))
    terminal = QueryEvent(event="result", data={"status": "succeeded"})
    broker.publish(query_id, terminal)
    received: list[QueryEvent] = []
    for _ in range(2):
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        received.append(event)
    assert [e.event for e in received] == ["status", "result"]
    assert broker.terminal(query_id) == terminal
