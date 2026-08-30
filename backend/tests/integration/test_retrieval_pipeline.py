"""Pipeline de recuperação: lexical + vetorial + RRF + reranking contra
PostgreSQL real (T09; SPEC §8.5, AC-05, AC-06, AC-07).

Cobre:
- estágios independentes e preservados em `RetrievalResult` (AC-06);
- RRF funde as duas listas com scores determinísticos esperados;
- o reranker altera a ordem num caso controlado;
- obra excluída não chega ao reranker (AC-07);
- falha do reranker não é mascarada como sucesso (falha fechada);
- a política de orçamento fica registrada como `RetrievalPolicyVersion` (AC-15).
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import ConceptEmbeddingProvider, FakeRerankerProvider

from rag.application.search import RetrievalService
from rag.domain.enums import Depth, SourceType
from rag.domain.errors import ModelTimeoutError
from rag.domain.library import Edition, Passage, Work
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.retrieval import RetrievalPolicy
from rag.domain.runs import RankedCandidate
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    RetrievalPolicyVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.versions import VersionsRepository
from rag.infrastructure.repositories.works import WorksRepository

_SEMANTIC_QUERY = "liberdade spleen destino"


@dataclass
class Corpus:
    work_a: UUID
    work_b: UUID
    edition_a: UUID
    edition_b: UUID
    a: UUID
    b: UUID
    c: UUID

    @property
    def texts(self) -> dict[UUID, str]:
        return {
            self.a: "O spleen sufoca a liberdade de Bentinho.",
            self.b: "O destino e o ciúme e a liberdade misturan o spleen de Capitu.",
            self.c: "O spleen guia a memória dos antigos.",
        }


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
            ChunkingVersion(label="chunk-pipe", created_at=utcnow())
        )
        embedding_version = await versions.get_or_create(
            EmbeddingVersion(label="emb-pipe", model_name="m", dimensions=1024, created_at=utcnow())
        )
        repo = PassagesRepository(conn)
        provider = ConceptEmbeddingProvider()

        async def child(edition_id: UUID, ordinal: int, text: str) -> UUID:
            passage = Passage(
                edition_id=edition_id,
                ordinal=ordinal,
                text=text,
                token_count=len(text.split()),
                chunking_version_id=chunking.id,
                embedding_version_id=embedding_version.id,
            )
            created = await repo.create(passage, embedding=await provider.embed_query(text))
            return created.id

        a = await child(edition_a.id, 0, "O spleen sufoca a liberdade de Bentinho.")
        b = await child(
            edition_a.id, 1, "O destino e o ciúme e a liberdade misturan o spleen de Capitu."
        )
        c = await child(edition_b.id, 0, "O spleen guia a memória dos antigos.")
        await conn.execute("ANALYZE passages")

    return Corpus(
        work_a=work_a.id,
        work_b=work_b.id,
        edition_a=edition_a.id,
        edition_b=edition_b.id,
        a=a,
        b=b,
        c=c,
    )


def _service() -> RetrievalService:
    return RetrievalService(ConceptEmbeddingProvider(), FakeRerankerProvider())


def _ids(candidates: list[RankedCandidate] | tuple[RankedCandidate, ...]) -> list[UUID]:
    return [c.passage_id for c in candidates]


async def test_pipeline_preserves_all_stages_and_fuses_deterministically(
    db: Database,
) -> None:
    """AC-06: as listas lexical/vetorial/fundida/reranked ficam preservadas; RRF
    produz scores determinísticos sobre as duas listas."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
        )

    # Estágio lexical: só A contém "sufoca".
    assert [c.passage_id for c in result.lexical] == [corpus.a]
    # Estágio vetorial: A (cosseno 1.0), B (2/√6), C (1/2).
    assert _ids(result.vector)[:3] == [corpus.a, corpus.b, corpus.c]

    # RRF determinístico: A em rank 0 nas duas listas -> 2/61; B e C só na
    # vetorial -> 1/62 e 1/63 (constante k = 60).
    assert [c.passage_id for c in result.fused] == [corpus.a, corpus.b, corpus.c]
    assert result.fused[0].score == pytest.approx(2.0 / 61.0)
    assert result.fused[1].score == pytest.approx(1.0 / 62.0)
    assert result.fused[2].score == pytest.approx(1.0 / 63.0)

    # Todos os estágios presentes e distintos (AC-06).
    assert len(result.answer_run_candidates()) == len(result.lexical) + len(result.vector) + len(
        result.fused
    ) + len(result.reranked)

    # A política fica versionada (AC-15).
    assert result.policy_version_id is not None


