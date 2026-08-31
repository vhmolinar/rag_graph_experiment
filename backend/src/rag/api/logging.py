"""Logging estruturado da API (T14; SPEC §13.1, AC-16).

Mesmo formato do CLI (JSON, stderr): request_id, IDs e versões — nunca
textos integrais do acervo, chaves ou prompts completos. O traceback nunca
vai ao console; só `error_type`. O detalhe interno fica nos logs com
`request_id` correlacionado.

A redação de `exc_info` é a mesma convenção do CLI (`cli/main.py`), mantida
em separado para não importar o módulo CLI (que configura logging ao import).
"""

import sys
from collections.abc import MutableMapping
from typing import Any

import structlog


def _redact_exception(
    _logger: Any,  # noqa: ANN401 -- assinatura de Processor do structlog
    _name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Remove o traceback do log de console; mantém só `error_type`."""
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return event_dict
    if exc_info is True:
        exc_type, _exc_value, _tb = sys.exc_info()
    else:
        exc_type, _exc_value, _tb = exc_info
    event_dict["error_type"] = exc_type.__name__ if exc_type is not None else "desconhecido"
    return event_dict


def configure_logging() -> None:
    """Configura structlog global (JSON, redação de traceback). Idempotente."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.contextvars.merge_contextvars,
            _redact_exception,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
