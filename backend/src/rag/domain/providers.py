"""Contratos dos provedores de modelos (SPEC §11).

O domínio define Protocols; adapters HTTP concretos vivem em `rag.adapters`.
Nenhum tipo de SDK de modelo atravessa estas fronteiras.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.answer import Claim, EvidenceRef, GeneratedAnswer
from rag.domain.enums import Depth, SummaryScope
from rag.domain.query import MAX_SUBQUESTIONS
from rag.domain.versions import EmbeddingVersion

MAX_PLANNED_ALIASES = 50
MAX_PLANNED_CONCEPTS = 50


class GenerationRequest(BaseModel):
    """Blocos separados do prompt dissertativo (SPEC §9.3)."""

    model_config = ConfigDict(frozen=True)

    system_policy: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    question: str = Field(min_length=1)
    scope_description: str = Field(min_length=1)
    evidences: list[EvidenceRef] = Field(min_length=1)
    depth: Depth
    session_context: str | None = None
    prompt_version_id: UUID | None = None
    verification_feedback: str | None = Field(
        default=None,
        max_length=20_000,
        description="Retorno do verificador para regeneração (T13).",
    )


class PlanningRequest(BaseModel):
    """Pedido ao provedor de planejamento (SPEC §8.2, T10).

    A fase de planejamento não tem evidências — por isso o contrato é separado
    de `GenerationRequest` (NOTES.md §10.11 item 4).
    """

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, max_length=4000)
    depth: Depth
    prompt_version_id: UUID | None = None


class PlannedQuery(BaseModel):
    """Sugestão do provedor de planejamento para enriquecer o plano.

    Nunca decide intenção/estratégia por sí só — o planejador integra a
    sugestão dentro do plano determinístico. `subquestions` é limitada
    (`MAX_SUBQUESTIONS`); `aliases`/`concept_labels` também têm limite, validado
    aqui (falha fechada se o provedor violar o contrato).
    """

    model_config = ConfigDict(frozen=True)

    semantic_query: str | None = Field(default=None, min_length=1, max_length=4000)
    subquestions: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_SUBQUESTIONS)
    aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_PLANNED_ALIASES)
    concept_labels: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_PLANNED_CONCEPTS)


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def embedding_version(self) -> EmbeddingVersion: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class RerankerProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


@runtime_checkable
class GeneratorProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GeneratedAnswer: ...


@runtime_checkable
class PlannerProvider(Protocol):
    """Geração limitada de subperguntas/aliases no planejamento (SPEC §8.2).

    Só enriquece o plano determinístico (NOTES.md §10.11 item 3/4); nunca
    decide intenção ou estratégia.
    """

    async def plan(self, request: PlanningRequest) -> PlannedQuery: ...


class PassageRef(BaseModel):
    """Referência a passagem com texto citável para sínteses e conceitos.

    Mais leve que `EvidenceRef` (que carrega score/rank/work_id — conceitos da
    resposta, não da extração). `section_path` ajuda o provedor a entender o
    contexto hierárquico; nunca é texto citável.
    """

    model_config = ConfigDict(frozen=True)

    passage_id: UUID
    text: str = Field(min_length=1, max_length=5_000_000)
    section_path: tuple[str, ...] = Field(default_factory=tuple)


class SummaryRequest(BaseModel):
    """Pedido ao provedor de síntese hierárquica (SPEC §7.4, T11).

    Blocos separados (mesma convenção de `GenerationRequest`): política e
    contrato de saída imutáveis, escopo descritivo e as passagens-filho do
    escopo. Sem profundidade — as políticas de §9.1 são para respostas
    dissertativas (NOTES.md §10.12 item 10).
    """

    model_config = ConfigDict(frozen=True)

    system_policy: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    scope_type: SummaryScope
    scope_description: str = Field(min_length=1)
    passages: tuple[PassageRef, ...] = Field(min_length=1)
    prompt_version_id: UUID | None = None


class SummaryResult(BaseModel):
    """Síntese + passagens de suporte.

    `supporting_passage_ids` PODE ser vazio: o provedor pode julgar que
    nenhuma passagem sustenta a síntese (SPEC §7.4) — é o SERVIÇO que decide
    a publicação (NOTES.md §10.12 item 2). O `Summary` do domínio, por
    contrast, exige ao menos uma passagem: um item publicado SEMPRE tem
    suporte (AC-12).
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    supporting_passage_ids: tuple[UUID, ...] = Field(default_factory=tuple)


