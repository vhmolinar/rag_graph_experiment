"""Contexto de sessão e reescrita de follow-up contra PostgreSQL real (T15, AC-13).

Cobre os entregáveis de T15:

- reescrita de follow-up em pergunta autônoma registrada e inspeccionável
  ("compare isso com o segundo autor" → "compare O Ensaio da Memória com Bruno
  Silva") na `answer_runs.rewritten_query` e `session_entries`;
- contexto de OUTRA sessão nunca é usado (isolamento por `session_id`);
- histórico limitado: o limite configura a janela de rodadas usadas na
  reescrita (`SESSION_HISTORY_LIMIT`);
- exclusão da sessão remove o histórico (`session_entries` cai em CASCADE);
- pergunta sem anáfora não é reescrita; sem sessão não há rodada.

Requer Docker (PostgreSQL via testcontainers); sem Docker os testes são
saltados pelo marker `integration`.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import (
    ConceptEmbeddingProvider,
    FakeGeneratorProvider,
    FakePlannerProvider,
    FakeRerankerProvider,
    FakeVerifierProvider,
)

from rag.api.app import create_app
from rag.api.schemas import QueryState
from rag.api.settings import ApiSettings
from rag.domain.enums import ContributorRole, QueryStatus, SourceType
from rag.domain.library import Contributor, Edition, Page, Passage, Section, Work
from rag.domain.versions import ChunkingVersion, EmbeddingVersion, utcnow
from rag.infrastructure.config import DatabaseSettings
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.content import PagesRepository, SectionsRepository
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.sessions import SessionsRepository
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

BuildApp = Callable[..., Awaitable[FastAPI]]
ClientFor = Callable[[FastAPI], Awaitable[httpx.AsyncClient]]


async def _seed(db: Database) -> None:
    """Semilla corpus mínimo: duas obras com autores e uma passagem cada."""
    async with db.connection() as conn:
        work_a = await WorksRepository(conn).create(
            Work(
                canonical_title="O Ensaio da Liberdade",
                authors=[Contributor(name="Ana Pereira", role=ContributorRole.AUTHOR, ordinal=0)],
            )
        )
        work_b = await WorksRepository(conn).create(
            Work(
                canonical_title="O Ensaio da Memória",
                authors=[Contributor(name="Bruno Silva", role=ContributorRole.AUTHOR, ordinal=0)],
            )
        )
        edition_a = await EditionsRepository(conn).create(
            Edition(
                work_id=work_a.id,
                title="O Ensaio da Liberdade (edição A)",
                source_type=SourceType.PDF_TEXT,
                source_sha256="1" * 64,
            )
        )
        edition_b = await EditionsRepository(conn).create(
            Edition(
                work_id=work_b.id,
                title="O Ensaio da Memória (edição B)",
                source_type=SourceType.PDF_TEXT,
                source_sha256="2" * 64,
            )
        )
        section_a = Section(
            edition_id=edition_a.id, level=0, ordinal=0, path=["Capítulo Único"], title="Capítulo"
        )
        section_b = Section(
            edition_id=edition_b.id, level=0, ordinal=0, path=["Capítulo Único"], title="Capítulo"
        )
        await SectionsRepository(conn).create_many([section_a, section_b])
        page_a = Page.create(
            edition_id=edition_a.id,
            physical_index=0,
            text="spleen sufoca a liberdade de Ana.",
            printed_label="p. 1",
        )
        page_b = Page.create(
            edition_id=edition_b.id,
            physical_index=0,
            text="A memória guarda o spleen de Bruno.",
            printed_label="p. 1",
        )
        await PagesRepository(conn).create_many([page_a, page_b])
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="ctx-chunk", created_at=utcnow())
        )
        embedding_version = await versions.get_or_create(
            EmbeddingVersion(label="ctx-emb", model_name="m", dimensions=1024, created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        await PassagesRepository(conn).create(
            Passage(
                id=UUID("b0000000-0000-0000-0000-00000000000a"),
                edition_id=edition_a.id,
                ordinal=0,
                text=page_a.text,
                token_count=len(page_a.text.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=section_a.id,
                page_start_id=page_a.id,
                page_end_id=page_a.id,
                char_start=0,
                char_end=len(page_a.text),
            ),
            embedding=await provider.embed_query(page_a.text),
        )
        await PassagesRepository(conn).create(
            Passage(
                id=UUID("b0000000-0000-0000-0000-00000000000b"),
                edition_id=edition_b.id,
                ordinal=0,
                text=page_b.text,
                token_count=len(page_b.text.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
                section_id=section_b.id,
                page_start_id=page_b.id,
                page_end_id=page_b.id,
                char_start=0,
                char_end=len(page_b.text),
            ),
            embedding=await provider.embed_query(page_b.text),
        )
        await conn.execute("ANALYZE passages")


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

    async def build(*, session_history_limit: int = 20) -> FastAPI:
        db = await _open_db()
        return create_app(
            db=db,
            settings=ApiSettings(
                cors_allowed_origins="http://localhost:5173",
                rate_limit_per_minute=10000,
                session_history_limit=session_history_limit,
            ),
            embedding_provider=ConceptEmbeddingProvider(),
            reranker_provider=FakeRerankerProvider(),
            generator_provider=FakeGeneratorProvider(),
            verifier_provider=FakeVerifierProvider(),
            # Estratégia `expanded` (conceitual/comparativa) chama o provedor de
            # planejamento: double determinístico, sem rede e sem depender do
            # ambiente (`PLANNER_BASE_URL`).
            planner_provider=FakePlannerProvider(),
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
    client: httpx.AsyncClient, *, question: str, session_id: UUID | None = None
) -> QueryState:
    payload: dict[str, object] = {"question": question, "answer_mode": "quote"}
    if session_id is not None:
        payload["session_id"] = str(session_id)
    response = await client.post("/api/v1/queries", json=payload)
    assert response.status_code == 202
    return await _wait_terminal(client, UUID(response.json()["query_id"]))


async def _wait_terminal(
    client: httpx.AsyncClient, query_id: UUID, timeout_seconds: float = 10.0
) -> QueryState:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, object] = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/v1/queries/{query_id}")
        # A execução é assíncrona: a rodada ainda pode não existir (404).
        if response.status_code == 404:
            await asyncio.sleep(0.05)
            continue
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"succeeded", "abstained", "failed", "cancelled"}:
            return QueryState.model_validate(last)
        await asyncio.sleep(0.05)
    raise AssertionError(f"consulta não concluíu no tempo: {last}")


async def test_follow_up_is_rewritten_and_registered(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """AC-13: "compare isso com o segundo autor" resolve as referências da sessão
    e a pergunta autônoma fica registrada e inspeccionável."""
    app, _build, client_for = api
    await _seed(app.state.deps.db)
    client = await client_for(app)
    async with client:
        created = await client.post("/api/v1/sessions")
        assert created.status_code == 201
        session_id = UUID(created.json()["session_id"])

        await _post_query(
            client,
            question="quem é o autor de O Ensaio da Liberdade?",
            session_id=session_id,
        )
        await _post_query(
            client,
            question="qual é a concepção de spleen em O Ensaio da Memória?",
            session_id=session_id,
        )
        follow_up = await _post_query(
            client, question="compare isso com o segundo autor", session_id=session_id
        )

        assert follow_up.status is QueryStatus.SUCCEEDED
        assert follow_up.rewritten_query == "compare O Ensaio da Memória com Bruno Silva"

        async with app.state.deps.db.connection() as conn:
            entries = await SessionsRepository(conn).list_entries(session_id, limit=10)
            assert len(entries) == 3
            assert entries[0].question_original == "quem é o autor de O Ensaio da Liberdade?"
            assert entries[0].rewritten_query is None
            assert entries[2].question_original == "compare isso com o segundo autor"
            assert entries[2].rewritten_query == "compare O Ensaio da Memória com Bruno Silva"
            assert entries[2].answer_run_id == follow_up.query_id


async def test_context_of_other_session_is_never_used(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """O histórico de uma sessão nunca contamina outra."""
    app, _build, client_for = api
    await _seed(app.state.deps.db)
    client = await client_for(app)
    async with client:
        s1 = await client.post("/api/v1/sessions")
        s2 = await client.post("/api/v1/sessions")
        session_1 = UUID(s1.json()["session_id"])
        session_2 = UUID(s2.json()["session_id"])

        await _post_query(
            client,
            question="quem é o autor de O Ensaio da Liberdade?",
            session_id=session_1,
        )
        await _post_query(
            client,
            question="qual é a concepção de spleen em O Ensaio da Memória?",
            session_id=session_1,
        )

        # Na sessão 2 o mesmo follow-up NÃO resolve "isso"/"o segundo autor":
        # não há histórico próprio — a pergunta autônoma é a pergunta original.
        isolated = await _post_query(
            client, question="compare isso com o segundo autor", session_id=session_2
        )
        assert isolated.rewritten_query is None

        # Na sessão 1 (com histórico) o follow-up SÍ resolve.
        follow_up = await _post_query(
            client, question="compare isso com o segundo autor", session_id=session_1
        )
        assert follow_up.rewritten_query == "compare O Ensaio da Memória com Bruno Silva"


async def test_session_deletion_removes_history(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """Exclusão da sessão remove o histórico conforme a política (CASCADE)."""
    app, _build, client_for = api
    await _seed(app.state.deps.db)
    client = await client_for(app)
    async with client:
        created = await client.post("/api/v1/sessions")
        session_id = UUID(created.json()["session_id"])
        run = await _post_query(
            client,
            question="quem é o autor de O Ensaio da Liberdade?",
            session_id=session_id,
        )

        async with app.state.deps.db.connection() as conn:
            entries = await SessionsRepository(conn).list_entries(session_id, limit=10)
            assert len(entries) == 1

        deleted = await client.delete(f"/api/v1/sessions/{session_id}")
        assert deleted.status_code == 204

        async with app.state.deps.db.connection() as conn:
            remaining = await SessionsRepository(conn).list_entries(session_id, limit=10)
            assert remaining == []
            row = await conn.execute(
                "SELECT session_id FROM answer_runs WHERE id = %s", (run.query_id,)
            )
            result = await row.fetchone()
            assert result is not None
            assert result[0] is None


async def test_history_limit_controls_rewrite_window(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """Histórico limitado: com janela de 1 rodada, "o segundo autor" não resolve."""
    _app, build, client_for = api
    limited_app = await build(session_history_limit=1)
    await _seed(limited_app.state.deps.db)
    client = await client_for(limited_app)
    async with client:
        created = await client.post("/api/v1/sessions")
        session_id = UUID(created.json()["session_id"])
        await _post_query(
            client,
            question="quem é o autor de O Ensaio da Liberdade?",
            session_id=session_id,
        )
        await _post_query(
            client,
            question="qual é a concepção de spleen em O Ensaio da Memória?",
            session_id=session_id,
        )

        follow_up = await _post_query(
            client, question="compare isso com o segundo autor", session_id=session_id
        )
        # Janela = última rodada (obra B): "isso" resolve para a obra mais
        # recente, mas "o segundo autor" fica fora do alcance.
        assert follow_up.rewritten_query == "compare O Ensaio da Memória com o segundo autor"


async def test_question_without_anaphora_is_not_rewritten(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    """Sem anáfora a pergunta autônoma coincide com a original (rewritten_query None)."""
    app, _build, client_for = api
    await _seed(app.state.deps.db)
    client = await client_for(app)
    async with client:
        created = await client.post("/api/v1/sessions")
        session_id = UUID(created.json()["session_id"])
        state = await _post_query(
            client,
            question="qual é a concepção de spleen em O Ensaio da Memória?",
            session_id=session_id,
        )
        assert state.rewritten_query is None
        async with app.state.deps.db.connection() as conn:
            entries = await SessionsRepository(conn).list_entries(session_id, limit=10)
            assert len(entries) == 1
            assert entries[0].rewritten_query is None


async def test_query_without_session_has_no_history(
    api: tuple[FastAPI, BuildApp, ClientFor],
) -> None:
    app, _build, client_for = api
    await _seed(app.state.deps.db)
    client = await client_for(app)
    async with client:
        state = await _post_query(
            client, question="qual é a concepção de spleen em O Ensaio da Memória?"
        )
        assert state.rewritten_query is None
        async with app.state.deps.db.connection() as conn:
            row = await conn.execute("SELECT COUNT(*) AS n FROM session_entries")
            result = await row.fetchone()
            assert result is not None
            assert result[0] == 0
