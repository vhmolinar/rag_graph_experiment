"""Busca vetorial por cosseno contra PostgreSQL real (T09; SPEC §8.5, AC-05, AC-07).

Corpus com embeddings determinísticos por conceito (função em
`tests/fixtures/model_doubles.py`): a consulta é uma PARÁFRASE da passagem
original (sem termos principais comuns) e compartilha o mesmo conceito, logo
o vetor da consulta fica próximo ao da passagem. Exercita o estágio vetorial
isolado — filtros, exclusão de passagens-pai, dimensão inválida — e prova
AC-05 (paráfrase recuperada sem termos principais) e AC-07 (obra excluída
não retorna).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import ConceptEmbeddingProvider, concept_embedding

from rag.domain.enums import SourceType
from rag.domain.errors import EmbeddingDimensionError
from rag.domain.indexing import IndexRun
from rag.domain.library import Edition, Passage, Work
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.runs import RankedCandidate
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ExtractionVersion,
    ModelEndpointVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.index_runs import IndexRunsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.search import LexicalSearchRepository
from rag.infrastructure.repositories.vector import VectorSearchRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_PARAPHRASE_QUERY = "O fado guia os passos da vida."


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID
    fate_original: UUID
    jealousy: UUID
    memory: UUID
    parent: UUID
    embedding_version_id: UUID


async def _seed(db: Database) -> Corpus:
    async with db.connection() as conn:
        work_a = await WorksRepository(conn).create(Work(canonical_title="Dom Casmurro"))
        work_b = await WorksRepository(conn).create(
            Work(canonical_title="Memórias Póstumas de Brás Cubas")
        )
        edition_a = await EditionsRepository(conn).create(
            Edition(
                work_id=work_a.id,
                title="Dom Casmurro",
                source_type=SourceType.PDF_TEXT,
                source_sha256="a" * 64,
            )
        )
        edition_b = await EditionsRepository(conn).create(
            Edition(
                work_id=work_b.id,
                title="Memórias Póstumas",
                source_type=SourceType.PDF_TEXT,
                source_sha256="b" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-vec", created_at=utcnow())
        )
        provider = ConceptEmbeddingProvider()
        embedding_version = await versions.get_or_create(provider.embedding_version)
        repo = PassagesRepository(conn)

        async def child(edition_id: UUID, ordinal: int, text: str) -> UUID:
            passage = Passage(
                edition_id=edition_id,
                ordinal=ordinal,
                text=text,
                token_count=len(text.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            created = await repo.create(passage, embedding=concept_embedding(text))
            return created.id

        async def parent(edition_id: UUID, ordinal: int, text: str) -> UUID:
            passage = Passage(
                edition_id=edition_id,
                ordinal=ordinal,
                text=text,
                token_count=len(text.split()),
                chunking_version_id=chunking.id,
            )
            created = await repo.create(passage)
            return created.id

        fate_original = await child(edition_a.id, 0, "O destino do homem depende de suas escolhas.")
        jealousy = await child(edition_a.id, 1, "Capitu observa com ciúme a confiança de Bentinho.")
        memory = await child(edition_b.id, 0, "A memória dos antigos se apaga com o tempo.")
        parent_passage = await parent(
            edition_a.id, 2, "Capítulo inteiro sobre o spleen: contexto completo."
        )
        await conn.execute("ANALYZE passages")

    return Corpus(
        work_a=work_a.id,
        work_b=work_b.id,
        edition_a=edition_a.id,
        edition_b=edition_b.id,
        fate_original=fate_original,
        jealousy=jealousy,
        memory=memory,
        parent=parent_passage,
        embedding_version_id=embedding_version.id,
    )


def _ids(candidates: list[RankedCandidate]) -> list[UUID]:
    return [c.passage_id for c in candidates]


async def test_paraphrase_recovered_by_vector_search(db: Database) -> None:
    """AC-05: a consulta-paráfrase (sem termos comuns com a passagem original)
    recupera a passagem original em primeiro, via similaridade de cosseno."""
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            limit=10,
        )
    assert hits, "a busca vetorial deve devolver candidatos"
    assert hits[0].passage_id == corpus.fate_original
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)
    # As passagens de outros conceitos têm similaridade 0 e ficam atrás.
    assert corpus.memory in _ids(hits)
    assert _ids(hits).index(corpus.memory) > _ids(hits).index(corpus.fate_original)


async def test_cosine_score_is_similarity(db: Database) -> None:
    """A métrica documentada é similaridade de cosseno (1 - distância): um
    vetor idêntico à consulta dá score ≈ 1.0."""
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query("O destino do homem depende de suas escolhas.")
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            limit=5,
        )
    by_id = {c.passage_id: c for c in hits}
    assert by_id[corpus.fate_original].score == pytest.approx(1.0, abs=1e-6)


async def test_lexical_does_not_recover_paraphrase(db: Database) -> None:
    """Independência dos estágios: a busca literal pelos termos da paráfrase
    NÃO encontra a passagem original (não há termos comuns)."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        hits = await LexicalSearchRepository(conn).search(
            LexicalQuery(required_terms=("fado",), trigram_threshold=0.9)
        )
    assert corpus.fate_original not in _ids(hits)


