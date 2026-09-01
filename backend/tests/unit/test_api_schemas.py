"""Testes unitários dos contratos da API (T14; SPEC §10).

Cobre: derivação de `mode` do tipo da resposta, projeção `AnswerRun` →
`QueryState` (GET / SSE terminal), e envelope de erro.
"""

from rag.api.errors import ErrorEnvelope, ErrorOut
from rag.api.schemas import build_query_state, mode_of
from rag.domain.answer import GeneratedAnswer, QuoteResponse
from rag.domain.enums import AnswerMode, QueryStatus
from rag.domain.query import EditionFilter
from rag.domain.runs import AnswerRun


def _quote_response() -> QuoteResponse:
    return QuoteResponse(evidences=())


def _generated_answer() -> GeneratedAnswer:
    return GeneratedAnswer(
        answer_markdown="Resposta.",
        claims=(),
        limitations=(),
        abstained=False,
        abstention_reason=None,
    )


def test_mode_of_quote() -> None:
    assert mode_of(_quote_response()) is AnswerMode.QUOTE


def test_mode_of_dissertative() -> None:
    assert mode_of(_generated_answer()) is AnswerMode.DISSERTATIVE


def test_mode_of_none() -> None:
    assert mode_of(None) is None


def test_build_query_state_quote_mode() -> None:
    run = AnswerRun(
        question_original="pergunta",
        question_anonymized="pergunta",
        explicit_filters=EditionFilter(),
        status=QueryStatus.SUCCEEDED,
        response=_quote_response(),
    )
    state = build_query_state(run)
    assert state.mode is AnswerMode.QUOTE
    assert state.status is QueryStatus.SUCCEEDED
    assert state.result == _quote_response()
    assert state.error is None
    assert state.question == "pergunta"


def test_build_query_state_dissertative_mode() -> None:
    run = AnswerRun(
        question_original="pergunta",
        question_anonymized="pergunta",
        explicit_filters=EditionFilter(),
        status=QueryStatus.SUCCEEDED,
        response=_generated_answer(),
    )
    state = build_query_state(run)
    assert state.mode is AnswerMode.DISSERTATIVE
    assert isinstance(state.result, GeneratedAnswer)


def test_build_query_state_failed_carries_safe_error() -> None:
    from rag.domain.errors import ErrorCode

    run = AnswerRun(
        question_original="pergunta",
        question_anonymized="pergunta",
        explicit_filters=EditionFilter(),
        status=QueryStatus.FAILED,
        error_code=ErrorCode.MODEL_TIMEOUT,
        error_message="Não foi possível concluir a consulta.",
    )
    state = build_query_state(run)
    assert state.error is not None
    assert state.error.code == "MODEL_TIMEOUT"
    assert state.error.message
    assert state.result is None


def test_build_query_state_exposes_rewritten_query() -> None:
    """AC-13: a pergunta autônoma registrada é inspeccionável via a API."""
    run = AnswerRun(
        question_original="compare isso com o segundo autor",
        question_anonymized="compare isso com o segundo autor",
        rewritten_query="compare O Ensaio da Memória com Bruno Silva",
        explicit_filters=EditionFilter(),
        status=QueryStatus.SUCCEEDED,
        response=_quote_response(),
    )
    state = build_query_state(run)
    assert state.rewritten_query == "compare O Ensaio da Memória com Bruno Silva"
    assert state.question == "compare isso com o segundo autor"


def test_error_envelope_shape() -> None:
    envelope = ErrorEnvelope(
        error=ErrorOut(code="NOT_FOUND", message="Recurso não encontrado.", request_id="abc")
    )
    dumped = envelope.model_dump(mode="json")
    assert dumped == {
        "error": {"code": "NOT_FOUND", "message": "Recurso não encontrado.", "request_id": "abc"}
    }
