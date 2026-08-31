"""Fábrica da aplicação FastAPI (T14; SPEC §10, §14, AC-18).

`create_app()` construte a aplicação com injeção de dependências: produção
usa os defaults por ambiente (adapters HTTP reais, banco por `POSTGRES_*`,
store por `ARTIFACT_*`); os testes injetan doubles pelos parâmetros.

Uso (CLI `rag serve`):
    uvicorn "rag.api.app:create_app" --factory --host 127.0.0.1 --port 8000
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from rag.adapters.generation_adapter import GenerationEndpointSettings
from rag.api.deps import (
    AppDependencies,
    _default_database,
    _default_embedding_provider,
    _default_generator_provider,
    _default_planner_provider,
    _default_reranker_provider,
    _default_store,
    _default_verifier_provider,
)
from rag.api.errors import install_exception_handlers
from rag.api.events import EventBroker
from rag.api.logging import configure_logging
from rag.api.routes import catalog, health, queries, sessions
from rag.api.security import install_security
from rag.api.settings import ApiSettings
from rag.api.tasks import QueryRegistry
from rag.application.context import ContextService
from rag.application.dissertative import DissertativeService
from rag.application.planning import PlannerService
from rag.application.search import RetrievalService
from rag.domain.context import ContextPolicy
from rag.domain.providers import (
    EmbeddingProvider,
    GeneratorProvider,
    PlannerProvider,
    RerankerProvider,
    VerifierProvider,
)
from rag.domain.retrieval import RetrievalPolicy
from rag.domain.verification import VerificationPolicy
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.db import Database

_LOGGER = structlog.get_logger(__name__)

_API_TITLE = "RAG de livros — API (fase 1)"
_API_VERSION = "0.1.0"


def create_app(
    *,
    db: Database | None = None,
    store: ArtifactStore | None = None,
    settings: ApiSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    reranker_provider: RerankerProvider | None = None,
    generator_provider: GeneratorProvider | None = None,
    verifier_provider: VerifierProvider | None = None,
    planner_provider: PlannerProvider | None = None,
    retrieval_policy: RetrievalPolicy | None = None,
    context_policy: ContextPolicy | None = None,
    verification_policy: VerificationPolicy | None = None,
    generator_model_name: str | None = None,
    now: Callable[[], float] | None = None,
) -> FastAPI:
    configure_logging()
    settings = settings or ApiSettings()
    db = db or _default_database()
    store = store or _default_store()
    embedding = embedding_provider or _default_embedding_provider()
    reranker = reranker_provider or _default_reranker_provider()
    generator = generator_provider or _default_generator_provider()
    verifier = verifier_provider or _default_verifier_provider()
    planner = planner_provider if planner_provider is not None else _default_planner_provider()
    if generator_model_name is None:
        generator_model_name = GenerationEndpointSettings().model

    deps = AppDependencies(
        settings=settings,
        db=db,
        store=store,
        planner=PlannerService(planner),
        retrieval=RetrievalService(embedding, reranker),
        context=ContextService(),
        dissertative=DissertativeService(generator, verifier),
        retrieval_policy=retrieval_policy or RetrievalPolicy.defaults(),
        context_policy=context_policy or ContextPolicy.defaults(),
        verification_policy=verification_policy or VerificationPolicy.defaults(),
        broker=EventBroker(),
        registry=QueryRegistry(),
        generator_model_name=generator_model_name,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await deps.db.open()
        try:
            yield
        finally:
            await deps.registry.shutdown()
            await deps.db.close()

    app = FastAPI(
        title=_API_TITLE,
        version=_API_VERSION,
        docs_url="/api/v1/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.deps = deps

    for router in (catalog.router, health.router, sessions.router, queries.router):
        app.include_router(router, prefix="/api/v1")

    install_exception_handlers(app)
    install_security(app, settings, now=now)
    _LOGGER.info("api.started", title=_API_TITLE, version=_API_VERSION)
    return app
