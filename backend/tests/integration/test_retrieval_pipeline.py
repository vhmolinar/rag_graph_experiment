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
from psycopg import AsyncConnection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from model_doubles import ConceptEmbeddingProvider, FakeRerankerProvider, concept_embedding

from rag.application.search import RetrievalService
from rag.domain.enums import Depth, QueryStatus, RankingStage, SourceType
from rag.domain.errors import ModelTimeoutError
from rag.domain.indexing import IndexRun
from rag.domain.library import Edition, Passage, Work
from rag.domain.query import EditionFilter, LexicalQuery
from rag.domain.retrieval import RetrievalBudget, RetrievalPolicy
from rag.domain.runs import AnswerRun, RankedCandidate
from rag.domain.versions import (
    ChunkingVersion,
    EmbeddingVersion,
    ExtractionVersion,
    ModelEndpointVersion,
    RetrievalPolicyVersion,
    utcnow,
)
from rag.infrastructure.db import Database
from rag.infrastructure.repositories.editions import EditionsRepository
from rag.infrastructure.repositories.index_runs import IndexRunsRepository
from rag.infrastructure.repositories.passages import PassagesRepository
from rag.infrastructure.repositories.runs import AnswerRunsRepository
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


async def _create_run(conn: AsyncConnection) -> AnswerRun:
    return await AnswerRunsRepository(conn).create(
        AnswerRun(
            question_original="O que é liberdade?",
            question_anonymized="O que é liberdade?",
            explicit_filters=EditionFilter(),
            created_at=utcnow(),
        )
    )


def _ids(candidates: list[RankedCandidate] | tuple[RankedCandidate, ...]) -> list[UUID]:
    return [c.passage_id for c in candidates]


