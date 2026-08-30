"""Repository de registros de versão imutáveis.

`get_or_create` é idempotente: mesma chave única retorna o registro existente.
Não há método de update/delete — o banco rejeita mutação via trigger (SPEC §6).
Nomes de tabela/coluna vêm de allowlist fixa, nunca de entrada externa.
"""

from typing import TypeVar
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rag.domain.errors import EmbeddingDimensionError
from rag.domain.versions import (
    ChunkingVersion,
    ContextPolicyVersion,
    EmbeddingVersion,
    ExtractionVersion,
    ModelEndpointVersion,
    PromptVersion,
    RetrievalPolicyVersion,
    VersionRecord,
)
from rag.infrastructure.schema import EMBEDDING_COLUMN_DIMENSIONS

V = TypeVar("V", bound=VersionRecord)

# Allowlist fixa: tipo de domínio -> (tabela, colunas extras, colunas da chave única).
_TABLES: dict[type[VersionRecord], tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    ExtractionVersion: ("extraction_versions", (), ("label", "params")),
    ChunkingVersion: ("chunking_versions", (), ("label", "params")),
    EmbeddingVersion: (
        "embedding_versions",
        ("model_name", "dimensions"),
        ("label", "model_name", "dimensions", "params"),
    ),
    ModelEndpointVersion: (
        "model_endpoint_versions",
        ("endpoint_kind", "provider", "model_name"),
        ("label", "endpoint_kind", "provider", "model_name", "params"),
    ),
    PromptVersion: (
        "prompt_versions",
        ("template_sha256",),
        # R03: o hash do template participa da identidade — templates distintos
        # com mesmo label/params geram versões distintas.
        ("label", "template_sha256", "params"),
    ),
    RetrievalPolicyVersion: ("retrieval_policy_versions", (), ("label", "params")),
    ContextPolicyVersion: ("context_policy_versions", (), ("label", "params")),
}


class VersionsRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def get_or_create(self, version: V) -> V:
        # RR05: versão de embedding incompatível com a capacidade do schema é
        # rejeitada no cadastro, antes de qualquer ingestão.
        if (
            isinstance(version, EmbeddingVersion)
            and version.dimensions != EMBEDDING_COLUMN_DIMENSIONS
        ):
            raise EmbeddingDimensionError(
                "Dimensão da versão de embedding incompatível com o schema "
                f"(vector({EMBEDDING_COLUMN_DIMENSIONS})).",
                context={
                    "dimensions": version.dimensions,
                    "schema_dimensions": EMBEDDING_COLUMN_DIMENSIONS,
                },
            )
        table, extras, unique_cols = _TABLES[type(version)]
        data = version.model_dump(mode="json")
        columns = ["id", "label", "params", "created_at", *extras]
        values: dict[str, object] = {
            "id": version.id,
            "label": version.label,
            "created_at": version.created_at,
            "params": Jsonb(data["params"]),
        }
        for column in extras:
            values[column] = data[column]

        column_list = ", ".join(columns)
        placeholders = ", ".join(f"%({c})s" for c in columns)
        conflict_target = ", ".join(unique_cols)
        # Identificadores (tabela/colunas) vêm exclusivamente da allowlist _TABLES,
        # indexada por tipo de domínio — nunca de entrada externa. Valores são
        # sempre parametrizados.
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"INSERT INTO {table} ({column_list}) VALUES ({placeholders}) "  # noqa: S608
                f"ON CONFLICT ({conflict_target}) DO NOTHING",
                values,
            )
            where = " AND ".join(
                f"{c} = %({c})s" if c != "params" else "params = %(params)s" for c in unique_cols
            )
            await cur.execute(
                f"SELECT id, label, params, created_at{', ' if extras else ''}"  # noqa: S608
                f"{', '.join(extras)} FROM {table} WHERE {where}",
                values,
            )
            row = await cur.fetchone()
        if row is None:  # pragma: no cover - defensivo
            msg = f"falha ao ler versão recém-criada em {table}"
            raise RuntimeError(msg)
        return type(version)(**row)

    async def get(self, kind: type[V], version_id: UUID) -> V | None:
        table, extras, _ = _TABLES[kind]
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT id, label, params, created_at{', ' if extras else ''}"  # noqa: S608
                f"{', '.join(extras)} FROM {table} WHERE id = %(id)s",
                {"id": version_id},
            )
            row = await cur.fetchone()
        return kind(**row) if row else None