class ConceptExtractRequest(BaseModel):
    """Pedido ao provedor de extração de conceitos (SPEC §7.4, T11)."""

    model_config = ConfigDict(frozen=True)

    system_policy: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    scope_description: str = Field(min_length=1)
    passages: tuple[PassageRef, ...] = Field(min_length=1)
    prompt_version_id: UUID | None = None


class ExtractedConcept(BaseModel):
    """Conceito proposto pelo provedor, ligado às passagens de suporte.

    `supporting_passage_ids` vazio = o provedor não identificou suporte —
    o serviço não publica o item (SPEC §7.4).
    """

    model_config = ConfigDict(frozen=True)

    normalized_label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    supporting_passage_ids: tuple[UUID, ...] = Field(default_factory=tuple)


class ExtractedConcepts(BaseModel):
    model_config = ConfigDict(frozen=True)

    concepts: tuple[ExtractedConcept, ...] = Field(default_factory=tuple, max_length=500)


@runtime_checkable
class EnrichmentProvider(Protocol):
    """Sínteses hierárquicas e conceitos (SPEC §7.4, T11).

    Um provedor, duas operações sobre o mesmo endpoint compatível com OpenAI
    (`/chat/completions`, JSON mode). Nunca devolve prosa fora do contrato:
    síntese sem suporte é um resultado válido (item não publicado pelo
    serviço), mas suportes fora do escopo são violação de contrato.
    """

    async def summarize(self, request: SummaryRequest) -> SummaryResult: ...
    async def extract_concepts(self, request: ConceptExtractRequest) -> ExtractedConcepts: ...


class VerificationRequest(BaseModel):
    """Pedido ao provedor de verificação (SPEC §9.4, T13).

    Blocos separados (mesma convenção de `GenerationRequest`): política e
    contrato de saída imutáveis, pergunta, as afirmações a julgar e as
    evidências montadas (T12). O provedor julga CADA par (afirmação,
    evidência) — nunca introduz novas afirmações.
    """

    model_config = ConfigDict(frozen=True)

    system_policy: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    question: str = Field(min_length=1)
    claims: tuple[Claim, ...] = Field(min_length=1)
    evidences: tuple[EvidenceRef, ...] = Field(min_length=1)
    prompt_version_id: UUID | None = None


class ClaimVerdict(BaseModel):
    """Veredicto de suporte de um par (afirmação, evidência) — SPEC §9.4.

    T13-FULL-02: a saída do verificador fica reduzida a IDs, flags e códigos —
    NUNCA texto livre. Descrições de contradição são renderizadas pelo domínio/
    serviço com texto fixo e não factual.
    """

    model_config = ConfigDict(frozen=True)

    claim_id: str = Field(min_length=1, max_length=64)
    evidence_id: UUID
    supported: bool
    contradiction: bool = False


class VerificationVerdict(BaseModel):
    """Conjunto de veredictos do provedor. Nunca introduz conteúdo novo."""

    model_config = ConfigDict(frozen=True)

    verdicts: tuple[ClaimVerdict, ...] = Field(default_factory=tuple)


@runtime_checkable
class VerifierProvider(Protocol):
    """Verificação semântica de afirmações (SPEC §9.4, T13).

    Julga se cada evidência citada realmente sustenta a afirmação e identifica
    contradições entre resposta e fonte. A existência dos IDs é
    responsabilidade do serviço, não deste provedor (NOTES.md §10.14 item 2).
    """

    async def verify(self, request: VerificationRequest) -> VerificationVerdict: ...