async def test_pipeline_preserves_all_stages_and_fuses_deterministically(
    db: Database,
) -> None:
    """AC-06: as listas lexical/vetorial/fundida/reranked ficam preservadas; RRF
    produz scores determinísticos sobre as duas listas; AnswerRun é persistido."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
        )

        # Valida persistência obrigatória no PostgreSQL
        reloaded = await AnswerRunsRepository(conn).get(run.id)
        assert reloaded is not None
        assert reloaded.status is QueryStatus.RUNNING
        assert reloaded.versions.retrieval_policy_version_id == result.policy_version_id
        assert reloaded.versions.embedding_version_id == result.embedding_version_id
        assert len(reloaded.candidates) == len(result.answer_run_candidates())

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
    assert result.run_id == run.id


async def test_reranker_changes_order_in_controlled_case(db: Database) -> None:
    """O reranker altera a ordem num caso controlado: B domina a fusão RRF
    (alta nos dois estágios) mas compartilha mais termos da consulta e é
    promovida acima de A no reranking."""
    corpus = await _seed(db)
    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
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
        run = await _create_run(conn)
        result = await service.retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=EditionFilter(exclude_work_ids=frozenset({corpus.work_b})),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
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
        run = await _create_run(conn)
        with pytest.raises(ModelTimeoutError):
            await service.retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("sufoca",)),
                semantic_query=_SEMANTIC_QUERY,
                filters=None,
                policy=RetrievalPolicy.defaults(),
                depth=Depth.STANDARD,
                run=run,
            )


async def test_policy_version_is_registered_and_reusable(db: Database) -> None:
    """A mesma política registra a MESMA versão (idempotente); params permitem
    reproduzir os orçamentos (AC-15)."""
    await _seed(db)
    async with db.connection() as conn:
        run1 = await _create_run(conn)
        first = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.BRIEF,
            run=run1,
        )
        run2 = await _create_run(conn)
        second = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run2,
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
        run = await _create_run(conn)
        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=EditionFilter(
                exclude_edition_ids=frozenset({corpus.edition_a, corpus.edition_b})
            ),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
        )
    assert result.lexical == ()
    assert result.vector == ()
    assert result.fused == ()
    assert result.reranked == ()


async def test_retrieval_persists_rankings_into_answer_run_and_reloads_from_db(
    db: Database,
) -> None:
    """T9-03 (AC-06): scores e posições de todos os 4 estágios são persistidos no
    PostgreSQL via AnswerRunsRepository e recarregados com sucesso."""
    await _seed(db)
    async with db.connection() as conn:
        run_repo = AnswerRunsRepository(conn)
        initial_run = await run_repo.create(
            AnswerRun(
                question_original="O que é liberdade?",
                question_anonymized="O que é liberdade?",
                explicit_filters=EditionFilter(),
                created_at=utcnow(),
            )
        )

        result = await _service().retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=initial_run,
        )

        # Recarrega do banco real para provar persistência
        reloaded = await run_repo.get(initial_run.id)
        assert reloaded is not None
        assert reloaded.status is QueryStatus.RUNNING
        assert reloaded.versions.retrieval_policy_version_id == result.policy_version_id
        assert reloaded.versions.embedding_version_id == result.embedding_version_id
        assert result.run_id == initial_run.id

        # Confere que os 4 estágios foram persistidos
        stages = {c.stage for c in reloaded.candidates}
        assert stages == {
            RankingStage.LEXICAL,
            RankingStage.VECTOR,
            RankingStage.FUSED,
            RankingStage.RERANKED,
        }

        # Confere que as tuplas de candidatos correspondem ao retornado
        expected_candidates = result.answer_run_candidates()
        assert len(reloaded.candidates) == len(expected_candidates)
        for actual, expected in zip(reloaded.candidates, expected_candidates, strict=True):
            assert actual.passage_id == expected.passage_id
            assert actual.stage == expected.stage
            assert actual.score == pytest.approx(expected.score)
            assert actual.rank == expected.rank


async def test_retrieval_pipeline_preserves_full_fused_list_when_fused_exceeds_rerank_top_n(
    db: Database,
) -> None:
    """T9-04: RetrievalResult.fused preserva TODOS os itens da fusão RRF,
    enquanto apenas os primeiros `rerank_top_n` são enviados ao reranker."""
    corpus = await _seed(db)
    reranker = FakeRerankerProvider()
    service = RetrievalService(ConceptEmbeddingProvider(), reranker)

    # Política onde rerank_top_n = 1, mas lexical/vector recuperam 3 candidatos
    custom_policy = RetrievalPolicy(
        budgets=(
            (
                Depth.BRIEF,
                RetrievalBudget(
                    depth=Depth.BRIEF,
                    lexical_top_k=10,
                    vector_top_k=10,
                    rrf_k=60.0,
                    rerank_top_n=1,
                ),
            ),
            (
                Depth.STANDARD,
                RetrievalBudget(
                    depth=Depth.STANDARD,
                    lexical_top_k=10,
                    vector_top_k=10,
                    rrf_k=60.0,
                    rerank_top_n=1,
                ),
            ),
            (
                Depth.DEEP,
                RetrievalBudget(
                    depth=Depth.DEEP,
                    lexical_top_k=10,
                    vector_top_k=10,
                    rrf_k=60.0,
                    rerank_top_n=1,
                ),
            ),
        )
    )

    async with db.connection() as conn:
        run = await _create_run(conn)
        result = await service.retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("sufoca",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=None,
            policy=custom_policy,
            depth=Depth.STANDARD,
            run=run,
        )

    # Fused tem todos os 3 candidatos
    assert len(result.fused) == 3
    assert [c.passage_id for c in result.fused] == [corpus.a, corpus.b, corpus.c]
    assert [c.rank for c in result.fused] == [0, 1, 2]

    # Reranked tem apenas 1 candidato (o top-1 do RRF)
    assert len(result.reranked) == 1
    assert result.reranked[0].passage_id == corpus.a

    # O reranker recebeu apenas 1 documento
    assert reranker.calls
    _, documents = reranker.calls[-1]
    assert len(documents) == 1
    assert corpus.texts[corpus.a] == documents[0]


async def test_retrieval_pipeline_excludes_inactive_index_run_from_vector_and_reranker(
    db: Database,
) -> None:
    """T9-01: passagens de index_run inativa não aparecem em vector, fused nem no reranker."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra Pipeline Reindexada"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Edição Pipeline",
                source_type=SourceType.PDF_TEXT,
                source_sha256="f" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-p-v1", created_at=utcnow())
        )
        emb_ver = await versions.get_or_create(
            EmbeddingVersion(
                label="concept-embedding",
                model_name="concept-embedding",
                dimensions=1024,
                created_at=utcnow(),
            )
        )
        ext_ver = await versions.get_or_create(
            ExtractionVersion(label="ext-p-v1", created_at=utcnow())
        )
        model_ver = await versions.get_or_create(
            ModelEndpointVersion(
                label="end-p-v1",
                endpoint_kind="embedding",
                provider="test",
                model_name="concept-embedding",
                created_at=utcnow(),
            )
        )
        runs_repo = IndexRunsRepository(conn)
        passages_repo = PassagesRepository(conn)

        # Execução inativa
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
                text="Texto obsoleto da execução inativa que fala de liberdade.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                index_run_id=old_run.id,
            ),
            embedding=concept_embedding(
                "Texto obsoleto da execução inativa que fala de liberdade."
            ),
        )

        # Execução ativa
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
                text="Texto ativo da execução atual que fala de liberdade.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver.id,
                index_run_id=active_run.id,
            ),
            embedding=concept_embedding("Texto ativo da execução atual que fala de liberdade."),
        )
        await conn.execute("ANALYZE passages")

        reranker = FakeRerankerProvider()
        service = RetrievalService(ConceptEmbeddingProvider(), reranker)
        run = await _create_run(conn)
        result = await service.retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("liberdade",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=EditionFilter(include_edition_ids=frozenset({edition.id})),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
        )

        all_ids = set(_ids(result.lexical)) | set(_ids(result.vector))
        all_ids |= set(_ids(result.fused)) | set(_ids(result.reranked))

        assert active_passage.id in all_ids
        assert old_passage.id not in all_ids, "Passagem inativa não pode aparecer em nenhum estágio"

        assert reranker.calls
        _, docs = reranker.calls[-1]
        assert "Texto obsoleto da execução inativa que fala de liberdade." not in docs