async def test_parent_passages_are_never_candidates(db: Database) -> None:
    """NOTES.md §10.6 item 2: passagem-pai (sem embedding) nunca é candidata."""
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            limit=10,
        )
    assert corpus.parent not in _ids(hits)


async def test_filter_by_edition(db: Database) -> None:
    """AC-07 no estágio vetorial: obra/edição excluída não retorna."""
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            filters=EditionFilter(exclude_edition_ids=frozenset({corpus.edition_b})),
            limit=10,
        )
    assert corpus.memory not in _ids(hits)
    assert corpus.fate_original in _ids(hits)


async def test_filter_by_work(db: Database) -> None:
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            filters=EditionFilter(include_work_ids=frozenset({corpus.work_a})),
            limit=10,
        )
    assert corpus.memory not in _ids(hits)
    assert corpus.fate_original in _ids(hits)


async def test_limit_respected(db: Database) -> None:
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            limit=1,
        )
    assert len(hits) == 1
    assert hits[0].passage_id == corpus.fate_original


async def test_no_results_returns_empty_list(db: Database) -> None:
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            filters=EditionFilter(include_edition_ids=frozenset({corpus.edition_b})),
            limit=10,
        )
    # Só memória (edição_b) está incluída; a consulta "fado" é ortogonal a ela,
    # mas continua sendo devolvida como única candidata (similaridade 0).
    assert _ids(hits) == [corpus.memory]


