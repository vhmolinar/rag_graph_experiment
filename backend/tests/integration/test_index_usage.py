"""Plano de consulta demonstra uso dos índices FTS e vetorial (T03).

`enable_seqscan = off` força o planejador a preferir índices no dataset pequeno de
teste; o objetivo é provar que os índices são utilizáveis pelas consultas reais.
"""

import random

from psycopg import AsyncConnection

from rag.domain.enums import SourceType
from rag.domain.library import Edition, Passage, Work
from rag.domain.versions import ChunkingVersion, EmbeddingVersion, utcnow
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_WORDS = [
    "liberdade",
    "destino",
    "memória",
    "tempo",
    "razão",
    "spleen",
    "tédio",
    "amor",
    "ciência",
    "história",
    "capitu",
    "bentinho",
    "romance",
    "sociedade",
    "linguagem",
    "verdade",
    "dúvida",
    "ciúme",
    "narrador",
]


async def _seed(db: Database, count: int = 1500) -> None:
    # Dataset determinístico (seed fixa) para planos de consulta reproduzíveis;
    # não é uso criptográfico.
    rng = random.Random(42)  # noqa: S311
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra de teste"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Obra de teste",
                source_type=SourceType.PDF_TEXT,
                source_sha256="e" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-idx", created_at=utcnow())
        )
        embedding_version = await versions.get_or_create(
            EmbeddingVersion(label="emb-idx", model_name="m", dimensions=1024, created_at=utcnow())
        )
        repo = PassagesRepository(conn)
        for i in range(count):
            text = " ".join(rng.choice(_WORDS) for _ in range(20))
            passage = Passage(
                edition_id=edition.id,
                ordinal=i,
                text=text,
                token_count=20,
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            vector = [rng.random() for _ in range(1024)]
            await repo.create(passage, embedding=vector)
        await conn.execute("ANALYZE passages")


async def _plan_text(conn: AsyncConnection, sql: str, params: list[object]) -> str:
    cursor = await conn.execute("EXPLAIN (COSTS OFF) " + sql, params)
    return "\n".join(row[0] for row in await cursor.fetchall())


async def test_fts_query_uses_gin_index(db: Database) -> None:
    await _seed(db)
    async with db.connection() as conn:
        await conn.execute("SET enable_seqscan = off")
        plan = await _plan_text(
            conn,
            "SELECT id FROM passages WHERE text_search @@ "
            "plainto_tsquery('portuguese_unaccent', %s)",
            ["liberdade"],
        )
    assert "passages_text_search_gin" in plan


async def test_vector_query_uses_hnsw_index(db: Database) -> None:
    await _seed(db)
    async with db.connection() as conn:
        await conn.execute("SET enable_seqscan = off")
        plan = await _plan_text(
            conn,
            "SELECT id FROM passages ORDER BY embedding <=> %s::vector LIMIT 5",
            [[0.5] * 1024],
        )
    assert "passages_embedding_hnsw" in plan


async def test_trgm_similarity_query_uses_gin_index(db: Database) -> None:
    """R10: prova de plano para o índice trigram usado na busca aproximada."""
    await _seed(db)
    async with db.connection() as conn:
        await conn.execute("SET enable_seqscan = off")
        plan = await _plan_text(
            conn,
            "SELECT id FROM passages WHERE rag_immutable_unaccent(text) %% %s "
            "ORDER BY similarity(rag_immutable_unaccent(text), %s) DESC LIMIT 5",
            ["capitu", "capitu"],
        )
    assert "passages_text_trgm_gin" in plan
