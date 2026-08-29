"""Ambiente Alembic — migrations em SQL puro, sem ORM.

A URL vem de RAG_DATABASE_URL (ambiente) ou de -x url=... Nunca de código versionado.
SQLAlchemy aparece aqui apenas como motor de execução das migrations (ferramenta);
a aplicação usa psycopg3 diretamente.
"""

import os

from alembic import context
from sqlalchemy import create_engine

config = context.config


def _url() -> str:
    url = os.environ.get("RAG_DATABASE_URL")
    if not url:
        url = config.get_main_option("sqlalchemy.url") or ""
    if not url:
        x_args = context.get_x_argument(as_dictionary=True)
        url = x_args.get("url", "")
    if not url:
        raise RuntimeError(
            "RAG_DATABASE_URL não definida; exporte a URL do banco antes de migrar"
        )
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