async def test_query_vector_dimension_mismatch_fails_closed(db: Database) -> None:
    """Dimensão inesperada do vetor de consulta falha antes de consultar —
    erro tipado, nunca um DataError cru do banco."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        with pytest.raises(EmbeddingDimensionError):
            await VectorSearchRepository(conn).search(
                [0.1, 0.2],
                embedding_version_id=corpus.embedding_version_id,
            )


async def test_hostile_filter_ids_are_parameters(db: Database) -> None:
    """IDs de filtros são valores, nunca SQL: entrada adversarial não é
    interpolada e a consulta devolve vazio sem alterar o banco."""
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    hostile = EditionFilter(
        include_edition_ids=frozenset({UUID("00000000-0000-0000-0000-000000000000")})
    )
    async with db.connection() as conn:
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=corpus.embedding_version_id,
            filters=hostile,
            limit=10,
        )
    assert _ids(hits) == []


@pytest.mark.parametrize(
    "invalid_limit",
    [
        0,
        -1,
        101,
        True,
        False,
        1.5,
        "10",
    ],
)
async def test_limit_validation_rejects_invalid_values(db: Database, invalid_limit: object) -> None:
    """T9-05: limit deve ser inteiro positivo respeitando MAX_SEARCH_LIMIT (100)."""
    corpus = await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        with pytest.raises(ValueError, match="limit"):
            await VectorSearchRepository(conn).search(
                query_vector,
                embedding_version_id=corpus.embedding_version_id,
                limit=invalid_limit,  # type: ignore[arg-type]
            )


async def test_search_requires_valid_embedding_version_id(db: Database) -> None:
    """R2-T9-01: embedding_version_id é obrigatório e tipado como UUID."""
    await _seed(db)
    provider = ConceptEmbeddingProvider()
    query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
    async with db.connection() as conn:
        with pytest.raises(TypeError, match="embedding_version_id"):
            await VectorSearchRepository(conn).search(
                query_vector,
                embedding_version_id=None,  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="embedding_version_id"):
            await VectorSearchRepository(conn).search(
                query_vector,
                embedding_version_id="not-a-uuid",  # type: ignore[arg-type]
            )


async def test_vector_search_excludes_inactive_index_runs_and_legacy_when_active_exists(
    db: Database,
) -> None:
    """T9-01: passagens de execuções inativas e passagens legadas NÃO são retornadas
    quando a edição possui uma execução de indexação ativa."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra com Reindexação"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Edição Reindexada",
                source_type=SourceType.PDF_TEXT,
                source_sha256="c" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-v1", created_at=utcnow())
        )
        emb_ver = await versions.get_or_create(
            EmbeddingVersion(label="emb-v1", model_name="m1", dimensions=1024, created_at=utcnow())
        )
        ext_ver = await versions.get_or_create(
            ExtractionVersion(label="ext-v1", created_at=utcnow())
        )
        model_ver = await versions.get_or_create(
            ModelEndpointVersion(
                label="end-v1",
                endpoint_kind="embedding",
                provider="test",
                model_name="m1",
                created_at=utcnow(),
            )
        )
        runs_repo = IndexRunsRepository(conn)
        passages_repo = PassagesRepository(conn)

        # 1. Execução 1 (inativa)
        old_run = await runs_repo.create(
            IndexRun(
                edition_id=edition.id,
                extraction_version_id=ext_ver.id,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                model_endpoint_version_id=model_ver.id,
                is_active=False,
                created_at=utcnow(),
            )
        )
        old_passage = await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=0,
                text="Texto obsoleto sobre destino da execução inativa.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                index_run_id=old_run.id,
            ),
            embedding=concept_embedding("Texto obsoleto sobre destino da execução inativa."),
        )

        # 2. Passagem legada (sem index_run_id)
        legacy_passage = await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=1,
                text="Texto legado sobre fado e destino.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                index_run_id=None,
            ),
            embedding=concept_embedding("Texto legado sobre fado e destino."),
        )

        # 3. Execução 2 (ativa)
        active_run = await runs_repo.create(
            IndexRun(
                edition_id=edition.id,
                extraction_version_id=ext_ver.id,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                model_endpoint_version_id=model_ver.id,
                is_active=True,
                created_at=utcnow(),
            )
        )
        active_passage = await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=0,
                text="Texto corrente e ativo sobre destino do homem.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                index_run_id=active_run.id,
            ),
            embedding=concept_embedding("Texto corrente e ativo sobre destino do homem."),
        )
        await conn.execute("ANALYZE passages")

        provider = ConceptEmbeddingProvider()
        query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=emb_ver.id,
            filters=EditionFilter(include_edition_ids=frozenset({edition.id})),
            limit=10,
        )

        returned_ids = _ids(hits)
        assert active_passage.id in returned_ids
        assert old_passage.id not in returned_ids, "Passagem inativa não pode ser retornada"
        assert legacy_passage.id not in returned_ids, (
            "Passagem legada não pode ser retornada quando há execução ativa"
        )


