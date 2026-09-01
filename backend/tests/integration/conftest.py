"""Fixtures de integração: PostgreSQL+pgvector real via testcontainers.

As migrations são aplicadas com Alembic contra o container — o mesmo caminho
usado em produção (RAG_DATABASE_URL).
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TypedDict

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from rag.infrastructure.config import DatabaseSettings
from rag.infrastructure.db import Database


class PgParams(TypedDict):
    host: str
    port: int
    db: str
    user: str
    password: str


PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg17-bookworm"
BACKEND_DIR = Path(__file__).resolve().parents[2]

ALL_TABLES = (
    "session_entries",
    "answer_runs",
    "sessions",
    "concept_evidence",
    "concept_aliases",
    "concepts",
    "summary_supports",
    "summaries",
    "passages",
    "index_runs",
    "pages",
    "sections",
    "derived_artifacts",
    "editions",
    "contributors",
    "works",
    "extraction_versions",
    "chunking_versions",
    "embedding_versions",
    "model_endpoint_versions",
    "prompt_versions",
    "retrieval_policy_versions",
)


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(PGVECTOR_IMAGE) as container:
        yield container


@pytest.fixture(scope="session")
def pg_params(pg_container: PostgresContainer) -> PgParams:
    return {
        "host": pg_container.get_container_host_ip(),
        "port": int(pg_container.get_exposed_port(5432)),
        "db": pg_container.dbname,
        "user": pg_container.username,
        "password": pg_container.password,
    }


@pytest.fixture(scope="session")
def migrated(pg_params: PgParams) -> PgParams:
    url = (
        f"postgresql+psycopg://{pg_params['user']}:{pg_params['password']}"
        f"@{pg_params['host']}:{pg_params['port']}/{pg_params['db']}"
    )
    command.upgrade(alembic_config(url), "head")
    return pg_params


@pytest.fixture
async def db(migrated: PgParams) -> AsyncIterator[Database]:
    settings = DatabaseSettings(
        host=migrated["host"],
        port=migrated["port"],
        db=migrated["db"],
        user=migrated["user"],
        password=SecretStr(migrated["password"]),
    )
    database = Database(settings)
    await database.open()
    try:
        yield database
    finally:
        async with database.connection() as conn:
            await conn.execute(f"TRUNCATE {', '.join(ALL_TABLES)} CASCADE")
        await database.close()
