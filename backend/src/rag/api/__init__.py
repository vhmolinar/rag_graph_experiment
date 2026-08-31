"""Camada HTTP (FastAPI). Nunca importada pelo domínio (T14).

`create_app` (app.py) é a fábrica; `rag serve` inicia uvicorn com ela.
"""

from rag.api.app import create_app

__all__ = ["create_app"]