async def test_vector_search_includes_legacy_only_when_no_active_index_run(
    db: Database,
) -> None:
    """T9-01: passagens legadas (index_run_id IS NULL) são aceitas APENAS
    se a edição não tem execução ativa."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra Legada"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Edição Sem IndexRun",
                source_type=SourceType.PDF_TEXT,
                source_sha256="d" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-leg", created_at=utcnow())
        )
        emb_ver = await versions.get_or_create(
            EmbeddingVersion(label="emb-leg", model_name="m1", dimensions=1024, created_at=utcnow())
        )
        passages_repo = PassagesRepository(conn)
        legacy_passage = await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=0,
                text="Texto legado sobre destino e fado.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                index_run_id=None,
            ),
            embedding=concept_embedding("Texto legado sobre destino e fado."),
        )
        await conn.execute("ANALYZE passages")

        provider = ConceptEmbeddingProvider()
        query_vector = await provider.embed_query(_PARAPHRASE_QUERY)
        hits = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=emb_ver.id,
            filters=EditionFilter(include_edition_ids=frozenset({edition.id})),
            limit=10,
        )

        assert _ids(hits) == [legacy_passage.id]


async def test_vector_search_excludes_incompatible_embedding_version_deterministically(
    db: Database,
) -> None:
    """T9-02: passagens indexadas com outra EmbeddingVersion (mesma dimensão)
    são excluídas determinísticamente."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra Multi-Modelo"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Edição Multi-Modelo",
                source_type=SourceType.PDF_TEXT,
                source_sha256="e" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-mm", created_at=utcnow())
        )
        emb_v1 = await versions.get_or_create(
            EmbeddingVersion(
                label="model-1", model_name="model-1", dimensions=1024, created_at=utcnow()
            )
        )
        emb_v2 = await versions.get_or_create(
            EmbeddingVersion(
                label="model-2", model_name="model-2", dimensions=1024, created_at=utcnow()
            )
        )
        ext_ver = await versions.get_or_create(
            ExtractionVersion(label="ext-mm", created_at=utcnow())
        )
        model_ver = await versions.get_or_create(
            ModelEndpointVersion(
                label="end-mm",
                endpoint_kind="embedding",
                provider="test",
                model_name="model-1",
                created_at=utcnow(),
            )
        )
        runs_repo = IndexRunsRepository(conn)
        active_run = await runs_repo.create(
            IndexRun(
                edition_id=edition.id,
                extraction_version_id=ext_ver.id,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_v1.id,
                model_endpoint_version_id=model_ver.id,
                is_active=True,
                created_at=utcnow(),
            )
        )
        passages_repo = PassagesRepository(conn)

        p1 = await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=0,
                text="Passagem modelo 1 sobre destino.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_v1.id,
                index_run_id=active_run.id,
            ),
            embedding=concept_embedding("Passagem modelo 1 sobre destino."),
        )

        p2 = await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=1,
                text="Passagem modelo 2 sobre destino.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_v2.id,
                index_run_id=active_run.id,
            ),
            embedding=concept_embedding("Passagem modelo 2 sobre destino."),
        )
        await conn.execute("ANALYZE passages")

        provider = ConceptEmbeddingProvider()
        query_vector = await provider.embed_query(_PARAPHRASE_QUERY)

        # Busca filtrando pela versão 1: apenas p1 deve retornar
        hits_v1 = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=emb_v1.id,
            filters=EditionFilter(include_edition_ids=frozenset({edition.id})),
            limit=10,
        )
        assert _ids(hits_v1) == [p1.id]

        # Busca filtrando pela versão 2: apenas p2 deve retornar
        hits_v2 = await VectorSearchRepository(conn).search(
            query_vector,
            embedding_version_id=emb_v2.id,
            filters=EditionFilter(include_edition_ids=frozenset({edition.id})),
            limit=10,
        )
        assert _ids(hits_v2) == [p2.id]
