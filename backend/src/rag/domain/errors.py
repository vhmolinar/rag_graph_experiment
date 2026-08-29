"""Erros tipados do domínio.

`message` é sempre segura para o cliente: sem stack trace, SQL, caminhos locais ou
segredos. Detalhes internos ficam em `cause`/`context` e só vão para logs sanitizados.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_INVALID_RESPONSE = "MODEL_INVALID_RESPONSE"
    EMBEDDING_DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"
    RATE_LIMITED = "RATE_LIMITED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INGESTION_FAILED = "INGESTION_FAILED"
    OCR_FAILED = "OCR_FAILED"
    STORAGE_ERROR = "STORAGE_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_ErrorKwargs = dict[str, Any]


class RagError(Exception):
    """Base de todos os erros do sistema. Falha explícita, nunca silenciosa."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.cause = cause
        self.context = context or {}


class ModelError(RagError):
    """Falha em provedor de modelo (embedding, reranker, gerador)."""


def _model_kwargs(
    code: ErrorCode,
    cause: BaseException | None,
    context: _ErrorKwargs | None,
) -> _ErrorKwargs:
    return {"code": code, "cause": cause, "context": context}


class ModelTimeoutError(ModelError):
    def __init__(
        self,
        message: str = "Tempo limite do modelo excedido.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.MODEL_TIMEOUT, cause, context))


class ModelUnavailableError(ModelError):
    def __init__(
        self,
        message: str = "Modelo indisponível.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.MODEL_UNAVAILABLE, cause, context))


class ModelResponseError(ModelError):
    def __init__(
        self,
        message: str = "Resposta inválida do modelo.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.MODEL_INVALID_RESPONSE, cause, context))


class EmbeddingDimensionError(RagError):
    def __init__(
        self,
        message: str = "Dimensão de embedding incompatível.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(
            message, **_model_kwargs(ErrorCode.EMBEDDING_DIMENSION_MISMATCH, cause, context)
        )


class NotFoundError(RagError):
    def __init__(
        self,
        message: str = "Recurso não encontrado.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.NOT_FOUND, cause, context))


class ConflictError(RagError):
    def __init__(
        self,
        message: str = "Conflito de estado.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.CONFLICT, cause, context))


class ConcurrencyError(ConflictError):
    """Perda de corrida otimista: o registro foi modificado desde a leitura.

    Tipo distinto de `NotFoundError` (R4-01): o recurso existe, mas a revisão
    esperada não corresponde mais à persistida.
    """

    def __init__(
        self,
        message: str = "Registro modificado concorrentemente.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, cause=cause, context=context)


class InvalidTransitionError(RagError):
    def __init__(
        self,
        message: str = "Transição de estado inválida.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.VALIDATION_ERROR, cause, context))


class RateLimitError(RagError):
    def __init__(
        self,
        message: str = "Limite de requisições excedido.",
        *,
        retry_after_seconds: int = 60,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.RATE_LIMITED, cause, context))
        self.retry_after_seconds = retry_after_seconds


class VerificationError(RagError):
    def __init__(
        self,
        message: str = "Falha na verificação da resposta.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.VERIFICATION_FAILED, cause, context))


class IngestionError(RagError):
    def __init__(
        self,
        message: str = "Falha na ingestão do documento.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.INGESTION_FAILED, cause, context))


class OcrError(RagError):
    def __init__(
        self,
        message: str = "Falha na conversão OCR.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.OCR_FAILED, cause, context))


class StorageError(RagError):
    def __init__(
        self,
        message: str = "Falha no armazenamento de artefatos.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.STORAGE_ERROR, cause, context))


class DatabaseError(RagError):
    def __init__(
        self,
        message: str = "Falha no banco de dados.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.DATABASE_ERROR, cause, context))


class CancelledError(RagError):
    def __init__(
        self,
        message: str = "Operação cancelada.",
        *,
        cause: BaseException | None = None,
        context: _ErrorKwargs | None = None,
    ) -> None:
        super().__init__(message, **_model_kwargs(ErrorCode.CANCELLED, cause, context))
