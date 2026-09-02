"""Contratos de request/response da API (T14; SPEC §10).

Request/response tipados com Pydantic. Os modelos de resultado (quote e
dissertativo), filtros e explicación de estratégia são os modelos do domínio
(reuso — fonte única de verdade); aqui ficam só os envelopes da API.

`QueryState` é o contrato do `GET /queries/{id}` e do evento SSE terminal
(`result`). `mode` é derivado do tipo da resposta (NOTES.md §10.15 item 2):
`QuoteResponse` → `quote`; `GeneratedAnswer` → `dissertative`.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rag.api.errors import ErrorOut, safe_message
from rag.domain.answer import GeneratedAnswer, QuoteResponse, VerificationResult
from rag.domain.enums import AnswerMode, Intent, QueryStatus, SearchStrategy
from rag.domain.errors import ErrorCode
from rag.domain.query import EditionFilter, StrategyExplanation
from rag.domain.runs import AnswerRun


class QueryAccepted(BaseModel):
    """Resposta de `POST /queries` (202 + `query_id`; execução em tarefa)."""

    model_config = ConfigDict(frozen=True)

    query_id: UUID
    status: Literal["queued"]


class QueryCancelled(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_id: UUID
    status: Literal["cancelling"]


class QueryState(BaseModel):
    """Estado completo de uma consulta (progresso e resultado)."""

    model_config = ConfigDict(frozen=True)

    query_id: UUID
    status: QueryStatus
    mode: AnswerMode | None = None
    question: str = Field(min_length=1)
    intent: Intent | None = None
    strategy: SearchStrategy | None = None
    strategy_explanation: StrategyExplanation | None = None
    inferred_filters: EditionFilter | None = None
    result: QuoteResponse | GeneratedAnswer | None = None
    verification: VerificationResult | None = None
    error: ErrorOut | None = None
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: UUID
    created_at: datetime
    last_activity_at: datetime


class ContributorOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    role: str


class EditionOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    edition_id: UUID
    work_id: UUID
    title: str
    source_type: str
    publisher: str | None = None
    publication_year: int | None = None
    edition_label: str | None = None
    license_status: str
    ingestion_status: str


class WorkSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_id: UUID
    canonical_title: str
    original_title: str | None = None
    authors: list[ContributorOut] = Field(default_factory=list)
    language: str


class WorkDetail(WorkSummary):
    editions: list[EditionOut] = Field(default_factory=list)


class EditionDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    edition: EditionOut
    work_title: str | None = None


class PassageDetail(BaseModel):
    """Metadados citáveis de uma passagem (AC-03: abrir a origem e destaca)."""

    model_config = ConfigDict(frozen=True)

    passage_id: UUID
    edition_id: UUID
    work_id: UUID
    text: str
    section_path: list[str] = Field(default_factory=list)
    physical_page: int | None = None
    printed_label: str | None = None
    char_start: int | None = None
    char_end: int | None = None


def mode_of(response: QuoteResponse | GeneratedAnswer | None) -> AnswerMode | None:
    """Deriva o modo do tipo da resposta persistida (NOTES.md §10.15 item 2)."""
    if isinstance(response, QuoteResponse):
        return AnswerMode.QUOTE
    if isinstance(response, GeneratedAnswer):
        return AnswerMode.DISSERTATIVE
    return None


def build_query_state(run: AnswerRun) -> QueryState:
    """Projeta um `AnswerRun` no contrato de `QueryState` (GET e SSE terminal)."""
    plan = run.plan
    error: ErrorOut | None = None
    if run.status is QueryStatus.FAILED:
        code = run.error_code or ErrorCode.INTERNAL_ERROR
        error = ErrorOut(
            code=code.value,
            message=safe_message(code, run.error_message),
            request_id=run.request_id,
        )
    return QueryState(
        query_id=run.id,
        status=run.status,
        mode=mode_of(run.response),
        question=run.question_original,
        intent=plan.intent if plan is not None else None,
        strategy=plan.strategy if plan is not None else None,
        strategy_explanation=plan.strategy_explanation if plan is not None else None,
        inferred_filters=run.inferred_filters,
        result=run.response,
        verification=run.verification,
        error=error,
        created_at=run.created_at,
    )
