"""Testes de integração da API FastAPI contra PostgreSQL real (T14).

Cubre os entregáveis de T14:

- fluxo de consulta quote e dissertativo (planejamento → recuperação →
  contexto → resposta) via HTTP, com doubles de provedores (sem rede);
- abstenção (AC-10) e erro interno sanitizado (AC-14/AC-18);
- rate limiting 429 + `Retry-After` (AC-18);
- cancelamento cooperativo interrompe o trabalho (AC-18);
- SSE: stream de eventos com terminal `result` (AC-18);
- range requests do artefato de origem (SPEC §10.2);
- CRUD de sessões e validação de body/query/path (4xx tipado).

Requer Docker (PostgreSQL via testcontainers); sem Docker os testes são
saltados pelo marker `integration`.
"""

import asyncio
import hashlib
import io
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from builders import make_text_pdf
from model_doubles import (
    ConceptEmbeddingProvider,
    FakeGeneratorProvider,
    FakePlannerProvider,
    FakeRerankerProvider,
    FakeVerifierProvider,
    abstention_answer,
)

from rag.api.app import create_app
from rag.api.schemas import QueryState
from rag.api.settings import ApiSettings
from rag.domain.answer import GeneratedAnswer, QuoteResponse
from rag.domain.enums import AnswerMode, QueryStatus, SearchStrategy, SourceType
from rag.domain.library import Edition, Page, Passage, Section, Work
from rag.domain.providers import GenerationRequest
from rag.domain.versions import ChunkingVersion, utcnow
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.config import DatabaseSettings
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository
from tests.integration.conftest import PgParams

pytestmark = pytest.mark.integration

_ALL_TABLES = (
    "session_entries",
    "answer_runs",
    "sessions",
    "concept_evidence",
    "concept_aliases",
    "concepts",
    "summary_supports",
    "summaries",
    "passages",
    "pages",
    "sections",
    "derived_artifacts",
    "editions",
    "contributors",
    "works",
    "extraction_versions",
    "chunking_versions",
    "embedding_versions",
    "model_endpoint_versions",
    "prompt_versions",
    "retrieval_policy_versions",
    "context_policy_versions",
    "verification_policy_versions",
)

_A_TEXT = "spleen sufoca a liberdade de Bentinho."
_QUERY = "qual é a concepção de liberdade do autor?"

BuildApp = Callable[..., Awaitable[FastAPI]]
ClientFor = Callable[[FastAPI], Awaitable[httpx.AsyncClient]]


@dataclass(frozen=True)
class Corpus:
    edition_a: UUID
    source_sha256: str


