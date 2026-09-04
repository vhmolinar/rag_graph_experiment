"""AnswerRun: rastreabilidade completa de cada execução (SPEC §5.1, AC-15)."""

from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from rag.domain.answer import GeneratedAnswer, QuoteResponse, VerificationResult
from rag.domain.enums import QueryStatus
from rag.domain.errors import ErrorCode, InvalidTransitionError
from rag.domain.query import EditionFilter, QueryPlan
from rag.domain.retrieval import ExpansionResult, HierarchicalHit
from rag.domain.retrieval import RankedCandidate as RankedCandidate
from rag.domain.versions import utcnow


class StageLatency(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: str = Field(min_length=1, max_length=100)
    duration_ms: float = Field(ge=0.0)


class VersionSet(BaseModel):
    """Todas as versões necessárias para reproduzir uma execução (AC-15).

    Frozen com coleções imutáveis (RR02): uma vez registrado, um campo de
    versão não pode ser alterado por transições (ver `AnswerRun.transition`).
    """

    model_config = ConfigDict(frozen=True)

    extraction_version_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    chunking_version_id: UUID | None = None
    embedding_version_id: UUID | None = None
    embedding_endpoint_version_id: UUID | None = None
    reranker_endpoint_version_id: UUID | None = None
    generator_endpoint_version_id: UUID | None = None
    prompt_version_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    retrieval_policy_version_id: UUID | None = None
    # R03 (B02): política de expansão (`expanded`) — orçamento total por
    # profundidade que governou a recuperação (AC-15).
    expansion_policy_version_id: UUID | None = None
    # R04 (B03): política do estágio hierárquico — orçamento de nós relevantes
    # e tetos de passagens descendentes que governou a recuperação (AC-15).
    hierarchical_policy_version_id: UUID | None = None


_TERMINAL: frozenset[QueryStatus] = frozenset(
    {QueryStatus.SUCCEEDED, QueryStatus.ABSTAINED, QueryStatus.FAILED, QueryStatus.CANCELLED}
)

_ALLOWED_TRANSITIONS: dict[QueryStatus, frozenset[QueryStatus]] = {
    QueryStatus.QUEUED: frozenset({QueryStatus.RUNNING, QueryStatus.FAILED, QueryStatus.CANCELLED}),
    # RUNNING->RUNNING: atualização de progresso entre estágios (append-only).
    QueryStatus.RUNNING: frozenset(
        {
            QueryStatus.RUNNING,
            QueryStatus.SUCCEEDED,
            QueryStatus.ABSTAINED,
            QueryStatus.FAILED,
            QueryStatus.CANCELLED,
        }
    ),
    QueryStatus.SUCCEEDED: frozenset(),
    QueryStatus.ABSTAINED: frozenset(),
    QueryStatus.FAILED: frozenset(),
    QueryStatus.CANCELLED: frozenset(),
}

# RR02: únicos campos mutáveis durante a execução. Identidade (id, created_at),
# pergunta (original/anonymized) e filtros explícitos NUNCA mudam via transição.
_ALLOWED_CHANGE_FIELDS: frozenset[str] = frozenset(
    {
        "session_id",
        "rewritten_query",
        "inferred_filters",
        "plan",
        "candidates",
        "expansions",
        "hierarchical_hits",
        "selected_evidence_ids",
        "response",
        "verification",
        "versions",
        "latencies",
        "error_code",
        "error_message",
    }
)
_APPEND_ONLY_FIELDS: tuple[str, ...] = (
    "candidates",
    "latencies",
    "expansions",
    "hierarchical_hits",
)


class AnswerRun(BaseModel):
    """Registro imutável de uma execução de consulta.

    Frozen: mudanças de estado só via ``transition``, que revalida o modelo
    inteiro (R05 — ``model_copy(update=...)`` não executa validators).
    """

    model_config = ConfigDict(frozen=True)

    question_original: str = Field(min_length=1)
    question_anonymized: str = Field(min_length=1)
    explicit_filters: EditionFilter
    status: QueryStatus = QueryStatus.QUEUED
    session_id: UUID | None = None
    rewritten_query: str | None = None
    inferred_filters: EditionFilter | None = None
    plan: QueryPlan | None = None
    candidates: tuple[RankedCandidate, ...] = Field(default_factory=tuple)
    # R03 (B02): consultas de expansão executadas na estratégia `expanded`,
    # com scores e posições por expansão — rastreabilidade do ranking por
    # expansão (AC-15). Vazio nas demais estratégias.
    expansions: tuple[ExpansionResult, ...] = Field(default_factory=tuple)
    # R04 (B03): auditoria do estágio hierárquico — qual síntese/conceito
    # localizou qual passagem original (AC-12/AC-15). Vazio quando o plano não
    # marca `needs_hierarchical`.
    hierarchical_hits: tuple[HierarchicalHit, ...] = Field(default_factory=tuple)
    selected_evidence_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    response: GeneratedAnswer | QuoteResponse | None = None
    verification: VerificationResult | None = None
    versions: VersionSet = Field(default_factory=VersionSet)
    latencies: tuple[StageLatency, ...] = Field(default_factory=tuple)
    error_code: ErrorCode | None = None
    error_message: str | None = Field(
        default=None,
        max_length=2000,
        description="Mensagem segura para o cliente do falha (T14); vazio/None em sucesso.",
    )
    # Correlação com o log da requisição HTTP que iniciou a execução (T14,
    # SPEC §10.1): setado na criação; permite ligar o erro terminal persistido
    # (GET /queries e SSE) ao `X-Request-ID` do POST original.
    request_id: str = Field(default="", max_length=64)
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)
    # R4-01: revisão persistida lida no carregamento; incrementada pelo
    # repository a cada save() (compare-and-swap). Nunca muda via transition().
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _terminal_state_coherent(self) -> Self:
        if self.status is QueryStatus.FAILED and self.error_code is None:
            raise ValueError("execução FAILED exige error_code")
        if self.status is QueryStatus.SUCCEEDED and self.response is None:
            raise ValueError("execução SUCCEEDED exige response")
        if self.status is QueryStatus.ABSTAINED and (
            not isinstance(self.response, GeneratedAnswer) or not self.response.abstained
        ):
            raise ValueError("status ABSTAINED exige GeneratedAnswer com abstained=true")
        return self

    def transition(self, status: QueryStatus, **changes: object) -> "AnswerRun":
        """Transiciona o status aplicando `changes`, com revalidação completa.

        RR02: apenas campos da allowlist de progresso podem mudar; identidade,
        pergunta, filtros explícitos e criação são imutáveis por construção.
        `candidates` e `latencies` são append-only; campos de `versions` já
        registrados não podem ser alterados. O resultado é sempre revalidado.
        """
        allowed = _ALLOWED_TRANSITIONS[self.status]
        if status not in allowed:
            raise InvalidTransitionError(
                f"Transição {self.status} -> {status} não permitida.",
                context={"from": str(self.status), "to": str(status)},
            )
        unknown = set(changes) - _ALLOWED_CHANGE_FIELDS
        if unknown:
            raise InvalidTransitionError(
                f"Campos não permitidos em transição: {sorted(unknown)}",
                context={"fields": sorted(unknown)},
            )
        for field in _APPEND_ONLY_FIELDS:
            if field in changes:
                current = getattr(self, field)
                incoming: tuple[object, ...] = tuple(changes[field])  # type: ignore[arg-type]
                if incoming[: len(current)] != current:
                    raise InvalidTransitionError(
                        f"Campo '{field}' é append-only.",
                        context={"field": field},
                    )
        if "versions" in changes:
            incoming_versions = changes["versions"]
            if not isinstance(incoming_versions, VersionSet):
                raise InvalidTransitionError("'versions' deve ser um VersionSet.")
            for name in VersionSet.model_fields:
                current_value = getattr(self.versions, name)
                already_set = current_value is not None and current_value != ()
                if already_set and getattr(incoming_versions, name) != current_value:
                    raise InvalidTransitionError(
                        f"Versão já registrada não pode ser alterada: {name}",
                        context={"field": name},
                    )
        data = self.model_dump(mode="python")
        data.update(changes)
        data["status"] = status
        return AnswerRun.model_validate(data)