async def test_reranker_changes_order_in_controlled_case(db: Database) -> None:
    """O reranker altera a ordem num caso controlado: B domina a fusão RRF
    (alta nos dois estágios) mas compartilha mais termos da consulta e é
    promovida acima de A no reranking."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
        )

    assert [c.passage_id for c in result.fused] == [corpus.a, corpus.b, corpus.c]
    assert [c.passage_id for c in result.reranked] == [corpus.b, corpus.a, corpus.c]
    assert result.reranked[0].score > result.reranked[1].score


async def test_excluded_work_never_reaches_reranker(db: Database) -> None:
    """AC-07: obra excluída não aparece em NINGUM estágio e seu texto nunca é
    enviado ao provider de reranking."""
    corpus = await _seed(db)
    reranker = FakeRerankerProvider()
    service = RetrievalService(ConceptEmbeddingProvider(), reranker)
    async with db.connection() as conn:
        result = await service.retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=EditionFilter(exclude_work_ids=frozenset({corpus.work_b})),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
        )

    all_ids = set(_ids(result.lexical)) | set(_ids(result.vector))
    all_ids |= set(_ids(result.fused)) | set(_ids(result.reranked))
    assert corpus.c not in all_ids

    assert reranker.calls, "o reranker deve ter sido chamado"
    query, documents = reranker.calls[-1]
    assert query == _SEMANTIC_QUERY
    assert corpus.texts[corpus.c] not in documents
    assert corpus.texts[corpus.a] in documents
    assert corpus.texts[corpus.b] in documents


async def test_reranker_failure_is_not_masked(db: Database) -> None:
    """Falha do reranker propagha fechada — nunca se devolve a lista fundida
    como se fora resultado reranked (checklist §9)."""
    await _seed(db)
    failing = FakeRerankerProvider(fail_with=[ModelTimeoutError()])
    service = RetrievalService(ConceptEmbeddingProvider(), failing)
    async with db.connection() as conn:
        with pytest.raises(ModelTimeoutError):
            await service.retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("sufoca",)),
                semantic_query=_SEMANTIC_QUERY,
                filters=None,
                policy=RetrievalPolicy.defaults(),
                depth=Depth.STANDARD,
            )


async def test_policy_version_is_registered_and_reusable(db: Database) -> None:
    """A mesma política registra a MESMA versão (idempotente); params permitem
    reproduzir os orçamentos (AC-15)."""
    await _seed(db)
    async with db.connection() as conn:
        first = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.BRIEF,
        )
        second = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
        )
        assert first.policy_version_id is not None
        assert second.policy_version_id == first.policy_version_id
        version = await VersionsRepository(conn).get(
            RetrievalPolicyVersion, first.policy_version_id
        )
        assert version is not None
        assert len(version.params["budgets"]) == 3


async def test_empty_candidates_returns_empty_reranked(db: Database) -> None:
    corpus = await _seed(db)
    async with db.connection() as conn:
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=EditionFilter(
                exclude_edition_ids=frozenset({corpus.edition_a, corpus.edition_b})
            ),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
        )
    assert result.lexical == ()
    assert result.vector == ()
    assert result.fused == ()
    assert result.reranked == ()