async def _seed(db: Database, store: ArtifactStore) -> Corpus:
    """Semilla corpus mínimo: obra A, edição, seção, página e passagem."""
    pdf_bytes = make_text_pdf([["Capítulo I", "texto de exemplo para range requests"]])
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    with io.BytesIO(pdf_bytes) as stream:
        store.put(stream, sha, original_filename="fixture.pdf")

    async with db.connection() as conn:
        work_a = await WorksRepository(conn).create(Work(canonical_title="Dom Casmurro"))
        edition_a = await EditionsRepository(conn).create(
            Edition(
                work_id=work_a.id,
                title="Dom Casmurro (1942)",
                source_type=SourceType.PDF_TEXT,
                source_sha256=sha,
            )
        )
        section = Section(
            edition_id=edition_a.id,
            level=0,
            ordinal=0,
            path=["Capítulo I"],
            title="Capítulo I",
        )
        await SectionsRepository(conn).create_many([section])
        page = Page.create(
            edition_id=edition_a.id, physical_index=0, text=_A_TEXT, printed_label="p. 1"
        )
        await PagesRepository(conn).create_many([page])
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="api-chunk", created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        embedding_version = await versions.get_or_create(provider.embedding_version)
        passage_id = UUID("a0000000-0000-0000-0000-00000000cafe")
        await PassagesRepository(conn).create(
            Passage(
                id=passage_id,
                edition_id=edition_a.id,
                ordinal=0,
                text=_A_TEXT,
                token_count=len(_A_TEXT.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=section.id,
                page_start_id=page.id,
                page_end_id=page.id,
                char_start=0,
                char_end=len(_A_TEXT),
            ),
            embedding=await provider.embed_query(_A_TEXT),
        )
        await conn.execute("ANALYZE passages")
    return Corpus(edition_a=edition_a.id, source_sha256=sha)


class BlockingEmbeddingProvider(ConceptEmbeddingProvider):
    """Double que bloqueia na `embed_query` até `release` ser definido —
    permite cancelar uma consulta durante a recuperação."""

    def __init__(self, enter: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__()
        self._enter = enter
        self._release = release

    async def embed_query(self, text: str) -> list[float]:
        self._enter.set()
        await self._release.wait()
        return await super().embed_query(text)


@pytest.fixture
async def api(
    migrated: PgParams, tmp_path: Path
) -> AsyncIterator[tuple[FastAPI, BuildApp, ClientFor]]:
    """Harness: apps de API contra o banco real, com doubles de provedores."""
    dbs: list[Database] = []

    async def _open_db() -> Database:
        settings = DatabaseSettings(
            host=migrated["host"],
            port=migrated["port"],
            db=migrated["db"],
            user=migrated["user"],
            password=SecretStr(migrated["password"]),
        )
        db = Database(settings)
        await db.open()
        dbs.append(db)
        return db

    async def build(
        *,
        rate_limit_per_minute: int = 10000,
        embedding_provider: Any = None,
        reranker_provider: Any = None,
        generator_provider: Any = None,
        verifier_provider: Any = None,
        planner_provider: Any = None,
    ) -> FastAPI:
        db = await _open_db()
        store = ArtifactStore(tmp_path / f"artifacts-{len(dbs)}")
        # Por padrão o harness injeta um provedor de planejamento determinístico
        # (FakePlannerProvider) — sem provedor real de modelo no ambiente de teste.
        planner_provider = planner_provider or FakePlannerProvider()
        return create_app(
            db=db,
            store=store,
            settings=ApiSettings(
                cors_allowed_origins="http://localhost:5173",
                rate_limit_per_minute=rate_limit_per_minute,
            ),
            embedding_provider=embedding_provider or ConceptEmbeddingProvider(),
            reranker_provider=reranker_provider or FakeRerankerProvider(),
            generator_provider=generator_provider or FakeGeneratorProvider(),
            verifier_provider=verifier_provider or FakeVerifierProvider(),
            planner_provider=planner_provider,
            generator_model_name="fake-generator",
        )

    async def client_for(app: FastAPI) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    default_app = await build()
    yield default_app, build, client_for
    for db in dbs:
        try:
            async with db.connection() as conn:
                await conn.execute(f"TRUNCATE {', '.join(_ALL_TABLES)} CASCADE")
        finally:
            await db.close()


async def _post_query(
    client: httpx.AsyncClient, *, question: str = _QUERY, mode: str = "quote", **extra: object
) -> httpx.Response:
    payload: dict[str, object] = {"question": question, "answer_mode": mode, **extra}
    return await client.post("/api/v1/queries", json=payload)


async def _wait_terminal(
    client: httpx.AsyncClient, query_id: UUID, timeout_seconds: float = 10.0
) -> QueryState:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, object] = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/v1/queries/{query_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"succeeded", "abstained", "failed", "cancelled"}:
            return QueryState.model_validate(last)
        await asyncio.sleep(0.05)
    raise AssertionError(f"consulta não concluíu no tempo: {last}")


async def test_quote_flow_succeeds(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    app, _build, client_for = api
    await _seed(app.state.deps.db, app.state.deps.store)
    client = await client_for(app)
    async with client:
        response = await _post_query(client, mode="quote", depth="standard")
        assert response.status_code == 202
        query_id = UUID(response.json()["query_id"])
        state = await _wait_terminal(client, query_id)
        assert state.status is QueryStatus.SUCCEEDED
        assert state.mode is AnswerMode.QUOTE
        assert state.strategy in {s.value for s in SearchStrategy}
        assert state.intent is not None
        assert isinstance(state.result, QuoteResponse)
        assert state.result.evidences, "modo quote deve devolver trechos"


async def test_dissertative_flow_succeeds(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    app, _build, client_for = api
    await _seed(app.state.deps.db, app.state.deps.store)
    client = await client_for(app)
    async with client:
        response = await _post_query(client, mode="dissertative", depth="standard")
        assert response.status_code == 202
        query_id = UUID(response.json()["query_id"])
        state = await _wait_terminal(client, query_id)
        assert state.status is QueryStatus.SUCCEEDED
        assert state.mode is AnswerMode.DISSERTATIVE
        assert isinstance(state.result, GeneratedAnswer)
        assert state.result.answer_markdown
        assert state.verification is not None
        assert state.verification.total_claims >= 0
        assert state.verification.action.value == "accepted"


async def test_abstention_when_generator_abstains(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    _app, build, client_for = api
    generator = FakeGeneratorProvider(answer_factory=lambda req: abstention_answer())
    custom_app = await build(generator_provider=generator)
    await _seed(custom_app.state.deps.db, custom_app.state.deps.store)
    client = await client_for(custom_app)
    async with client:
        response = await _post_query(client, mode="dissertative")
        query_id = UUID(response.json()["query_id"])
        state = await _wait_terminal(client, query_id)
        assert state.status is QueryStatus.ABSTAINED
        assert isinstance(state.result, GeneratedAnswer)
        assert state.result.abstained is True
        assert state.result.abstention_reason


async def test_rate_limit_returns_429_with_retry_after(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    _app, build, client_for = api
    limited_app = await build(rate_limit_per_minute=2)
    client = await client_for(limited_app)
    async with client:
        assert (await client.get("/api/v1/works")).status_code == 200
        assert (await client.get("/api/v1/works")).status_code == 200
        response = await client.get("/api/v1/works")
        assert response.status_code == 429
        assert response.headers["retry-after"]
        # Revisão T14-01: a 429 também atravessa request-ID e headers de segurança.
        assert response.headers["x-request-id"]
        assert response.headers["strict-transport-security"]
        assert response.headers["content-security-policy"]
        body = response.json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert body["error"]["request_id"] == response.headers["x-request-id"]


async def test_internal_error_does_not_expose_details(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    _app, build, client_for = api
    leak_marker = "segredo=topsecret /home/user/x.sql"

    def _boom(request: GenerationRequest) -> GeneratedAnswer:
        raise RuntimeError(leak_marker)

    generator = FakeGeneratorProvider(answer_factory=_boom)
    custom_app = await build(generator_provider=generator)
    await _seed(custom_app.state.deps.db, custom_app.state.deps.store)
    client = await client_for(custom_app)
    async with client:
        response = await _post_query(client, mode="dissertative")
        query_id = UUID(response.json()["query_id"])
        state = await _wait_terminal(client, query_id)
        assert state.status is QueryStatus.FAILED
        assert state.error is not None
        assert state.error.code == "INTERNAL_ERROR"
        assert leak_marker not in response.text
        assert "Traceback" not in state.error.message


async def test_failed_query_error_request_id_is_correlatable(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """Revisão T14-02: o erro terminal persistido de uma falha de provedor
    expõe o `request_id` da requisição que iniciou a consulta (correlacionável
    ao log), não um ID vazio."""
    _app, build, client_for = api

    def _boom(request: GenerationRequest) -> GeneratedAnswer:
        raise RuntimeError("falha de provedor de modelo")

    generator = FakeGeneratorProvider(answer_factory=_boom)
    custom_app = await build(generator_provider=generator)
    await _seed(custom_app.state.deps.db, custom_app.state.deps.store)
    client = await client_for(custom_app)
    async with client:
        created = await client.post(
            "/api/v1/queries", json={"question": _QUERY, "answer_mode": "dissertative"}
        )
        assert created.status_code == 202
        post_request_id = created.headers["x-request-id"]
        assert post_request_id
        query_id = UUID(created.json()["query_id"])
        state = await _wait_terminal(client, query_id)
        assert state.status is QueryStatus.FAILED
        assert state.error is not None
        assert state.error.request_id
        assert state.error.request_id == post_request_id


async def test_failed_query_sse_terminal_carries_request_id(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """Revisão T14-02: o evento SSE terminal de uma falha também carrega o
    mesmo `request_id` correlacionável (não vazio)."""
    _app, build, client_for = api

    def _boom(request: GenerationRequest) -> GeneratedAnswer:
        raise RuntimeError("falha de provedor de modelo")

    generator = FakeGeneratorProvider(answer_factory=_boom)
    custom_app = await build(generator_provider=generator)
    await _seed(custom_app.state.deps.db, custom_app.state.deps.store)
    client = await client_for(custom_app)
    async with client:
        created = await client.post(
            "/api/v1/queries", json={"question": _QUERY, "answer_mode": "dissertative"}
        )
        assert created.status_code == 202
        post_request_id = created.headers["x-request-id"]
        query_id = UUID(created.json()["query_id"])
        await _wait_terminal(client, query_id)  # o terminal já está publicado
        data_events: list[str] = []
        async with client.stream("GET", f"/api/v1/queries/{query_id}/events") as stream:
            assert stream.status_code == 200
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    data_events.append(line.removeprefix("data: "))
                if data_events:
                    break
        assert data_events
        terminal = json.loads(data_events[0])
        assert terminal["status"] == "failed"
        assert terminal["error"]["request_id"] == post_request_id


async def test_cancel_interrupts_work(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    _app, build, client_for = api
    enter = asyncio.Event()
    release = asyncio.Event()
    custom_app = await build(embedding_provider=BlockingEmbeddingProvider(enter, release))
    await _seed(custom_app.state.deps.db, custom_app.state.deps.store)
    client = await client_for(custom_app)
    async with client:
        response = await _post_query(client, mode="quote")
        query_id = UUID(response.json()["query_id"])
        await asyncio.wait_for(enter.wait(), timeout=5.0)
        cancel_response = await client.post(f"/api/v1/queries/{query_id}/cancel")
        assert cancel_response.status_code == 202
        release.set()
        state = await _wait_terminal(client, query_id)
        assert state.status is QueryStatus.CANCELLED


async def test_query_with_unknown_session_returns_404(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    app, _build, client_for = api
    client = await client_for(app)
    async with client:
        response = await _post_query(client, session_id=str(uuid4()))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_sessions_crud(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    app, _build, client_for = api
    client = await client_for(app)
    async with client:
        created = await client.post("/api/v1/sessions")
        assert created.status_code == 201
        session_id = UUID(created.json()["session_id"])
        fetched = await client.get(f"/api/v1/sessions/{session_id}")
        assert fetched.status_code == 200
        assert fetched.json()["session_id"] == str(session_id)
        deleted = await client.delete(f"/api/v1/sessions/{session_id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/v1/sessions/{session_id}")).status_code == 404


async def test_validation_and_unknown_work(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    app, _build, client_for = api
    client = await client_for(app)
    async with client:
        invalid = await client.post("/api/v1/queries", json={"question": ""})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
        bad_uuid = await client.get("/api/v1/works/not-a-uuid")
        assert bad_uuid.status_code == 422
        unknown = await client.get("/api/v1/works/00000000-0000-0000-0000-000000000000")
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "NOT_FOUND"


async def test_source_range_requests(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    app, _build, client_for = api
    corpus = await _seed(app.state.deps.db, app.state.deps.store)
    client = await client_for(app)
    async with client:
        full = await client.get(f"/api/v1/editions/{corpus.edition_a}/source")
        assert full.status_code == 200
        assert full.headers["accept-ranges"] == "bytes"
        assert full.headers["content-type"] == "application/pdf"
        expected = await asyncio.to_thread(
            app.state.deps.store.verify_integrity, corpus.source_sha256
        )
        assert len(full.content) == expected.size_bytes

        partial = await client.get(
            f"/api/v1/editions/{corpus.edition_a}/source", headers={"Range": "bytes=0-4"}
        )
        assert partial.status_code == 206
        assert partial.content == full.content[:5]
        assert partial.headers["content-range"] == f"bytes 0-4/{expected.size_bytes}"

        suffix = await client.get(
            f"/api/v1/editions/{corpus.edition_a}/source", headers={"Range": "bytes=-4"}
        )
        assert suffix.status_code == 206
        assert suffix.content == full.content[-4:]

        invalid = await client.get(
            f"/api/v1/editions/{corpus.edition_a}/source",
            headers={"Range": f"bytes={expected.size_bytes}-{expected.size_bytes + 10}"},
        )
        assert invalid.status_code == 416


async def test_sse_stream_delivers_terminal_result(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """AC-18: `GET /queries/{id}/events` entrega o evento terminal `result`.

    Nota sobre o transporte: com `httpx.ASGITransport`, `client.stream()` fica
    disponível só quando a aplicação conclúu o corpo — a rota SSE encerra
    sempre após publicar o terminal (subscriptor ativo ou replay de terminal),
    então o corpo recebido contém o evento `result`. Os eventos de estágio
    (`status`) são cobertos a nível do broker em `test_api_events.py`.
    """
    app, _build, client_for = api
    await _seed(app.state.deps.db, app.state.deps.store)
    client = await client_for(app)
    async with client:
        response = await _post_query(client, mode="quote")
        assert response.status_code == 202
        query_id = UUID(response.json()["query_id"])
        data_events: list[dict[str, object]] = []
        async with client.stream("GET", f"/api/v1/queries/{query_id}/events") as stream:
            assert stream.status_code == 200
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    data_events.append(json.loads(line.removeprefix("data: ")))
        assert data_events
        # A rota encerra o stream DEPOIS de publicar o terminal: o último evento
        # `data:` é sempre o `result` terminal (subscrição ativa ou replay).
        terminal = data_events[-1]
        assert terminal["query_id"] == str(query_id)
        assert terminal["status"] == "succeeded"


async def test_readiness_and_liveness(api: tuple[FastAPI, BuildApp, ClientFor]) -> None:
    app, _build, client_for = api
    client = await client_for(app)
    async with client:
        live = await client.get("/api/v1/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        ready = await client.get("/api/v1/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}
