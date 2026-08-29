"""Migrations sobem banco vazio e rollback é exercitado em banco descartável (T03)."""

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest import mock

import psycopg
import pytest
from alembic import command

from tests.integration.conftest import PgParams, alembic_config


def _load_migration_0001() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("migration_0001", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _url(params: PgParams, dbname: str) -> str:
    return (
        f"postgresql+psycopg://{params['user']}:{params['password']}"
        f"@{params['host']}:{params['port']}/{dbname}"
    )


def _admin_dsn(params: PgParams, dbname: str | None = None) -> str:
    return (
        f"host={params['host']} port={params['port']} dbname={dbname or params['db']} "
        f"user={params['user']} password={params['password']}"
    )


def test_extensions_and_fts_config_present(migrated: PgParams) -> None:
    with psycopg.connect(_admin_dsn(migrated)) as conn:
        extensions = {
            row[0]
            for row in conn.execute(
                "SELECT extname FROM pg_extension WHERE extname IN "
                "('vector', 'unaccent', 'pg_trgm')"
            ).fetchall()
        }
        assert extensions == {"vector", "unaccent", "pg_trgm"}
        config = conn.execute(
            "SELECT 1 FROM pg_ts_config WHERE cfgname = 'portuguese_unaccent'"
        ).fetchone()
        assert config is not None


def test_downgrade_and_reupgrade_on_throwaway_database(
    migrated: PgParams,
) -> None:
    """Rollback seguro: downgrade -1 remove tudo; upgrade restaura head."""
    probe = "migration_probe"
    with psycopg.connect(_admin_dsn(migrated), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {probe}")
        conn.execute(f"CREATE DATABASE {probe}")
    try:
        config = alembic_config(_url(migrated, probe))
        command.upgrade(config, "head")
        command.downgrade(config, "-1")
        with psycopg.connect(_admin_dsn(migrated, probe)) as conn:
            remaining = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            ).fetchall()
            assert remaining == []
        command.upgrade(config, "head")
        with psycopg.connect(_admin_dsn(migrated, probe)) as conn:
            restored = conn.execute(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
            ).fetchone()
            assert restored is not None
            assert restored[0] > 1
    finally:
        with psycopg.connect(_admin_dsn(migrated), autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {probe} WITH (FORCE)")


def test_migration_is_deterministic_regardless_of_env(migrated: PgParams) -> None:
    """R02: variável de ambiente não altera o schema produzido pela revisão 0001."""
    probe = "migration_env_probe"
    with psycopg.connect(_admin_dsn(migrated), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {probe} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {probe}")
    try:
        config = alembic_config(_url(migrated, probe))
        with mock.patch.dict(os.environ, {"RAG_EMBEDDING_DIMENSIONS": "4096"}):
            command.upgrade(config, "head")
        with psycopg.connect(_admin_dsn(migrated, probe)) as conn:
            column_type = conn.execute(
                "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                "WHERE a.attrelid = 'passages'::regclass AND a.attname = 'embedding'"
            ).fetchone()
            assert column_type is not None
            assert column_type[0] == "vector(1024)"
    finally:
        with psycopg.connect(_admin_dsn(migrated), autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {probe} WITH (FORCE)")


def test_embedding_dimension_within_hnsw_limit() -> None:
    """R02: a dimensão fixa da revisão é suportada pelo índice HNSW sobre vector."""
    module = _load_migration_0001()
    assert 1 <= module.EMBEDDING_DIMENSIONS <= 2000


def test_embedding_versions_reject_incompatible_dimension_in_db(
    migrated: PgParams,
) -> None:
    """RRR03: o banco rejeita dimensão incompatível mesmo fora do repository."""
    with psycopg.connect(_admin_dsn(migrated), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO embedding_versions (id, label, model_name, dimensions,"
                " created_at) VALUES (gen_random_uuid(), 'emb-x', 'm', 8, now())"
            )
        conn.execute(
            "INSERT INTO embedding_versions (id, label, model_name, dimensions,"
            " created_at) VALUES (gen_random_uuid(), 'emb-ok', 'm', 1024, now())"
        )


def test_schema_constant_matches_physical_column(migrated: PgParams) -> None:
    """RRR03: a capacidade declarada pela infraestrutura corresponde ao tipo
    físico criado pela migration (e à constante da própria migration)."""
    from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

    module = _load_migration_0001()
    with psycopg.connect(_admin_dsn(migrated)) as conn:
        column_type = conn.execute(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = 'passages'::regclass AND a.attname = 'embedding'"
        ).fetchone()
    assert column_type is not None
    assert column_type[0] == f"vector({EMBEDDING_COLUMN_DIMENSIONS})"
    assert module.EMBEDDING_DIMENSIONS == EMBEDDING_COLUMN_DIMENSIONS


def test_version_tables_reject_update_and_delete(migrated: PgParams) -> None:
    """SPEC §6: registros de versão são imutáveis no banco (trigger).

    Autocommit isola cada statement; TRUNCATE ao final é a escotilha administrativa
    documentada (triggers FOR EACH ROW não disparam em TRUNCATE).
    """
    with psycopg.connect(_admin_dsn(migrated), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO chunking_versions (id, label, params, created_at) VALUES "
            "(gen_random_uuid(), 'probe', '{}'::jsonb, now())"
        )
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE chunking_versions SET label = 'x'")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM chunking_versions")
        conn.execute("TRUNCATE chunking_versions CASCADE")