async def test_retrieval_pipeline_excludes_incompatible_embedding_version(
    db: Database,
) -> None:
    """T9-02: documentos com EmbeddingVersion incompatível não entram no estágio vetorial."""
    async with db.connection() as conn:
        work = await WorksRepository(conn).create(Work(canonical_title="Obra Incompatível"))
        edition = await EditionsRepository(conn).create(
            Edition(
                work_id=work.id,
                title="Edição Incompatível",
                source_type=SourceType.PDF_TEXT,
                source_sha256="7" * 64,
            )
        )
        versions = VersionsRepository(conn)
        chunking = await versions.get_or_create(
            ChunkingVersion(label="chunk-inc", created_at=utcnow())
        )
        emb_ver_other = await versions.get_or_create(
            EmbeddingVersion(
                label="other-model",
                model_name="other-model",
                dimensions=1024,
                created_at=utcnow(),
            )
        )
        ext_ver = await versions.get_or_create(
            ExtractionVersion(label="ext-inc", created_at=utcnow())
        )
        model_ver = await versions.get_or_create(
            ModelEndpointVersion(
                label="end-inc",
                endpoint_kind="embedding",
                provider="test",
                model_name="other-model",
                created_at=utcnow(),
            )
        )
        runs_repo = IndexRunsRepository(conn)
        active_run = await runs_repo.create(
            IndexRun(
                edition_id=edition.id,
                extraction_version_id=ext_ver.id,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver_other.id,
                model_endpoint_version_id=model_ver.id,
                is_active=True,
                created_at=utcnow(),
            )
        )
        passages_repo = PassagesRepository(conn)
        await passages_repo.create(
            Passage(
                edition_id=edition.id,
                ordinal=0,
                text="Texto sobre liberdade com embedding incompatível.",
                token_count=10,
                chunking_version_id=chunking.id,
                embedding_version_id=emb_ver_other.id,
                index_run_id=active_run.id,
            ),
            embedding=concept_embedding("Texto sobre liberdade com embedding incompatível."),
        )
        await conn.execute("ANALYZE passages")

        # RetrievalService com ConceptEmbeddingProvider (label="concept-embedding")
        service = RetrievalService(ConceptEmbeddingProvider(), FakeRerankerProvider())
        run = await _create_run(conn)
        result = await service.retrieve(
            conn,
            lexical_query=LexicalQuery(required_terms=("inexistente",)),
            semantic_query=_SEMANTIC_QUERY,
            filters=EditionFilter(include_edition_ids=frozenset({edition.id})),
            policy=RetrievalPolicy.defaults(),
            depth=Depth.STANDARD,
            run=run,
        )

        assert _ids(result.vector) == [], (
            "Passagem com embedding incompatível não deve retornar na busca vetorial"
        )
        assert result.fused == ()
        assert result.reranked == ()


async def test_retrieval_service_rejects_missing_run_against_db(db: Database) -> None:
    """R2-T9-02: RetrievalService.retrieve exige AnswerRun (rejeita None com TypeError)."""
    await _seed(db)
    async with db.connection() as conn:
        with pytest.raises(TypeError, match="AnswerRun"):
            await _service().retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("sufoca",)),
                semantic_query=_SEMANTIC_QUERY,
                run=None,  # type: ignore[arg-type]
            )


async def test_retrieval_service_rejects_provider_without_embedding_version_against_db(
    db: Database,
) -> None:
    """R2-T9-01: RetrievalService falha fechado se o provider não implementar embedding_version."""

    class ProviderWithoutVersion:
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return []

        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * 1024

    await _seed(db)
    service = RetrievalService(ProviderWithoutVersion(), FakeRerankerProvider())  # type: ignore[arg-type]
    async with db.connection() as conn:
        run = await _create_run(conn)
        with pytest.raises(TypeError, match="embedding_version"):
            await service.retrieve(
                conn,
                lexical_query=LexicalQuery(required_terms=("sufoca",)),
                semantic_query=_SEMANTIC_QUERY,
                run=run,
            )
