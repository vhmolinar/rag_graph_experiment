"""Tratamento centralizado de erros da API (T14; SPEC §10.1, AC-14, AC-18).

Contrato de erro da especificação:

    {"error": {"code": "MODEL_TIMEOUT", "message": "...", "request_id": "..."}}

Regras estruturais:

- `RagError` tipado do domínio é mapeado `ErrorCode` → HTTP status; `message`
  já é segura para o cliente (sem stack trace, SQL, caminhos locais ou
  segredos — `domain/errors.py`);
- `RequestValidationError` (Pydantic/FastAPI) → 422 com o mesmo envelope; o
  detalhe de validação é resumido com loc/msg, nunca valores do input;
- `HTTPException` de Starlette → envelope com código derivado do status;
- qualquer outra exceção → 500 `INTERNAL_ERROR` sanitizado, detalhes só nos
  logs (com `request_id`).
"""

from collections.abc import Sequence

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from rag.domain.errors import ErrorCode, RagError

_LOGGER = structlog.get_logger(__name__)

_GENERIC_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "Dados inválidos.",
    ErrorCode.NOT_FOUND: "Recurso não encontrado.",
    ErrorCode.CONFLICT: "Conflito de estado.",
    ErrorCode.RATE_LIMITED: "Limite de requisições excedido.",
    ErrorCode.MODEL_TIMEOUT: "Não foi possível concluir a consulta.",
    ErrorCode.MODEL_UNAVAILABLE: "O provedor de modelo está indisponível.",
    ErrorCode.MODEL_INVALID_RESPONSE: "Resposta inválida do provedor de modelo.",
    ErrorCode.EMBEDDING_DIMENSION_MISMATCH: "Dimensão de embedding incompatível.",
    ErrorCode.VERIFICATION_FAILED: "A resposta não pôde ser verificada.",
    ErrorCode.INGESTION_FAILED: "Falha na ingestão do documento.",
    ErrorCode.OCR_FAILED: "Falha na conversão OCR.",
    ErrorCode.STORAGE_ERROR: "Falha no armazenamento de artefatos.",
    ErrorCode.DATABASE_ERROR: "Falha no banco de dados.",
    ErrorCode.CANCELLED: "Operação cancelada.",
    ErrorCode.INTERNAL_ERROR: "Erro interno do servidor.",
}

_CODE_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_INVALID_RESPONSE: 502,
    ErrorCode.EMBEDDING_DIMENSION_MISMATCH: 502,
    ErrorCode.VERIFICATION_FAILED: 502,
    ErrorCode.INGESTION_FAILED: 500,
    ErrorCode.OCR_FAILED: 500,
    ErrorCode.STORAGE_ERROR: 500,
    ErrorCode.DATABASE_ERROR: 500,
    ErrorCode.CANCELLED: 409,
    ErrorCode.INTERNAL_ERROR: 500,
}

_HTTP_STATUS_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


class ErrorOut(BaseModel):
    """Corpo do erro exposto ao cliente (SPEC §10.1)."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorOut


def code_to_status(code: ErrorCode) -> int:
    return _CODE_STATUS.get(code, 500)


def safe_message(code: ErrorCode, message: str | None) -> str:
    """Mensagem segura para o cliente: a do erro quando informada e conocida,
    senão um genérico por código."""
    if message and message.strip():
        return message.strip()
    return _GENERIC_MESSAGES.get(code, _GENERIC_MESSAGES[ErrorCode.INTERNAL_ERROR])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "desconhecido"))


def error_response(
    request: Request,
    status: int,
    code: ErrorCode,
    message: str | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorEnvelope(
            error=ErrorOut(
                code=code.value,
                message=safe_message(code, message),
                request_id=_request_id(request),
            )
        ).model_dump(),
        headers=headers,
    )


def _validation_summary(errors: Sequence[dict[str, object]]) -> str:
    """Resumo sanitizado dos erros de validação: loc + msg, nunca o valor do
    input (que poderia conter segredos ou texto do usuário)."""
    parts: list[str] = []
    for error in errors:
        loc_value = error.get("loc", ())
        loc = (
            ".".join(str(part) for part in loc_value)
            if isinstance(loc_value, (list, tuple))
            else ""
        )
        msg = str(error.get("msg", "valor inválido"))
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts[:8])


def _handle_rag_error(request: Request, exc: RagError) -> JSONResponse:
    status = code_to_status(exc.code)
    _LOGGER.info(
        "http.error",
        request_id=_request_id(request),
        code=exc.code.value,
        status=status,
        error=exc.message,
    )
    return error_response(request, status, exc.code, exc.message)


def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = _validation_summary(exc.errors())
    _LOGGER.info(
        "http.validation_error",
        request_id=_request_id(request),
        detail=detail,
    )
    message = f"Dados inválidos: {detail}" if detail else "Dados inválidos."
    return error_response(request, 422, ErrorCode.VALIDATION_ERROR, message)


def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    message = (
        None
        if code is ErrorCode.INTERNAL_ERROR
        else (str(exc.detail) if isinstance(exc.detail, str) and exc.detail else None)
    )
    return error_response(request, exc.status_code, code, message)


def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    _LOGGER.exception(
        "http.unhandled_error",
        request_id=request_id,
        error_type=type(exc).__name__,
    )
    return error_response(request, 500, ErrorCode.INTERNAL_ERROR)


def install_exception_handlers(app: FastAPI) -> None:
    # As handlers com exceções tipadas não são assignables ao tipo genérico de
    # Starlette (contravariância); a assinatura real só invoca os subtipos.
    app.add_exception_handler(RagError, _handle_rag_error)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        _handle_validation_error,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException,
        _handle_http_exception,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, _handle_unhandled)
