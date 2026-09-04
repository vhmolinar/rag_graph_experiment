"""Composição de dependências da API (T14).

`AppDependencies` agrupa banco, store, serviços, políticas e provedores de
modelo. A fábrica `build_deps` construte os adapters HTTP reais a partir das
settings por ambiente; os testes injetan doubles pelos parâmetros da fábrica
`create_app` (mesmo padrão da fábrica do CLI).

Políticas de profundidade usam os `defaults()` do domínio (T09/T12/T13); a
calibração fica para o benchmark de T19 (NOTES.md §4).
"""

from dataclasses import dataclass

from rag.adapters.embedding_adapter import (
    EmbeddingEndpointSettings,
    OpenAiCompatibleEmbeddingProvider,
)
from rag.adapters.generation_adapter import (
    GenerationEndpointSettings,
    OpenAiCompatibleGeneratorProvider,
)
from rag.adapters.planner_adapter import (
    OpenAiCompatiblePlannerProvider,
    PlannerEndpointSettings,
)
from rag.adapters.reranker_adapter import HttpRerankerProvider, RerankerEndpointSettings
from rag.adapters.verifier_adapter import (
    OpenAiCompatibleVerifierProvider,
    VerifierEndpointSettings,
)
from rag.api.events import EventBroker
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
from rag.domain.retrieval import (
    ExpansionPolicy,
    HierarchicalPolicy,
    RetrievalPolicy,
)
from rag.domain.verification import VerificationPolicy
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.config import DatabaseSettings, StorageSettings
from rag.infrastructure.db import Database


@dataclass(frozen=True)
class AppDependencies:
    settings: ApiSettings
    db: Database
    store: ArtifactStore
    planner: PlannerService
    retrieval: RetrievalService
    context: ContextService
    dissertative: DissertativeService
    retrieval_policy: RetrievalPolicy
    expansion_policy: ExpansionPolicy
    hierarchical_policy: HierarchicalPolicy
    context_policy: ContextPolicy
    verification_policy: VerificationPolicy
    broker: EventBroker
    registry: QueryRegistry
    generator_model_name: str


def _default_database() -> Database:
    return Database(DatabaseSettings())


def _default_store() -> ArtifactStore:
    storage = StorageSettings()
    return ArtifactStore(storage.root, max_size_bytes=storage.max_size_bytes)


def _default_embedding_provider() -> EmbeddingProvider:
    return OpenAiCompatibleEmbeddingProvider(EmbeddingEndpointSettings())


def _default_reranker_provider() -> RerankerProvider:
    return HttpRerankerProvider(RerankerEndpointSettings())


def _default_generator_provider() -> GeneratorProvider:
    return OpenAiCompatibleGeneratorProvider(GenerationEndpointSettings())


def _default_verifier_provider() -> VerifierProvider:
    return OpenAiCompatibleVerifierProvider(VerifierEndpointSettings())


def _default_planner_provider() -> PlannerProvider | None:
    """Provedor de planejamento opcional (estratégia `expanded`).

    Sem settings explícitos (endereço/paridade) o provedor é `None` e o
    planejador determinístico continua funcionando sem expansão (NOTES.md
    §10.15 item 9)."""
    settings = PlannerEndpointSettings()
    if not settings.base_url:
        return None
    return OpenAiCompatiblePlannerProvider(settings)
