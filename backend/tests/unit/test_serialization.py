"""Serialização JSON dos contratos e rejeição de valores inválidos (T02)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag.domain.answer import GeneratedAnswer, QuoteResponse
from rag.domain.enums import AnswerMode, SourceType
from rag.domain.library import Edition, Work
from rag.domain.query import EditionFilter, QueryRequest
from rag.domain.runs import AnswerRun


def test_query_request_json_roundtrip() -> None:
    request = QueryRequest(
        question="O que é spleen?",
        answer_mode=AnswerMode.DISSERTATIVE,
        exclude_edition_ids=[uuid4()],
    )
    restored = QueryRequest.model_validate_json(request.model_dump_json())
    assert restored == request


def test_query_request_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"question": "q", "answer_mode": "essay"})


def test_query_request_rejects_bad_uuid() -> None:
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(
            {"question": "q", "answer_mode": "quote", "include_edition_ids": ["not-uuid"]}
        )


def test_work_edition_roundtrip() -> None:
    work = Work(canonical_title="Dom Casmurro")
    edition = Edition(
        work_id=work.id,
        title="Dom Casmurro — edição crítica",
        source_type=SourceType.EPUB,
        source_sha256="d" * 64,
    )
    restored = Edition.model_validate_json(edition.model_dump_json())
    assert restored == edition
    assert restored.source_type is SourceType.EPUB


def test_answer_run_roundtrip_with_response() -> None:
    run = AnswerRun(
        question_original="q",
        question_anonymized="q",
        explicit_filters=EditionFilter(),
        response=GeneratedAnswer(
            answer_markdown="...",
            abstained=True,
            abstention_reason="sem suporte no acervo",
        ),
    )
    restored = AnswerRun.model_validate_json(run.model_dump_json())
    assert isinstance(restored.response, GeneratedAnswer)
    assert restored.response.abstained


def test_union_response_discriminates_quote() -> None:
    run = AnswerRun(
        question_original="q",
        question_anonymized="q",
        explicit_filters=EditionFilter(),
        response=QuoteResponse(),
    )
    restored = AnswerRun.model_validate_json(run.model_dump_json())
    assert isinstance(restored.response, QuoteResponse)
